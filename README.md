# Ticket2PR

An autonomous coding agent, built on the [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview), that reads GitHub issues assigned to it and opens real pull requests to resolve them.

Assign it an issue on a repo it's watching; it checks out a branch, lets Claude implement and test the fix inside a sandboxed working tree, and — if the change is real — commits, pushes, and opens a PR for a human to review. It never merges anything itself.

**Stack:** a Python app that orchestrates two things it doesn't reimplement itself — GitHub (issues, branches, PRs, all via the `gh` CLI) and Claude (the actual coding, via Anthropic's Claude Agent SDK). Python's job is entirely the plumbing around those two: which issue to pick up, when to branch, when a change is real enough to commit, when to open the PR. Claude never touches git or GitHub directly — see [Why this exists](#why-this-exists) for how that boundary is enforced.

## Quick start

**1. Install:**

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
```

**2. Connect to GitHub** — the agent reads issues and opens PRs through the [`gh` CLI](https://cli.github.com/), riding whatever account you're already logged into. Nothing is stored in this repo:

```
gh auth login
```

Check it worked any time with `gh auth status`.

**3. Connect to Claude** — no setup needed if you already use Claude Code: the agent rides that same login automatically. If you'd rather run on API billing instead, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY` there (`.env` is gitignored, never committed).

**4. Run it** — either double-click **`run.bat`**, or from a terminal:

```
run.bat --dry-run
```

That runs against whatever repo is set in `config.py` (ships pointed at the demo repo below), lets Claude make the code changes, but stops before commit/push/PR — so you can see exactly what it would do first. Drop `--dry-run` once you're happy, and it pushes real branches and opens real PRs.

## Configuration — where the keys and settings live

There's no API key to paste in anywhere, on purpose — GitHub and Claude auth both ride your existing logins (Quick start, steps 2–3). The one thing you do edit directly is which repo/label to run against:

| Setting | Where it lives |
| --- | --- |
| Default target repo + label | **`config.py`** — edit `DEFAULT_TARGET_REPO` and `DEFAULT_LABEL` |
| GitHub auth | `gh auth login` / `gh auth status` — nothing stored in this repo |
| Claude auth | Your Claude Code login, automatically — or `ANTHROPIC_API_KEY` in `.env` for API billing |

Every `config.py` default can still be overridden per-run with a CLI flag (see [Running it](#running-it)).

## How it works

```
GitHub Issues assigned to the agent (agent-ready label)
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

Tasks are GitHub issues assigned to the agent. There's no bot account to hold a literal GitHub "Assignee," so a label (`agent-ready` by default) plays that role instead — it's how you hand the agent an issue and mark it ready to pick up. To give it work on any repo:

1. Open an issue describing the change, as specifically as you'd write it for a junior engineer - e.g. "`divide()` should raise `ValueError` on division by zero, add a test for it."
2. Assign it to the agent by adding the `agent-ready` label (`gh issue create --label agent-ready ...`, or add the label in the GitHub UI).

That's it - no special format required, the issue title + body become the agent's prompt directly.

## Running it

```
# Recommended first run: see the diff, nothing pushed
run.bat --dry-run

# Run for real against every open "agent-ready" issue on the config.py repo
run.bat

# Everything below also works with venv\Scripts\python main.py instead of run.bat
run.bat --target-repo C:\path\to\other\repo --dry-run
run.bat --issue 3
run.bat --label ready-for-agent
```

| Flag | Meaning |
| --- | --- |
| `--target-repo` | Path to a local clone with `origin` pointing at GitHub and push access (default: `config.py`) |
| `--dry-run` | Make the code changes but stop before commit/push/PR |
| `--issue N` | Only process issue `N`, instead of every open labeled issue |
| `--label` | Which issue label counts as the task queue (default: `config.py`) |
| `--repo-slug` | Override the `owner/repo` used for `gh` calls (defaults to the target repo's `origin` remote) |

## Why this exists

This is a demo of how to give an LLM agent real write access to a codebase *safely*. The interesting engineering here isn't "call the model" — it's the boundary around it:

- **The agent never touches git or GitHub.** Its tool surface is `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash` — and a `PreToolUse` hook (`agent/guardrails.py`) blocks every `git` and `gh` invocation the model might attempt through Bash. Branch creation, commit, push, and PR creation are deterministic Python in `agent/runner.py`, not model output. Agents are good at fuzzy judgment (what code to write); plain code is better at repeatable, auditable steps (the git lifecycle).
- **Write/Edit are path-jailed to the target repo.** `cwd` on `ClaudeAgentOptions` is only the working-directory *convention* the model reasons from — it does not restrict where `Write`/`Edit` can write. A second `PreToolUse` hook (`make_path_jail_hook` in `agent/guardrails.py`) resolves every `Write`/`Edit` target path and denies anything that lands outside the repo, or inside `.git/` directly (which would otherwise be a clean bypass of the git guardrail above). This isn't hypothetical — see the Demo section below.
- **Every change lands as a PR, never a commit to `main`.** A human reviews before anything merges.
- **The task source is pluggable.** `tasks/base.py` defines a `TaskSource` protocol; `tasks/github_source.py` is the one shipped implementation. The same interface could back a `JiraSource` or `LinearSource` without touching the orchestrator.
- **Turns are capped** (`max_turns=30`) and **dry-run is the recommended first run**.

## Known limitations

Being upfront about what this does *not* protect against:

- **The Bash guardrail is a text blocklist, not real sandboxing.** It pattern-matches the command string for `git`, `gh`, and a few other dangerous patterns. A differently-phrased command that reaches equivalent behavior without those literal words (e.g. a Python git library) would not be caught by it. The Claude Agent SDK does support real OS-level command sandboxing (`ClaudeAgentOptions.sandbox`), but it's explicitly macOS/Linux-only - not available on the Windows machine this was built and demoed on.
- **Issue bodies are untrusted input fed straight into the prompt**, and nothing here restricts the agent's outbound network access (only piping-into-shell is blocked, not general `curl`/`wget`/etc. calls). On a repo where the public can open issues, a malicious issue body is a real prompt-injection surface. This project assumes trusted issue authors - don't point it at a repo where anyone can file issues without review.
- Both guardrails **fail closed** (deny on anything unexpected) but are still two specific hooks, not a formal proof of containment. Treat them as raising the bar, not as a guarantee.

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

## Demo

Run live against [`ticket2pr-demo`](https://github.com/Hamzah-Muhammad/ticket2pr-demo), a small seeded repo — three `agent-ready` issues, three real PRs, agent-authored end to end:

- [PR #10 — Add a LICENSE file](https://github.com/Hamzah-Muhammad/ticket2pr-demo/pull/10)
- [PR #11 — `first_n()` returns one fewer item than requested](https://github.com/Hamzah-Muhammad/ticket2pr-demo/pull/11)
- [PR #12 — `divide()` should raise a clear error on division by zero](https://github.com/Hamzah-Muhammad/ticket2pr-demo/pull/12)

Each diff touches only what its issue asked for — notably, PR #12's agent turn noticed the *unrelated* pre-existing `test_first_n` failure (issue #11's bug, not yet merged) and correctly left it alone rather than fixing something out of scope.

**Three real bugs the live runs surfaced** — left in on purpose, since the interesting failure mode in agent tooling usually isn't the model, it's the surrounding automation's assumptions about state:

1. The first live pass produced PRs with diffs stacked on top of each other (issue B's PR contained issue A's changes too), because `agent/runner.py` created each new branch from whatever `HEAD` happened to be after the previous task, instead of from a freshly-updated `main`. Fixed by always rebasing each new branch from a freshly-pulled `main`.
2. Once that was fixed, mid-turn `HEAD` was observed drifting off the branch the harness checked out (onto an auxiliary branch during a Claude Code session) — pushing by local branch name silently stranded the commit on the wrong ref instead of erroring. Fixed by pushing `git push origin HEAD:<branch>` explicitly rather than trusting whatever branch is locally checked out.
3. During the audit that added the Write/Edit path-jail hook (see "Why this exists" above), re-running the LICENSE issue live reproduced the exact bug that hook is designed to catch: the agent's first attempt wrote the file to the parent of the repo directory instead of inside it. The path-jail hook denied that write, the agent read the denial and self-corrected to the right path in the same turn, and the file ended up in the right place - confirmed by checking both locations on disk afterward.

## Project layout

```
config.py                 Edit this: default target repo + label
run.bat                    Double-click to run with config.py defaults
tasks/base.py              Task dataclass + TaskSource protocol
tasks/github_source.py     GitHubIssuesSource (gh CLI backed)
agent/guardrails.py        Bash allow/block logic + Write/Edit path-jail, both as PreToolUse hooks
agent/runner.py            Per-task orchestration: branch -> agent turn -> commit/push/PR
main.py                    CLI entrypoint (what run.bat calls); isolates per-task failures
tests/                     pytest unit tests (guardrails, path-jail, task-source parsing)
```

## Testing

```
venv\Scripts\python -m pytest
```

## License

MIT
