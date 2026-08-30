import json
import subprocess
from unittest.mock import patch

from tasks.github_source import GitHubIssuesSource


def _fake_run(stdout: str):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_list_ready_tasks_parses_gh_json() -> None:
    payload = json.dumps(
        [
            {
                "number": 3,
                "title": "Add input validation",
                "body": "validate x > 0",
                "url": "https://github.com/o/r/issues/3",
            },
            {
                "number": 5,
                "title": "Add .gitignore",
                "body": None,
                "url": "https://github.com/o/r/issues/5",
            },
        ]
    )
    source = GitHubIssuesSource(repo="o/r", label="agent-ready")

    with patch("subprocess.run", return_value=_fake_run(payload)) as mock_run:
        tasks = source.list_ready_tasks()

    assert [t.id for t in tasks] == ["3", "5"]
    assert tasks[0].title == "Add input validation"
    assert tasks[1].body == ""  # None body normalized to ""
    args = mock_run.call_args.args[0]
    assert args[:3] == ["gh", "issue", "list"]
    assert "--label" in args and "agent-ready" in args


def test_get_task_parses_single_issue() -> None:
    payload = json.dumps(
        {
            "number": 7,
            "title": "Fix bug",
            "body": "details",
            "url": "https://github.com/o/r/issues/7",
        }
    )
    source = GitHubIssuesSource(repo="o/r")

    with patch("subprocess.run", return_value=_fake_run(payload)):
        task = source.get_task("7")

    assert task.id == "7"
    assert task.url == "https://github.com/o/r/issues/7"
