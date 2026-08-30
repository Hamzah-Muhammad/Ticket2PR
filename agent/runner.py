"""Turns one Task into one PR.

Split of responsibility (deliberate): the *agent* only reads/writes/tests code
inside the sandboxed working tree. All git/GitHub lifecycle - branch, commit,
push, PR - is deterministic Python in this file, never a model-issued shell
command. Agents are good at fuzzy judgment (what code to write); plain code is
better at repeatable steps (the git dance) and is what you want to audit when
something goes wrong.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    TextBlock,
    query,
)

from agent.guardrails import bash_guardrail_hook, make_path_jail_hook
from tasks.base import Task

# The whole system prompt. Deliberately one paragraph: the issue title and body
# are the actual task, this only sets the job, the scope, and the one hard rule
# (never git/gh - the harness owns that, and the hooks enforce it anyway).
SYSTEM_PROMPT = """You are a software engineer resolving one GitHub issue in a \
local git repository. Implement the smallest correct change that resolves the \
issue. Only touch files relevant to the issue. If the repo has a test suite, \
run it with Bash and make sure the tests related to your change pass before \
you finish. If other tests were already failing before you changed anything, \
leave them alone and mention them in your summary: fixing unrelated bugs \
belongs in their own issue, not this one. Do not run `git` \
or `gh` commands yourself - committing, pushing, and opening the PR are \
handled outside your turn. When you are done, stop; do not ask questions. \
Your final message should be a short summary of what you changed and why, \
suitable for a pull request description."""

# Child processes must not pop console windows when run from the desktop app.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Hard cap on agent turns per issue, so a stuck agent stops rather than looping.
MAX_TURNS = 30

# How much of the agent's closing summary goes into the PR body.
MAX_PR_SUMMARY_CHARS = 2000


@dataclass
class RunResult:
    task: Task
    branch: str
    changed: bool
    pr_url: str | None
    log: list[str]


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        check=True,
        creationflags=_NO_WINDOW,
    )


def working_tree_dirty(repo_path: Path) -> bool:
    """True if the target repo has uncommitted or untracked changes - typically
    what a previous --dry-run left behind. Starting a task on top of them would
    silently fold them into the next PR."""
    return bool(_run_git(repo_path, "status", "--porcelain").stdout.strip())


def discard_changes(repo_path: Path) -> None:
    """Throw away every uncommitted change and untracked file in the target repo."""
    _run_git(repo_path, "reset", "--hard")
    _run_git(repo_path, "clean", "-fd")


async def _run_agent_turn(
    repo_path: Path,
    task: Task,
    model: str | None = None,
    on_log: Callable[[str], None] | None = None,
    cli_path: Path | None = None,
) -> list[str]:
    """Runs one sandboxed agent turn against the checked-out branch. Returns the
    agent's text output lines for logging and the PR body."""
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        cwd=str(repo_path),
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[bash_guardrail_hook]),
                HookMatcher(matcher="Write|Edit", hooks=[make_path_jail_hook(repo_path)]),
            ]
        },
        max_turns=MAX_TURNS,
        model=model,
        cli_path=str(cli_path) if cli_path else None,
    )
    prompt = f"Issue #{task.id}: {task.title}\n\n{task.body}".strip()

    lines: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    lines.append(block.text)
                    if on_log:
                        on_log(block.text)
    return lines


def repo_info(repo_path: Path) -> tuple[str, str]:
    """Returns (owner/repo slug, default branch name) for the target repo, in
    one `gh` call - shared by main.py (needs the slug) and run_task (needs both)."""
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(repo_path),
        creationflags=_NO_WINDOW,
    )
    data = json.loads(result.stdout)
    return data["nameWithOwner"], data["defaultBranchRef"]["name"]


def existing_pr_url(repo_path: Path, repo_slug: str, branch: str) -> str | None:
    """URL of an already-open PR from `branch`, if any. Re-running an issue whose
    PR is still open should update that PR (the push already did), not fail on
    `gh pr create`."""
    result = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo_slug,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(repo_path),
        creationflags=_NO_WINDOW,
    )
    prs = json.loads(result.stdout or "[]")
    return prs[0]["url"] if prs else None


def pr_body(task: Task, agent_lines: list[str]) -> str:
    """The agent's closing summary becomes the PR description, so a reviewer
    sees what it did and why without opening the diff first."""
    summary = ""
    for line in reversed(agent_lines):
        if line.strip():
            summary = line.strip()
            break
    if len(summary) > MAX_PR_SUMMARY_CHARS:
        summary = summary[: MAX_PR_SUMMARY_CHARS - 3] + "..."
    parts = [f"Automated PR generated by Ticket2PR for #{task.id} ({task.url})."]
    if summary:
        parts += ["", "## What the agent did", "", summary]
    parts += ["", f"Closes #{task.id}"]
    return "\n".join(parts)


async def run_task(
    repo_path: Path,
    task: Task,
    dry_run: bool = True,
    model: str | None = None,
    on_log: Callable[[str], None] | None = None,
    cli_path: Path | None = None,
) -> RunResult:
    """on_log, if given, receives every log line as it happens (the desktop app
    streams it); the same lines are also returned in RunResult.log."""
    branch = f"agent/issue-{task.id}"
    repo_slug, base = repo_info(repo_path)
    log: list[str] = []

    def note(line: str) -> None:
        log.append(line)
        if on_log:
            on_log(line)

    note(f"Checking out {branch} from {base}")

    # Every task branches from a freshly-updated base branch, never from
    # wherever HEAD happens to be left after a previous task - otherwise
    # sequential runs would stack each issue's diff on top of the last one's.
    _run_git(repo_path, "checkout", base)
    _run_git(repo_path, "pull", "--ff-only")
    _run_git(repo_path, "checkout", "-B", branch, base)

    note("Running agent turn")
    agent_lines = await _run_agent_turn(
        repo_path, task, model=model, on_log=on_log, cli_path=cli_path
    )
    log.extend(agent_lines)

    changed = working_tree_dirty(repo_path)

    if not changed:
        note("No changes produced - skipping commit/PR")
        return RunResult(task=task, branch=branch, changed=False, pr_url=None, log=log)

    if dry_run:
        diffstat = _run_git(repo_path, "diff", "--stat")
        note("Dry run - changes made but not committed:")
        note(diffstat.stdout)
        return RunResult(task=task, branch=branch, changed=True, pr_url=None, log=log)

    _run_git(repo_path, "add", "-A")
    _run_git(repo_path, "commit", "-m", f"Closes #{task.id}: {task.title}")
    # Push HEAD explicitly (not the local branch name): Claude Code's own
    # session checkpointing has been observed to switch HEAD to an auxiliary
    # branch mid-turn, which would silently strand our commit if we pushed
    # `branch` by name instead of by ref.
    _run_git(repo_path, "push", "-u", "origin", f"HEAD:{branch}", "--force-with-lease")

    already = existing_pr_url(repo_path, repo_slug, branch)
    if already:
        note(f"PR already open for {branch}, updated by the push: {already}")
        return RunResult(task=task, branch=branch, changed=True, pr_url=already, log=log)

    pr = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repo_slug,
            "--head",
            branch,
            "--title",
            f"{task.title} (closes #{task.id})",
            "--body",
            pr_body(task, agent_lines),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(repo_path),
        creationflags=_NO_WINDOW,
    )
    pr_url = pr.stdout.strip()
    note(f"Opened PR: {pr_url}")
    return RunResult(task=task, branch=branch, changed=True, pr_url=pr_url, log=log)
