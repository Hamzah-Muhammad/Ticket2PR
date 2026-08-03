# TicketToPR

An autonomous coding agent, built on the [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview), that reads a backlog of tickets and opens real pull requests to resolve them.

Point it at a git repo and a labeled issue queue; it checks out a branch per issue, lets Claude implement and test the fix inside a sandboxed working tree, and — if the change is real — commits, pushes, and opens a PR for a human to review. It never merges anything itself.

## Why this exists

This is a demo of how to give an LLM agent real write access to a codebase *safely*. The interesting engineering here isn't "call the model" — it's the boundary around it:

- **The agent never touches git or GitHub.** Its tool surface is `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash` — and a `PreToolUse` hook (`agent/guardrails.py`) blocks every `git` and `gh` invocation the model might attempt through Bash. Branch creation, commit, push, and PR creation are deterministic Python in `agent/runner.py`, not model output. Agents are good at fuzzy judgment (what code to write); plain code is better at repeatable, auditable steps (the git lifecycle).
- **Every change lands as a PR, never a commit to `main`.** A human reviews before anything merges.
- **The task source is pluggable.** `tasks/base.py` defines a `TaskSource` protocol; `tasks/github_source.py` is the one shipped implementation (reads GitHub Issues via `gh`, so anyone can clone this and run it with zero extra credentials beyond their existing `gh auth login`). The same interface could back a `JiraSource` or `LinearSource` without touching the orchestrator.
- **Turns are capped** (`max_turns=30`) and **dry-run is the recommended first run** — see the change before anything is pushed.

## Architecture

```mermaid
flowchart LR
    A[TaskSource<br/>GitHubIssuesSource] -->|Task list| B[Orchestrator<br/>agent/runner.py]
    B -->|git checkout -B agent/issue-N| C[(sandboxed<br/>target repo)]
    B -->|one query() turn,<br/>cwd-scoped, Bash guardrail hook| D[Claude Agent SDK]
    D -->|Read / Write / Edit / Bash| C
    B -->|git diff check| C
    B -->|commit + push| E[GitHub branch]
    B -->|gh pr create| F[Pull Request]
```

## How it works

```
GitHub Issues (labeled "agent-ready")
        |
        v
tasks/github_source.py  ->  reads issues via `gh issue list`
        |
        v
agent/runner.py  ->  for each issue:
   1. git checkout -B agent/issue-N  (fresh branch off main)
   2. one Claude Agent SDK turn, working directory locked to your repo
      - can Read/Write/Edit files, run Bash (tests, linters, etc.)
      - cannot run git or gh at all - blocked by a hook (agent/guardrails.py)
   3. if real changes exist: commit -> push -> gh pr create
        |
        v
   A real PR, for you to review and merge yourself
```

The agent never merges anything or touches `main` directly - every result is a PR sitting there for human review.

## Feeding it tasks

Tasks are GitHub issues with a label (`agent-ready` by default). To give it work on any repo:

1. Open an issue on that repo describing the change, as specifically as you'd write it for a junior engineer - e.g. "`divide()` should raise `ValueError` on division by zero, add a test for it."
2. Label it `agent-ready` (`gh issue create --label agent-ready ...`, or add the label in the GitHub UI).

That's it - no special format required, the issue title + body become the agent's prompt directly.

## Setup

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
gh auth login          # if you haven't already
```

Auth for the model rides your existing Claude Code login — no API key needed (falls back to `ANTHROPIC_API_KEY` if set).

## Usage

```
# See what it would do, without pushing or opening a PR (recommended first run)
venv\Scripts\python main.py --target-repo C:\path\to\local\clone --dry-run

# Run for real against every open issue labeled "agent-ready"
venv\Scripts\python main.py --target-repo C:\path\to\local\clone

# Run a single issue
venv\Scripts\python main.py --target-repo C:\path\to\local\clone --issue 3

# Use a different label
venv\Scripts\python main.py --target-repo C:\path\to\local\clone --label ready-for-agent
```

Flags:

| Flag | Meaning |
| --- | --- |
| `--target-repo` | Path to a local clone you already have, with `origin` pointing at GitHub and push access |
| `--dry-run` | Make the code changes but stop before commit/push/PR, so you can inspect the diff first |
| `--issue N` | Only process issue `N`, instead of every open `agent-ready` issue |
| `--label` | Which issue label to treat as the task queue (default: `agent-ready`) |
| `--repo-slug` | Override the `owner/repo` used for `gh` calls (defaults to the target repo's `origin` remote) |

No API key needed - auth rides your existing Claude Code login (falls back to `ANTHROPIC_API_KEY` if set). You do need `gh auth login` done once.

## Demo

Run live against [`ticket-to-pr-demo`](https://github.com/Hamzah-Muhammad/ticket-to-pr-demo), a small seeded repo — three `agent-ready` issues, three real PRs, agent-authored end to end:

- [PR #10 — Add a LICENSE file](https://github.com/Hamzah-Muhammad/ticket-to-pr-demo/pull/10)
- [PR #11 — `first_n()` returns one fewer item than requested](https://github.com/Hamzah-Muhammad/ticket-to-pr-demo/pull/11)
- [PR #12 — `divide()` should raise a clear error on division by zero](https://github.com/Hamzah-Muhammad/ticket-to-pr-demo/pull/12)

Each diff touches only what its issue asked for — notably, PR #12's agent turn noticed the *unrelated* pre-existing `test_first_n` failure (issue #11's bug, not yet merged) and correctly left it alone rather than fixing something out of scope.

**A real bug the live run surfaced:** the first live pass produced PRs with diffs stacked on top of each other (issue B's PR contained issue A's changes too) because `agent/runner.py` created each new branch from whatever `HEAD` happened to be after the previous task, instead of from a freshly-updated `main`. A second bug then showed up once that was fixed: mid-turn, `HEAD` can drift off the branch the harness checked out (observed moving to an auxiliary branch during a Claude Code session) — pushing by local branch name silently stranded the commit on the wrong ref instead of erroring. The fix for both is in `agent/runner.py`: always rebase each new branch from a freshly-pulled `main`, and push with `git push origin HEAD:<branch>` rather than trusting whatever branch is locally checked out. Left this in the README on purpose — the interesting failure mode in agent tooling usually isn't the model, it's the surrounding automation's assumptions about state.

## Project layout

```
tasks/base.py            Task dataclass + TaskSource protocol
tasks/github_source.py   GitHubIssuesSource (gh CLI backed)
agent/guardrails.py      Bash allow/block logic + PreToolUse hook
agent/runner.py          Per-task orchestration: branch -> agent turn -> commit/push/PR
main.py                  CLI entrypoint
tests/                   pytest unit tests (guardrails + task-source parsing)
```

## Testing

```
venv\Scripts\python -m pytest
```

## License

MIT
