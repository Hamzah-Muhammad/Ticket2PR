r"""Ticket2PR - an agent that reads a backlog and opens PRs to resolve it.

Easiest way to run it: double-click run.bat (or `run.bat --dry-run` from a
terminal) - it uses the defaults in config.py.

Or run directly for full control:
    venv\Scripts\python main.py --target-repo C:\path\to\repo --dry-run
    venv\Scripts\python main.py --target-repo C:\path\to\repo
    venv\Scripts\python main.py --target-repo C:\path\to\repo --issue 3

Auth: uses your Claude Code login automatically (or ANTHROPIC_API_KEY - see
.env.example). Requires: `gh` CLI installed and authenticated
(`gh auth login`) with access to the target repo. The target repo must
already be a local git clone whose `origin` remote points at GitHub.

Defaults (target repo, label) live in config.py - edit that file instead of
retyping flags every time.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import anyio
from dotenv import load_dotenv

import config
from agent.runner import repo_info, run_task
from tasks.github_source import GitHubIssuesSource

load_dotenv()  # picks up ANTHROPIC_API_KEY from .env if present; no-op otherwise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target-repo", default=Path(config.DEFAULT_TARGET_REPO), type=Path, help=f"Local path to the git repo the agent should work in (default: {config.DEFAULT_TARGET_REPO}, see config.py)")
    parser.add_argument("--repo-slug", default=None, help="owner/repo for gh (defaults to the target repo's origin remote)")
    parser.add_argument("--label", default=config.DEFAULT_LABEL, help=f"Issue label to pick up (default: {config.DEFAULT_LABEL}, see config.py)")
    parser.add_argument("--issue", default=None, help="Only run a single issue number")
    parser.add_argument("--dry-run", action="store_true", help="Make the changes but don't commit/push/open a PR")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    repo_path: Path = args.target_repo.resolve()
    if not (repo_path / ".git").exists():
        raise SystemExit(
            f"{repo_path} is not a git repo. Clone the target repo there first "
            "(e.g. `gh repo clone Hamzah-Muhammad/ticket2pr-demo` next to this folder), "
            "set DEFAULT_TARGET_REPO in config.py, or pass --target-repo."
        )

    slug = args.repo_slug or repo_info(repo_path)[0]
    source = GitHubIssuesSource(repo=slug, label=args.label)

    tasks = [source.get_task(args.issue)] if args.issue else source.list_ready_tasks()
    if not tasks:
        print(f"No open '{args.label}'-labeled issues found on {slug}.")
        return

    failures: list[str] = []
    for task in tasks:
        print(f"\n=== #{task.id}: {task.title} ===")
        try:
            result = await run_task(repo_path, task, dry_run=args.dry_run)
        except Exception as exc:
            # One issue failing (git conflict, a gh hiccup, ...) shouldn't take
            # down the rest of the batch - log it and move on to the next task.
            print(f"FAILED: {exc}")
            failures.append(f"#{task.id}: {exc}")
            continue
        for line in result.log:
            print(line)
        if result.pr_url:
            print(f"PR: {result.pr_url}")

    if failures:
        print(f"\n{len(failures)} of {len(tasks)} issue(s) failed:")
        for f in failures:
            print(f"  - {f}")


if __name__ == "__main__":
    anyio.run(main)
