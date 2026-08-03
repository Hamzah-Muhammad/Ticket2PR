import subprocess
from pathlib import Path
from unittest.mock import patch

from agent.runner import repo_info


def test_repo_info_parses_gh_output() -> None:
    payload = '{"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}}'
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr="")

    with patch("subprocess.run", return_value=fake) as mock_run:
        slug, base = repo_info(Path("."))

    assert slug == "o/r"
    assert base == "main"
    args = mock_run.call_args.args[0]
    assert args[:3] == ["gh", "repo", "view"]
