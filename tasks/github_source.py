from __future__ import annotations

import json
import subprocess

from tasks.base import Task


class GitHubIssuesSource:
    """Reads open, labeled issues from a GitHub repo via the `gh` CLI.

    Requires `gh auth login` to already be done (no token handling here -
    we ride whatever auth the developer's `gh` already has, same pattern
    ClaudeAiAgents/hello_agent.py uses for Claude Code login).
    """

    def __init__(self, repo: str, label: str = "agent-ready"):
        self.repo = repo
        self.label = label

    def list_ready_tasks(self) -> list[Task]:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                self.repo,
                "--label",
                self.label,
                "--state",
                "open",
                "--json",
                "number,title,body,url",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return [self._to_task(item) for item in json.loads(result.stdout)]

    def get_task(self, task_id: str) -> Task:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                task_id,
                "--repo",
                self.repo,
                "--json",
                "number,title,body,url",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return self._to_task(json.loads(result.stdout))

    @staticmethod
    def _to_task(item: dict) -> Task:
        return Task(
            id=str(item["number"]),
            title=item["title"],
            body=item.get("body") or "",
            url=item["url"],
        )
