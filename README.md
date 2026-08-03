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

## Setup

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
gh auth login          # if you haven't already
```

Auth for the model rides your existing Claude Code login — no API key needed (falls back to `ANTHROPIC_API_KEY` if set).

## Usage

```
# See what it would do, without pushing or opening a PR
venv\Scripts\python main.py --target-repo C:\path\to\local\clone --dry-run

# Run for real against every open issue labeled "agent-ready"
venv\Scripts\python main.py --target-repo C:\path\to\local\clone

# Run a single issue
venv\Scripts\python main.py --target-repo C:\path\to\local\clone --issue 3
```

The target repo must already be a local git clone with an `origin` remote pointing at GitHub, and you need push access.

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
