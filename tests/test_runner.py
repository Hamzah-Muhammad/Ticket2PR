"""The orchestration around the agent turn: branch, detect changes, dry-run
stops before commit, real runs push and open (or reuse) a PR. The agent turn
itself is faked - these tests cover the harness, which is the part that broke
in the live runs (see README > Demo)."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import anyio

from agent import runner
from agent.runner import RunResult, pr_body, repo_info, run_task, working_tree_dirty
from tasks.base import Task

TASK = Task(id="7", title="Fix divide", body="raise on zero", url="https://github.com/o/r/issues/7")


def _fake_git(calls: list, porcelain: str = " M utils.py\n"):
    def run(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
        calls.append(args)
        out = ""
        if args[:2] == ("status", "--porcelain"):
            out = porcelain
        elif args[:2] == ("diff", "--stat"):
            out = " utils.py | 1 +\n"
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=out, stderr="")

    return run


async def _fake_agent_turn(repo_path, task, model=None):
    return ["Looking at utils.py", "Added a ZeroDivisionError guard and a test."]


def _run(coro) -> RunResult:
    return anyio.run(lambda: coro)


def test_repo_info_parses_gh_output() -> None:
    payload = '{"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}}'
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr="")

    with patch("subprocess.run", return_value=fake) as mock_run:
        slug, base = repo_info(Path("."))

    assert slug == "o/r"
    assert base == "main"
    args = mock_run.call_args.args[0]
    assert args[:3] == ["gh", "repo", "view"]


def test_working_tree_dirty_reads_porcelain_status() -> None:
    with patch.object(runner, "_run_git", _fake_git([], porcelain="?? new.py\n")):
        assert working_tree_dirty(Path("."))
    with patch.object(runner, "_run_git", _fake_git([], porcelain="\n")):
        assert not working_tree_dirty(Path("."))


def test_dry_run_branches_from_fresh_base_and_never_commits() -> None:
    calls: list = []
    with (
        patch.object(runner, "_run_git", _fake_git(calls)),
        patch.object(runner, "repo_info", return_value=("o/r", "main")),
        patch.object(runner, "_run_agent_turn", _fake_agent_turn),
    ):
        result = _run(run_task(Path("."), TASK, dry_run=True))

    assert result.changed and result.pr_url is None
    assert calls[:3] == [
        ("checkout", "main"),
        ("pull", "--ff-only"),
        ("checkout", "-B", "agent/issue-7", "main"),
    ]
    assert not any(c[0] in ("add", "commit", "push") for c in calls)
    assert any("Dry run" in line for line in result.log)


def test_no_changes_means_no_commit_and_no_pr() -> None:
    calls: list = []
    with (
        patch.object(runner, "_run_git", _fake_git(calls, porcelain="")),
        patch.object(runner, "repo_info", return_value=("o/r", "main")),
        patch.object(runner, "_run_agent_turn", _fake_agent_turn),
    ):
        result = _run(run_task(Path("."), TASK, dry_run=False))

    assert not result.changed and result.pr_url is None
    assert not any(c[0] in ("add", "commit", "push") for c in calls)


def test_real_run_pushes_head_by_ref_and_opens_pr() -> None:
    calls: list = []
    created = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="https://github.com/o/r/pull/9\n", stderr=""
    )
    with (
        patch.object(runner, "_run_git", _fake_git(calls)),
        patch.object(runner, "repo_info", return_value=("o/r", "main")),
        patch.object(runner, "_run_agent_turn", _fake_agent_turn),
        patch.object(runner, "existing_pr_url", return_value=None),
        patch.object(runner.subprocess, "run", return_value=created) as mock_run,
    ):
        result = _run(run_task(Path("."), TASK, dry_run=False))

    assert result.pr_url == "https://github.com/o/r/pull/9"
    assert ("push", "-u", "origin", "HEAD:agent/issue-7", "--force-with-lease") in calls
    pr_args = mock_run.call_args.args[0]
    assert pr_args[:3] == ["gh", "pr", "create"]
    body = pr_args[pr_args.index("--body") + 1]
    assert "Added a ZeroDivisionError guard" in body and "Closes #7" in body


def test_rerun_reuses_an_open_pr_instead_of_failing() -> None:
    calls: list = []
    with (
        patch.object(runner, "_run_git", _fake_git(calls)),
        patch.object(runner, "repo_info", return_value=("o/r", "main")),
        patch.object(runner, "_run_agent_turn", _fake_agent_turn),
        patch.object(runner, "existing_pr_url", return_value="https://github.com/o/r/pull/9"),
        patch.object(runner.subprocess, "run") as mock_run,
    ):
        result = _run(run_task(Path("."), TASK, dry_run=False))

    assert result.pr_url == "https://github.com/o/r/pull/9"
    assert any("already open" in line for line in result.log)
    mock_run.assert_not_called()  # no `gh pr create`


def test_pr_body_uses_the_agents_last_summary_line() -> None:
    body = pr_body(TASK, ["thinking...", "  ", "Final: guarded divide() and added a test."])
    assert body.startswith("Automated PR generated by Ticket2PR for #7")
    assert "## What the agent did" in body
    assert "Final: guarded divide()" in body
    assert body.endswith("Closes #7")


def test_pr_body_without_summary_still_closes_the_issue() -> None:
    body = pr_body(TASK, [])
    assert "What the agent did" not in body
    assert body.endswith("Closes #7")
