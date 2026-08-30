r"""Ticket2PR desktop app - what Ticket2PR.exe runs.

Double-click: opens the window (connect GitHub, pick a repo, click an issue,
get a PR). The same file also has two headless modes used to verify a build:

    Ticket2PR.exe --smoke
        prints the GitHub / Claude status the window would show, and exits
    Ticket2PR.exe --run-issue 2 --target-repo C:\path\to\clone --dry-run
        runs one issue exactly as a click in the window would, printing the log

The exe is built as a console app whose console is hidden when the window
opens: the Agent SDK and git/gh are child processes, and children of a hidden
console stay hidden, whereas a "windowed" build would flash a console for each.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def configure_console() -> None:
    """Headless modes print the agent's free text; on a cp1252 console one
    unencodable character must not crash a run that already did its work."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def hide_console() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="Ticket2PR", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--smoke", action="store_true", help="Print connection status and exit")
    parser.add_argument("--run-issue", metavar="N", help="Run one issue headlessly and exit")
    parser.add_argument("--target-repo", type=Path, help="Local clone to work in (headless modes)")
    parser.add_argument("--label", default=None, help="Issue label (headless modes)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Don't push or open a PR (headless run)"
    )
    return parser.parse_args(argv)


def smoke() -> int:
    import config
    from gui import engine

    gh = engine.github_status()
    claude = engine.claude_status()
    print(f"frozen: {getattr(sys, 'frozen', False)}")
    print(
        f"github: installed={gh.installed} logged_in={gh.logged_in} "
        f"user={gh.user or '-'} {gh.detail}"
    )
    print(f"claude: found={claude.found} {claude.detail} {claude.path}")
    print(f"default target repo: {config.DEFAULT_TARGET_REPO}")
    print(f"settings: {engine.settings_path()}")
    return 0 if gh.ok and claude.found else 1


def run_headless(args: argparse.Namespace) -> int:
    import config
    from gui import engine

    repo = (args.target_repo or Path(config.DEFAULT_TARGET_REPO)).resolve()
    slug = engine.detect_slug(repo)
    if not slug:
        print(f"{repo} is not a GitHub clone")
        return 2
    if engine.repo_is_dirty(repo):
        engine.reset_repo(repo)
        print("Discarded uncommitted changes in the target repo")
    task = engine.get_task(slug, args.run_issue, label=args.label or config.DEFAULT_LABEL)
    print(f"=== #{task.id}: {task.title} ===")
    result = engine.run_issue(
        repo, task, dry_run=args.dry_run, on_log=print, model=config.AGENT_MODEL
    )
    if result.pr_url:
        print(f"PR: {result.pr_url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_console()
    if args.smoke:
        return smoke()
    if args.run_issue:
        return run_headless(args)
    hide_console()
    from gui.app import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
