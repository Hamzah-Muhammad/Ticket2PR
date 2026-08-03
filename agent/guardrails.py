"""Safety rails for the agent's Bash and Write/Edit access.

Two independent guardrails, both wired as PreToolUse hooks (see
claude_agent_sdk hooks) because PreToolUse fires for every matching tool call
regardless of allowed_tools — unlike can_use_tool, which is skipped for tools
already auto-allowed:

1. `blocked_reason()` / `bash_guardrail_hook()` — blocks git/gh and other
   dangerous shell commands. This is a text blocklist, not real sandboxing:
   see the README's "Known limitations" section for what that does and
   doesn't cover.
2. `path_jail_reason()` / `make_path_jail_hook()` — blocks Write/Edit from
   touching anything outside the target repo, and specifically blocks direct
   writes under `.git/`. This exists because `cwd` on ClaudeAgentOptions is
   only the working-directory *convention* the model reasons from, not a
   filesystem jail — nothing else stops an absolute or `..`-traversal path
   from resolving outside the repo, and a direct write to `.git/refs/...`
   would be a clean bypass of guardrail #1 (it never touches the `git`
   binary at all).
"""

from __future__ import annotations

import re
from pathlib import Path

from claude_agent_sdk import HookContext, HookInput, HookJSONOutput

# Substring/regex patterns that must never run inside the sandboxed target repo.
#
# Notably: ALL git commands are blocked. The agent's job is only to write and
# test code; branch creation, commit, push, and PR creation are done by
# deterministic harness code in runner.py, never by model-issued shell
# commands. This is the load-bearing safety property of the whole project —
# an errant or manipulated agent turn cannot push/merge/force anything, because
# it has no path to `git` at all.
_BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"\bgit\b", "git operations are performed by the harness, not the agent"),
    (r"\brm\s+-rf\b", "recursive force-delete is not allowed"),
    (r"\bsudo\b", "privilege escalation is not allowed"),
    (r"curl[^\n|]*\|\s*(sh|bash)\b", "piping a remote script into a shell is not allowed"),
    (r"wget[^\n|]*\|\s*(sh|bash)\b", "piping a remote script into a shell is not allowed"),
    (r"\bgh\b", "GitHub CLI operations are performed by the harness, not the agent"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb pattern is not allowed"),
]


def blocked_reason(command: str) -> str | None:
    """Return why `command` is blocked, or None if it's allowed to run."""
    for pattern, reason in _BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


async def bash_guardrail_hook(
    input_data: HookInput, tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    if input_data.get("tool_name") != "Bash":
        return {}

    command = (input_data.get("tool_input") or {}).get("command", "")
    reason = blocked_reason(command)
    if reason is None:
        return {}

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Blocked by guardrails: {reason}",
        }
    }


def path_jail_reason(file_path: str, repo_root: Path) -> str | None:
    """Return why writing to `file_path` is blocked, or None if it's fine.

    Blocked when the resolved path falls outside `repo_root`, or lands inside
    `repo_root/.git` (writing git internals directly, bypassing the Bash
    guardrail entirely since it never invokes the `git` binary).
    """
    if not file_path:
        return None

    root = repo_root.resolve()
    candidate = Path(file_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()

    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return f"{resolved} resolves outside the target repo ({root})"

    if ".git" in rel.parts:
        return "writing inside .git/ directly is not allowed"

    return None


def make_path_jail_hook(repo_root: Path):
    """Builds a PreToolUse hook for Write/Edit that enforces path_jail_reason()
    against `repo_root`. A factory because the hook signature the SDK calls
    has no room for extra arguments, so `repo_root` has to be captured in a
    closure instead.
    """

    async def hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        if input_data.get("tool_name") not in ("Write", "Edit"):
            return {}

        file_path = (input_data.get("tool_input") or {}).get("file_path", "")
        reason = path_jail_reason(file_path, repo_root)
        if reason is None:
            return {}

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Blocked by guardrails: {reason}",
            }
        }

    return hook
