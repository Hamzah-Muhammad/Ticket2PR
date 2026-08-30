"""All the settings you're likely to want to change live here.

Auth is NOT configured in this file, on purpose - there is no GitHub token
or API key to paste in:

- GitHub auth rides your existing `gh` CLI login. Check it with
  `gh auth status`; set it up with `gh auth login` if needed.
- Claude auth rides your existing Claude Code login automatically. If you'd
  rather use API billing instead, copy `.env.example` to `.env` and set
  ANTHROPIC_API_KEY there - main.py loads it automatically.

Everything below is just a default; every value can still be overridden
per-run with a CLI flag (e.g. `--target-repo`, `--label`).
"""

from pathlib import Path

# Local path to the repo the agent works on by default. Ships pointed at the
# demo repo (https://github.com/Hamzah-Muhammad/ticket2pr-demo) cloned as a
# sibling folder of this one - point it at any local clone you have push
# access to, or override per run with --target-repo.
DEFAULT_TARGET_REPO = Path(__file__).resolve().parent.parent / "ticket2pr-demo"

# Which issue label marks a task as ready for the agent to pick up.
DEFAULT_LABEL = "agent-ready"

# Which Claude model the agent turn runs on. None = whatever your Claude Code
# login uses by default; set e.g. "claude-opus-5" to pin one.
AGENT_MODEL: str | None = None
