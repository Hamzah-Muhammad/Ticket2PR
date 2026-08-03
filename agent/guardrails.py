"""Safety rails for the agent's Bash access.

Two layers:
1. `blocked_reason()` — pure, unit-testable logic: given a shell command string,
   returns why it's blocked, or None if it's fine.
2. `bash_guardrail_hook()` — a PreToolUse hook (see claude_agent_sdk hooks) that
   calls #1 on every Bash tool call the agent makes, *before* it runs, and denies
   it if blocked. PreToolUse fires for every Bash call regardless of allowed_tools,
   unlike can_use_tool (which is skipped for tools already auto-allowed) — that's
   why this ships as a hook and not a permission callback.
"""

from __future__ import annotations

import re
from typing import Any

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
