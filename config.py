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

# Local path to the repo the agent works on by default.
DEFAULT_TARGET_REPO = r"C:\Apps\ticket-to-pr-demo"

# Which issue label marks a task as ready for the agent to pick up.
DEFAULT_LABEL = "agent-ready"
