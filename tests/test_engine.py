"""The desktop app's engine: what the window does, minus the widgets."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import anyio

from agent import runner
from gui import engine
from tasks.base import Task
from tasks.github_source import GitHubIssuesSource


def _completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_one_time_code_is_parsed_from_gh_output() -> None:
    text = (
        "\n! First copy your one-time code: 7A3F-9B2C\n"
        "Open this URL to continue in your web browser: https://github.com/login/device\n"
    )
    assert engine.parse_one_time_code(text) == "7A3F-9B2C"
    assert engine.parse_one_time_code("Logged in as someone") is None


def test_settings_round_trip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TICKET2PR_SETTINGS", str(tmp_path / "nested" / "settings.json"))
    assert engine.Settings.load() == engine.Settings()  # no file yet: defaults
    engine.Settings(target_repo="C:/x", label="ready", assignee="bot", dry_run=False).save()
    loaded = engine.Settings.load()
    assert loaded.target_repo == "C:/x" and loaded.label == "ready"
    assert loaded.assignee == "bot" and loaded.dry_run is False


def test_settings_ignore_unknown_keys(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    monkeypatch.setenv("TICKET2PR_SETTINGS", str(path))
    path.write_text('{"label": "x", "future_field": 1}', encoding="utf-8")
    assert engine.Settings.load().label == "x"


def test_github_slug_parsing_handles_ssh_https_and_non_github() -> None:
    assert engine.parse_github_slug("git@github.com:o/r.git\n") == "o/r"
    assert engine.parse_github_slug("https://github.com/o/r") == "o/r"
    assert engine.parse_github_slug("https://github.com/o/r.git/") == "o/r"
    assert engine.parse_github_slug("https://gitlab.com/o/r.git") is None


def test_detect_slug_reads_origin(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    with patch.object(engine, "_run", return_value=_completed("git@github.com:o/r.git\n")):
        assert engine.detect_slug(tmp_path) == "o/r"
    assert engine.detect_slug(tmp_path / "missing") is None


def test_github_status_without_gh_installed() -> None:
    with patch.object(engine.shutil, "which", return_value=None):
        status = engine.github_status()
    assert status.installed is False and status.ok is False


def test_github_status_logged_in_reports_user() -> None:
    with (
        patch.object(engine.shutil, "which", return_value="gh"),
        patch.object(engine, "_run", return_value=_completed("octocat\n")),
    ):
        status = engine.github_status()
    assert status.ok and status.user == "octocat"


def test_github_status_logged_out_keeps_the_reason() -> None:
    with (
        patch.object(engine.shutil, "which", return_value="gh"),
        patch.object(engine, "_run", return_value=_completed("", 1, "gh: Not logged in")),
    ):
        status = engine.github_status()
    assert status.installed and not status.logged_in and "Not logged in" in status.detail


def test_login_with_token_pipes_the_token_to_gh() -> None:
    with patch.object(engine, "_run", return_value=_completed("")) as run:
        ok, _ = engine.login_with_token("ghp_abc")
    assert ok
    assert run.call_args.args[0][:3] == ["gh", "auth", "login"]
    assert run.call_args.kwargs["input"] == "ghp_abc\n"
    assert engine.login_with_token("   ") == (False, "Paste a token first.")


def test_list_repos_parses_one_slug_per_line() -> None:
    with patch.object(engine, "_run", return_value=_completed("o/a\no/b\n")):
        assert engine.list_repos() == ["o/a", "o/b"]


def test_assignee_filter_reaches_gh() -> None:
    source = GitHubIssuesSource(repo="o/r", label="agent-ready", assignee="bot")
    with patch("subprocess.run", return_value=_completed("[]")) as run:
        assert source.list_ready_tasks() == []
    args = run.call_args.args[0]
    assert "--assignee" in args and args[args.index("--assignee") + 1] == "bot"


def test_tasks_carry_labels_and_updated_at() -> None:
    payload = (
        '[{"number": 3, "title": "t", "body": "b", "url": "u", '
        '"labels": [{"name": "agent-ready"}, {"name": "bug"}], '
        '"updatedAt": "2026-08-29T10:00:00Z"}]'
    )
    with patch("subprocess.run", return_value=_completed(payload)):
        (task,) = GitHubIssuesSource(repo="o/r").list_ready_tasks()
    assert task.labels == ("agent-ready", "bug")
    assert task.updated_at.startswith("2026-08-29")


def test_run_task_streams_lines_through_on_log() -> None:
    seen: list[str] = []

    def fake_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
        out = " M a.py\n" if args[:2] == ("status", "--porcelain") else ""
        return _completed(out)

    async def fake_turn(repo_path, task, on_log=None, **_kwargs):
        if on_log:
            on_log("agent says hi")
        return ["agent says hi"]

    task = Task(id="1", title="t", body="", url="u")
    with (
        patch.object(runner, "_run_git", fake_git),
        patch.object(runner, "repo_info", return_value=("o/r", "main")),
        patch.object(runner, "_run_agent_turn", fake_turn),
    ):
        result = anyio.run(
            lambda: runner.run_task(Path("."), task, dry_run=True, on_log=seen.append)
        )
    assert result.changed
    assert "agent says hi" in seen
    assert any(line.startswith("Checking out") for line in seen)


def test_claude_cli_lookup_rejects_the_npm_cmd_shim(monkeypatch) -> None:
    monkeypatch.delenv("TICKET2PR_CLAUDE_CLI", raising=False)
    with (
        patch.object(engine, "_sdk_bundled_cli", return_value=None),
        patch.object(engine, "_native_install_cli", return_value=None),
        patch.object(engine.shutil, "which", return_value=r"C:\npm\claude.CMD"),
    ):
        assert engine.find_claude_cli() is None
        status = engine.claude_status()
    assert not status.found and "npm" in status.detail and "install.ps1" in status.detail


def test_claude_cli_lookup_order(monkeypatch, tmp_path) -> None:
    override = tmp_path / "claude.exe"
    override.write_bytes(b"")
    monkeypatch.setenv("TICKET2PR_CLAUDE_CLI", str(override))
    assert engine.find_claude_cli() == override
    monkeypatch.delenv("TICKET2PR_CLAUDE_CLI")
    native = tmp_path / "native" / engine._CLI_NAME
    native.parent.mkdir()
    native.write_bytes(b"")
    with (
        patch.object(engine, "_sdk_bundled_cli", return_value=None),
        patch.object(engine, "_native_install_cli", return_value=native),
    ):
        assert engine.find_claude_cli() == native
