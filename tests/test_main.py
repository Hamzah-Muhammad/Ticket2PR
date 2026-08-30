"""Startup checks: a missing or logged-out gh, a dirty target repo, and failure
messages that actually say what went wrong."""

import subprocess
from unittest.mock import patch

import pytest
from claude_agent_sdk import CLINotFoundError

import main


def test_parse_args_defaults_to_config_and_live_mode() -> None:
    args = main.parse_args([])
    assert args.dry_run is False
    assert args.discard_changes is False
    assert args.label == main.config.DEFAULT_LABEL


def test_missing_gh_is_explained_not_tracebacked() -> None:
    with patch.object(main.shutil, "which", return_value=None):
        with pytest.raises(SystemExit, match="not installed"):
            main.check_github_access()


def test_logged_out_gh_points_at_gh_auth_login() -> None:
    logged_out = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="You are not logged into any GitHub hosts."
    )
    with (
        patch.object(main.shutil, "which", return_value="gh"),
        patch.object(main.subprocess, "run", return_value=logged_out),
    ):
        with pytest.raises(SystemExit, match="gh auth login") as exc:
            main.check_github_access()
    assert "not logged into" in str(exc.value)


def test_logged_in_gh_passes() -> None:
    ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="Logged in", stderr="")
    with (
        patch.object(main.shutil, "which", return_value="gh"),
        patch.object(main.subprocess, "run", return_value=ok),
    ):
        main.check_github_access()


def test_target_must_be_a_git_repo(tmp_path) -> None:
    with pytest.raises(SystemExit, match="not a git repo"):
        main.check_target_repo(tmp_path, discard=False)


def test_dirty_target_is_refused_unless_discarding(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    with patch.object(main, "working_tree_dirty", return_value=True):
        with pytest.raises(SystemExit, match="--discard-changes"):
            main.check_target_repo(tmp_path, discard=False)
        with patch.object(main, "discard_changes") as discard:
            main.check_target_repo(tmp_path, discard=True)
    discard.assert_called_once_with(tmp_path)


def test_failure_messages_include_stderr_and_the_claude_hint() -> None:
    err = subprocess.CalledProcessError(
        128, ["git", "-C", "x", "pull", "--ff-only"], stderr="fatal: not possible to fast-forward"
    )
    assert "not possible to fast-forward" in main.describe_failure(err)
    assert "exited 128" in main.describe_failure(err)
    assert "Claude Code" in main.describe_failure(CLINotFoundError("no cli"))


def test_console_survives_characters_the_code_page_lacks(capsys) -> None:
    main.configure_console()
    print("a → b")  # would raise UnicodeEncodeError on a cp1252 console without the fix
    assert "a" in capsys.readouterr().out
