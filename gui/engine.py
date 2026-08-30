"""Everything the desktop app does that is not drawing widgets.

Kept free of tkinter on purpose: it is unit-testable, it is what the headless
`--run-issue` / `--smoke` modes of the exe call, and the view stays a thin
layer over it. GitHub access is the `gh` CLI's own login (browser device flow
or a pasted token); this app never sees or stores a token itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import anyio

from agent.runner import RunResult, discard_changes, run_task, working_tree_dirty
from tasks.base import Task
from tasks.github_source import GitHubIssuesSource

# Child processes must not pop console windows in a desktop app.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEVICE_URL = "https://github.com/login/device"
GH_INSTALL_URL = "https://cli.github.com/"
CLAUDE_INSTALL_URL = "https://code.claude.com/docs/en/agent-sdk/overview"

_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def _run(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    input: str | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        input=input,
        timeout=timeout,
        creationflags=NO_WINDOW,
    )


# --------------------------------------------------------------------------
# Settings (last repo, label, assignee, dry-run) in the user's app-data folder
# --------------------------------------------------------------------------


def settings_path() -> Path:
    override = os.environ.get("TICKET2PR_SETTINGS")
    if override:
        return Path(override)
    base = Path(os.environ.get("APPDATA", Path.home()))
    return base / "Ticket2PR" / "settings.json"


@dataclass
class Settings:
    target_repo: str = ""
    label: str = "agent-ready"
    assignee: str = ""
    dry_run: bool = True

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(settings_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# GitHub: status, browser login, token login, repos
# --------------------------------------------------------------------------


@dataclass
class GitHubStatus:
    installed: bool
    logged_in: bool
    user: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.installed and self.logged_in


def github_status() -> GitHubStatus:
    if shutil.which("gh") is None:
        return GitHubStatus(False, False, detail="GitHub CLI (gh) is not installed")
    try:
        result = _run(["gh", "api", "user", "--jq", ".login"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitHubStatus(True, False, detail=str(exc))
    user = result.stdout.strip()
    if result.returncode == 0 and user:
        return GitHubStatus(True, True, user=user)
    return GitHubStatus(
        True, False, detail=(result.stderr or result.stdout).strip() or "not logged in"
    )


def parse_one_time_code(text: str) -> str | None:
    """`gh auth login --web` prints `! First copy your one-time code: XXXX-XXXX`."""
    match = _CODE_RE.search(text)
    return match.group(1) if match else None


class WebLogin:
    """Drives `gh auth login --web` in a background thread.

    Non-interactive gh prints the one-time code and the device URL and then
    polls GitHub until the user finishes in the browser; we surface the code
    as soon as it appears and report the outcome when gh exits.
    """

    def __init__(self) -> None:
        self.code: str | None = None
        self.output: list[str] = []
        self._proc: subprocess.Popen | None = None

    def start(self, on_code: Callable[[str], None], on_done: Callable[[bool, str], None]) -> None:
        threading.Thread(target=self._worker, args=(on_code, on_done), daemon=True).start()

    def _worker(self, on_code, on_done) -> None:
        try:
            self._proc = subprocess.Popen(
                [
                    "gh",
                    "auth",
                    "login",
                    "--hostname",
                    "github.com",
                    "--git-protocol",
                    "https",
                    "--web",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            on_done(False, f"Could not start gh: {exc}")
            return
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self.output.append(line)
            code = parse_one_time_code(line)
            if code and self.code is None:
                self.code = code
                on_code(code)
        rc = self._proc.wait()
        on_done(rc == 0, "".join(self.output).strip())

    def cancel(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()


def login_with_token(token: str) -> tuple[bool, str]:
    token = token.strip()
    if not token:
        return False, "Paste a token first."
    result = _run(
        ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
        input=token + "\n",
        timeout=60,
    )
    detail = (result.stderr or result.stdout).strip()
    return result.returncode == 0, detail or (
        "Logged in." if result.returncode == 0 else "Login failed."
    )


def list_repos(limit: int = 100) -> list[str]:
    result = _run(
        [
            "gh",
            "repo",
            "list",
            "--limit",
            str(limit),
            "--json",
            "nameWithOwner",
            "--jq",
            ".[].nameWithOwner",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "gh repo list failed")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def clone_repo(slug: str, parent: Path) -> Path:
    result = _run(["gh", "repo", "clone", slug], cwd=parent, timeout=600)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "gh repo clone failed")
    return Path(parent) / slug.split("/")[-1]


# --------------------------------------------------------------------------
# Target repo
# --------------------------------------------------------------------------


def is_git_repo(path: str | Path) -> bool:
    return bool(path) and (Path(path) / ".git").exists()


def parse_github_slug(remote_url: str) -> str | None:
    match = _GITHUB_REMOTE_RE.search(remote_url.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else None


def detect_slug(repo_path: str | Path) -> str | None:
    """owner/repo from the clone's `origin` remote, without needing gh."""
    if not is_git_repo(repo_path):
        return None
    result = _run(["git", "-C", str(repo_path), "remote", "get-url", "origin"], timeout=15)
    if result.returncode != 0:
        return None
    return parse_github_slug(result.stdout)


# --------------------------------------------------------------------------
# Claude
# --------------------------------------------------------------------------


@dataclass
class ClaudeStatus:
    found: bool
    detail: str
    path: str = ""


NATIVE_INSTALL_CMD = "irm https://claude.ai/install.ps1 | iex"
_CLI_NAME = "claude.exe" if sys.platform == "win32" else "claude"


def _sdk_bundled_cli() -> Path | None:
    try:
        import claude_agent_sdk

        candidate = Path(claude_agent_sdk.__file__).parent / "_bundled" / _CLI_NAME
    except Exception:  # SDK import problems are reported the same as "not found"
        return None
    return candidate if candidate.is_file() else None


def _native_install_cli() -> Path | None:
    candidate = Path.home() / ".local" / "bin" / _CLI_NAME
    return candidate if candidate.is_file() else None


def find_claude_cli() -> Path | None:
    """A native Claude Code executable the Agent SDK will accept.

    The npm install's `claude.cmd` shim is refused by the SDK (cmd.exe can be
    argument-injected), so the lookup is: explicit override, the SDK's own
    bundled copy (present when running from source), the native installer's
    location, then a real executable on PATH. The exe ships without the 253 MB
    bundled copy on purpose.
    """
    override = os.environ.get("TICKET2PR_CLAUDE_CLI")
    if override and Path(override).is_file():
        return Path(override)
    for finder in (_sdk_bundled_cli, _native_install_cli):
        found = finder()
        if found:
            return found
    hit = shutil.which(_CLI_NAME)
    if hit and not hit.lower().endswith((".cmd", ".bat")):
        return Path(hit)
    return None


def claude_status() -> ClaudeStatus:
    cli = find_claude_cli()
    if cli is None:
        if shutil.which("claude"):
            return ClaudeStatus(
                False,
                "Claude Code is installed via npm (claude.cmd), which the Agent SDK refuses; "
                f"install it natively: {NATIVE_INSTALL_CMD}",
            )
        return ClaudeStatus(
            False, f"Claude Code not found; install it ({NATIVE_INSTALL_CMD}) and log in"
        )
    try:
        result = _run([str(cli), "--version"], timeout=30)
        version = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        version = ""
    detail = f"Claude Code {version.split()[0]}" if version else "Claude Code"
    return ClaudeStatus(True, detail, path=str(cli))


# --------------------------------------------------------------------------
# Tasks and runs
# --------------------------------------------------------------------------


def list_tasks(slug: str, label: str, assignee: str = "") -> list[Task]:
    return GitHubIssuesSource(repo=slug, label=label, assignee=assignee or None).list_ready_tasks()


def get_task(slug: str, number: str, label: str = "agent-ready") -> Task:
    return GitHubIssuesSource(repo=slug, label=label).get_task(str(number))


def repo_is_dirty(repo_path: str | Path) -> bool:
    return working_tree_dirty(Path(repo_path))


def reset_repo(repo_path: str | Path) -> None:
    discard_changes(Path(repo_path))


def run_issue(
    repo_path: str | Path,
    task: Task,
    dry_run: bool,
    on_log: Callable[[str], None] | None = None,
    model: str | None = None,
) -> RunResult:
    """Blocking; call from a worker thread in the GUI."""
    cli = find_claude_cli()
    if cli is None:
        raise RuntimeError(claude_status().detail)
    return anyio.run(
        lambda: run_task(
            Path(repo_path), task, dry_run=dry_run, model=model, on_log=on_log, cli_path=cli
        )
    )
