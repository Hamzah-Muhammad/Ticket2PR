from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Task:
    """A single unit of work pulled from a backlog (GitHub Issues, Jira, Linear, ...)."""

    id: str
    title: str
    body: str
    url: str


class TaskSource(Protocol):
    """Anything that can list ready-to-work tasks implements this.

    Swap in a JiraSource, LinearSource, etc. without touching the orchestrator -
    the demo ships GitHubIssuesSource because it needs zero extra credentials
    beyond the `gh` CLI auth most developers already have.
    """

    def list_ready_tasks(self) -> list[Task]: ...

    def get_task(self, task_id: str) -> Task: ...
