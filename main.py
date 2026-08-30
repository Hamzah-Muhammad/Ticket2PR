r"""Ticket2PR - an agent that reads a backlog and opens PRs to resolve it.

Easiest way to run it: double-click run.bat (or `run.bat --dry-run` from a
terminal) - it uses the defaults in config.py.

Or run directly for full control:
    venv\Scripts\python main.py --target-repo C:\path\to\repo --dry-run
    venv\Scripts\python main.py --target-repo C:\path\to\repo
    venv\Scripts\python main.py --target-repo C:\path\to\repo --issue 3
    venv\Scripts\python main.py --discard-changes   # after a dry run

Auth: uses your Claude Code login automatically (or ANTHROPIC_API_KEY - see
.env.example). Requires: `gh` CLI installed and authenticated
(`gh auth login`) with access to the target repo. The target repo must
already be a local git clone whose `origin` remote points at GitHub.
Both are checked at startup, with the fix printed if something is missing.

Defaults (target repo, label, model) live in config.py - edit that file
instead of retyping flags every time.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import anyio
from claude_agent_sdk import CLINotFoundError
from dotenv import load_dotenv

import config
from agent.runner import discard_changes, repo_info, run_task, working_tree_dirty
from tasks.github_source import GitHubIssuesSource

load_dotenv()  # picks up ANTHROPIC_API_KEY from .env if present; no-op otherwise

GH_INSTALL_URL = "https://cli.github.com/"
CLAUDE_CODE_URL = "https://code.claude.com/docs/en/agent-sdk/overview"


def configure_console() -> None:
    """The agent's output is free text we don't control. On a legacy Windows
    console (cp1252) one arrow or box-drawing character would otherwise crash
    the run after the agent has already done its work. Replace, don't raise."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--target-repo",
        default=Path(config.DEFAULT_TARGET_REPO),
        type=Path,
        help=(
            "Local path to the git repo the agent should work in "
            f"(default: {config.DEFAULT_TARGET_REPO}, see config.py)"
        ),
    )
    parser.add_argument(
        "--repo-slug",
        default=None,
        help="owner/repo for gh (defaults to the target repo's origin remote)",
    )
    parser.add_argument(
        "--label",
        default=config.DEFAULT_LABEL,
        help=f"Issue label to pick up (default: {config.DEFAULT_LABEL}, see config.py)",
    )
    parser.add_argument("--issue", default=None, help="Only run a single issue number")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Make the changes but don't commit/push/open a PR",
    )
    parser.add_argument(
        "--discard-changes",
        action="store_true",
        help=(
            "Throw away uncommitted changes in the target repo before starting "
            "(e.g. what a previous --dry-run left behind)"
        ),
    )
    return parser.parse_args(argv)


def check_github_access() -> None:
    """Fail early, with the fix printed, instead of a traceback from the first gh call."""
    if shutil.which("gh") is None:
        raise SystemExit(
            "GitHub CLI (gh) is not installed or not on PATH.\n"
            f"  Install it from {GH_INSTALL_URL}, then run:  gh auth login"
        )
    status = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if status.returncode != 0:
        detail = (status.stderr or status.stdout).strip()
        raise SystemExit(
            "GitHub CLI is installed but not logged in.\n"
            "  Run:  gh auth login   (then re-run Ticket2PR)\n"
            + (f"  gh said: {detail}" if detail else "")
        )


def check_target_repo(repo_path: Path, discard: bool) -> None:
    if not (repo_path / ".git").exists():
        raise SystemExit(
            f"{repo_path} is not a git repo. Clone the target repo there first "
            "(e.g. `gh repo clone Hamzah-Muhammad/ticket2pr-demo` next to this folder), "
            "set DEFAULT_TARGET_REPO in config.py, or pass --target-repo."
        )
    if working_tree_dirty(repo_path):
        if discard:
            discard_changes(repo_path)
            print(f"Discarded uncommitted changes in {repo_path}")
            return
        raise SystemExit(
            f"{repo_path} has uncommitted changes (left over from a previous --dry-run?).\n"
            "  Every task must start from a clean, freshly pulled base branch.\n"
            "  Re-run with --discard-changes to throw them away, or commit/stash them yourself."
        )


def describe_failure(exc: BaseException) -> str:
    """One readable line per failure. CalledProcessError's own str() hides stderr,
    which is the only useful part of a failed git/gh call."""
    if isinstance(exc, subprocess.CalledProcessError):
        detail = (exc.stderr or exc.stdout or "").strip()
        cmd = " ".join(str(a) for a in list(exc.cmd)[:3])
        return f"`{cmd}` exited {exc.returncode}" + (f": {detail}" if detail else "")
    if isinstance(exc, CLINotFoundError):
        return (
            "Claude Code CLI not found. The agent runs on Claude Code: install it "
            f"(see {CLAUDE_CODE_URL}) and log in, then re-run."
        )
    return str(exc) or exc.__class__.__name__


async def main() -> None:
    configure_console()
    args = parse_args()
    check_github_access()
    repo_path: Path = args.target_repo.resolve()
    check_target_repo(repo_path, args.discard_changes)

    try:
        slug = args.repo_slug or repo_info(repo_path)[0]
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Could not identify the GitHub repo behind {repo_path}: {describe_failure(exc)}\n"
            "  Its `origin` remote must point at GitHub, or pass --repo-slug owner/repo."
        ) from exc
    source = GitHubIssuesSource(repo=slug, label=args.label)

    tasks = [source.get_task(args.issue)] if args.issue else source.list_ready_tasks()
    if not tasks:
        print(
            f"No open '{args.label}'-labeled issues found on {slug}.\n"
            f"  Hand the agent work with:  gh issue create --repo {slug} "
            f'--label {args.label} --title "..." --body "..."'
        )
        return

    mode = "DRY RUN (no commit/push/PR)" if args.dry_run else "LIVE (will push and open PRs)"
    print(f"{len(tasks)} task(s) on {slug}  |  {mode}  |  working tree: {repo_path}")

    failures: list[str] = []
    opened: list[str] = []
    for task in tasks:
        print(f"\n=== #{task.id}: {task.title} ===")
        try:
            result = await run_task(repo_path, task, dry_run=args.dry_run, model=config.AGENT_MODEL)
        except Exception as exc:
            # One issue failing (git conflict, a gh hiccup, ...) shouldn't take
            # down the rest of the batch - log it and move on to the next task.
            print(f"FAILED: {describe_failure(exc)}")
            failures.append(f"#{task.id}: {describe_failure(exc)}")
            continue
        for line in result.log:
            print(line)
        if result.pr_url:
            print(f"PR: {result.pr_url}")
            opened.append(result.pr_url)

    print()
    if opened:
        print(f"{len(opened)} PR(s) ready for review:")
        for url in opened:
            print(f"  {url}")
    if args.dry_run:
        print(
            "Dry run: changes are sitting uncommitted in the target repo. "
            "Re-run without --dry-run (add --discard-changes to start clean) to open PRs."
        )
    if failures:
        print(f"{len(failures)} of {len(tasks)} issue(s) failed:")
        for f in failures:
            print(f"  - {f}")
        raise SystemExit(1)


if __name__ == "__main__":
    anyio.run(main)
