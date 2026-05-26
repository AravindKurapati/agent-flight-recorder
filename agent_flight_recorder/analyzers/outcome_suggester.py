"""Heuristic outcome suggestions based on session signals.

Rules are intentionally conservative — return None when no strong signal exists,
so the user isn't pushed toward a wrong tag.
"""
import re
from typing import Optional

# Successful "ship" commands — strong signal that work was delivered.
_SHIP_PATTERNS = [
    re.compile(r"\bgit\s+commit\b"),
    re.compile(r"\bgit\s+push\b"),
    re.compile(r"\bgh\s+pr\s+create\b"),
    re.compile(r"\bnpm\s+publish\b"),
    re.compile(r"\btwine\s+upload\b"),
    re.compile(r"\bvercel\s+(deploy|--prod)\b"),
]

_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def suggest_outcome(events: dict) -> Optional[tuple[str, str]]:
    """Return (outcome, reason) or None when there's no clear signal.

    `events` matches the dict shape returned by db.get_run_events().
    """
    shell_cmds = events.get("shell_commands", []) or []
    errors = events.get("errors", []) or []
    tool_calls = events.get("tool_calls", []) or []

    # Rule 1: successful ship-style shell command → shipped.
    for sc in shell_cmds:
        cmd = sc["command"] or ""
        if sc["exit_code"] not in (0, None):
            continue
        for pat in _SHIP_PATTERNS:
            m = pat.search(cmd)
            if m:
                return ("shipped", f"successful `{m.group(0)}`")

    # Rule 2: failed shell commands or many errors → blocked.
    failed_shell = [sc for sc in shell_cmds if sc["exit_code"] not in (0, None)]
    if len(errors) >= 3:
        return ("blocked", f"{len(errors)} errors recorded")
    if failed_shell and len(failed_shell) >= max(2, len(shell_cmds) // 2):
        return ("blocked", f"{len(failed_shell)} of {len(shell_cmds)} shell commands failed")

    # Rule 3: read-only session → exploratory.
    if tool_calls:
        has_writes = any(tc["tool_name"] in _WRITE_TOOLS for tc in tool_calls)
        if not has_writes and len(tool_calls) >= 3:
            return ("exploratory", "no file writes — looks like research/reading")

    return None
