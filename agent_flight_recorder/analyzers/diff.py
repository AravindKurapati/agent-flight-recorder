"""Compare two recorded runs: summary stats and an aligned tool-call sequence.

Pure logic only: no DB access. `compute_summary` takes a `runs` row and a
`db.get_run_events`-shaped dict; `align_tool_calls` takes plain name lists so
it's independently testable.
"""
import difflib
from datetime import datetime
from typing import Optional


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_summary(run, events: dict) -> dict:
    started = _parse_iso(run["started_at"])
    ended = _parse_iso(run["ended_at"])
    duration_seconds = (ended - started).total_seconds() if started and ended else None

    shell_failure_count = sum(
        1 for sc in events["shell_commands"]
        if sc["exit_code"] is not None and sc["exit_code"] != 0
    )

    return {
        "cost_usd": run["cost_usd"],
        "tokens_in": run["tokens_in"],
        "tokens_out": run["tokens_out"],
        "duration_seconds": duration_seconds,
        "outcome": run["outcome"],
        "tool_call_count": len(events["tool_calls"]),
        "error_count": len(events["errors"]),
        "shell_failure_count": shell_failure_count,
    }


def align_tool_calls(names_a: list[str], names_b: list[str]) -> list[tuple]:
    matcher = difflib.SequenceMatcher(None, names_a, names_b)
    rows: list[tuple] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        a_slice = names_a[i1:i2]
        b_slice = names_b[j1:j2]
        if tag == "equal":
            rows.extend(zip(a_slice, b_slice))
        else:
            for k in range(max(len(a_slice), len(b_slice))):
                rows.append((
                    a_slice[k] if k < len(a_slice) else None,
                    b_slice[k] if k < len(b_slice) else None,
                ))
    return rows
