r"""TicketToPR - an agent that reads a backlog and opens PRs to resolve it.

Run:
    venv\Scripts\python main.py --target-repo C:\path\to\repo --dry-run
    venv\Scripts\python main.py --target-repo C:\path\to\repo
    venv\Scripts\python main.py --target-repo C:\path\to\repo --issue 3

Auth: uses your Claude Code login automatically (or ANTHROPIC_API_KEY).
Requires: `gh` CLI installed and authenticated (`gh auth login`) with access
to the target repo. The target repo must already be a local git clone whose
`origin` remote points at GitHub.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import anyio

from agent.runner import run_task
from tasks.github_source import GitHubIssuesSource


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target-repo", required=True, type=Path, help="Local path to the git repo the agent should work in")
    parser.add_argument("--repo-slug", default=None, help="owner/repo for gh (defaults to the target repo's origin remote)")
    parser.add_argument("--label", default="agent-ready", help="Issue label to pick up (default: agent-ready)")
    parser.add_argument("--issue", default=None, help="Only run a single issue number")
    parser.add_argument("--dry-run", action="store_true", help="Make the changes but don't commit/push/open a PR")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    repo_path: Path = args.target_repo.resolve()
    if not (repo_path / ".git").exists():
        raise SystemExit(f"{repo_path} is not a git repo — clone it locally first")

    slug = args.repo_slug or _infer_slug(repo_path)
    source = GitHubIssuesSource(repo=slug, label=args.label)

    tasks = [source.get_task(args.issue)] if args.issue else source.list_ready_tasks()
    if not tasks:
        print(f"No open '{args.label}'-labeled issues found on {slug}.")
        return

    for task in tasks:
        print(f"\n=== #{task.id}: {task.title} ===")
        result = await run_task(repo_path, task, dry_run=args.dry_run)
        for line in result.log:
            print(line)
        if result.pr_url:
            print(f"PR: {result.pr_url}")


def _infer_slug(repo_path: Path) -> str:
    import subprocess
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True, text=True, check=True, cwd=str(repo_path),
    )
    return result.stdout.strip()


if __name__ == "__main__":
    anyio.run(main)
