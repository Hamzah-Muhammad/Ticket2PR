"""Ticket2PR must never commit or push to the branch it opens PRs against.

That property is structural (the harness has no merge code, and the branch
name is built from GitHub's own issue number), but structure can be refactored
away, so it is also asserted. The last test runs real git against a real local
"origin" - no network, no model - and reproduces the failure the assertions
exist for: HEAD moving off the task branch during the agent turn.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest

from agent import runner
from agent.runner import SafetyRefusal, assert_head_is_task_branch, assert_writable_branch
from tasks.base import Task

TASK = Task(id="7", title="Fix divide", body="raise on zero", url="https://github.com/o/r/issues/7")


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


# ------------------------------------------------------------- assertions --


def test_the_default_branch_is_refused_as_a_target() -> None:
    """Even a well-formed task branch is refused if it IS the branch we target."""
    with pytest.raises(SafetyRefusal, match="default branch"):
        assert_writable_branch("agent/issue-7", "agent/issue-7")
    with pytest.raises(SafetyRefusal, match="default branch"):
        assert_writable_branch("agent/issue-7", "Agent/Issue-7")  # case does not rescue it


@pytest.mark.parametrize("branch", ["main", "master", "MAIN", "Master"])
def test_main_and_master_are_refused_whatever_the_default_is(branch: str) -> None:
    with pytest.raises(SafetyRefusal):
        assert_writable_branch(branch, "develop")


def test_anything_that_is_not_a_task_branch_is_refused() -> None:
    for branch in ("release/1.0", "agent-issue-7", "", "agent/issue"):
        with pytest.raises(SafetyRefusal, match="only writes to"):
            assert_writable_branch(branch, "main")


def test_a_real_task_branch_is_allowed() -> None:
    assert assert_writable_branch("agent/issue-7", "main") is None
    assert assert_writable_branch("agent/issue-7", "develop") is None


def test_commit_is_refused_when_head_moved(tmp_path) -> None:
    with patch.object(runner, "_current_branch", return_value="main"):
        with pytest.raises(SafetyRefusal, match="HEAD is on 'main'"):
            assert_head_is_task_branch(tmp_path, "agent/issue-7")
    with patch.object(runner, "_current_branch", return_value="HEAD"):  # detached
        with pytest.raises(SafetyRefusal):
            assert_head_is_task_branch(tmp_path, "agent/issue-7")
    with patch.object(runner, "_current_branch", return_value="agent/issue-7"):
        assert assert_head_is_task_branch(tmp_path, "agent/issue-7") is None


def test_run_task_refuses_before_spending_a_model_turn() -> None:
    """The branch check runs before the agent turn, so a bad target costs nothing."""
    turns = []

    async def fake_turn(*a, **k):
        turns.append(1)
        return []

    with (
        patch.object(runner, "repo_info", return_value=("o/r", "agent/issue-7")),
        patch.object(runner, "_run_git") as git,
        patch.object(runner, "_run_agent_turn", fake_turn),
    ):
        with pytest.raises(SafetyRefusal):
            anyio.run(lambda: runner.run_task(Path("."), TASK, dry_run=False))
    assert turns == []  # no model turn
    git.assert_not_called()  # and no git either


# --------------------------------------------------- real git, real origin --


@pytest.fixture
def repo_with_origin(tmp_path):
    """A clone with a local bare `origin`, on `main`, one commit - the same
    shape run_task expects, without touching the network."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "checkout", "-q", "-B", "main")
    (work / "utils.py").write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "seed")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "-u", "origin", "main")
    return work, origin


def _gh_only(fake_url: str = "https://github.com/o/r/pull/9"):
    """Intercept `gh` while letting every real git command through."""
    real_run = subprocess.run

    def dispatch(args, **kwargs):
        if args and args[0] == "gh":
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=fake_url, stderr="")
        return real_run(args, **kwargs)

    return dispatch


def test_real_run_commits_only_to_the_task_branch(repo_with_origin) -> None:
    work, origin = repo_with_origin

    async def fake_turn(repo_path, task, **_kwargs):
        (Path(repo_path) / "utils.py").write_text(
            "def divide(a, b):\n"
            "    if b == 0:\n"
            "        raise ValueError('cannot divide by zero')\n"
            "    return a / b\n",
            encoding="utf-8",
        )
        return ["Guarded divide()."]

    with (
        patch.object(runner, "repo_info", return_value=("o/r", "main")),
        patch.object(runner, "_run_agent_turn", fake_turn),
        patch.object(runner, "existing_pr_url", return_value=None),
        patch.object(runner.subprocess, "run", _gh_only()),
    ):
        result = anyio.run(lambda: runner.run_task(work, TASK, dry_run=False))

    assert result.pr_url == "https://github.com/o/r/pull/9"
    # The work is on the task branch, in origin, and main is untouched everywhere.
    remote_branches = subprocess.run(
        ["git", "-C", str(origin), "branch", "--format=%(refname:short)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "agent/issue-7" in remote_branches
    for ref in (f"{origin}", str(work)):
        subjects = subprocess.run(
            ["git", "-C", ref, "log", "main", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\n")
        assert subjects[0] == "seed", f"main moved in {ref}: {subjects}"


def test_a_turn_that_moves_head_back_to_main_cannot_commit_there(repo_with_origin) -> None:
    """The documented failure: Claude Code checkpointing moves HEAD mid-turn.
    Without the assertion, `git add -A && git commit` lands on local main."""
    work, origin = repo_with_origin

    async def drifting_turn(repo_path, task, **_kwargs):
        (Path(repo_path) / "utils.py").write_text("changed by the agent\n", encoding="utf-8")
        _git(repo_path, "checkout", "-q", "main")  # HEAD drifts, changes carried along
        return ["done"]

    with (
        patch.object(runner, "repo_info", return_value=("o/r", "main")),
        patch.object(runner, "_run_agent_turn", drifting_turn),
        patch.object(runner, "existing_pr_url", return_value=None),
        patch.object(runner.subprocess, "run", _gh_only()),
    ):
        with pytest.raises(SafetyRefusal, match="HEAD is on 'main'"):
            anyio.run(lambda: runner.run_task(work, TASK, dry_run=False))

    # Nothing committed anywhere, nothing pushed, the work is still on disk.
    for ref in (str(origin), str(work)):
        subjects = subprocess.run(
            ["git", "-C", ref, "log", "main", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\n")
        assert subjects[0] == "seed", f"main moved in {ref}: {subjects}"
    assert (work / "utils.py").read_text(encoding="utf-8") == "changed by the agent\n"
    assert runner.working_tree_dirty(work)
