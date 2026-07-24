"""Weekly/monthly rollup over recorded runs.

Builds on analyzers.stats.get_stats for totals, adding per-project grouping
and two derived signals (abandoned streak, cost-per-shipped). Read-only;
takes `now` as a parameter so tests are deterministic.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from .stats import get_stats

_STREAK_OUTCOMES = {"abandoned", "blocked"}


def _project_name(cwd: str) -> str:
    if not cwd:
        return "unknown"
    normalized = cwd.rstrip("/\\").replace("\\", "/")
    return normalized.rsplit("/", 1)[-1] or "unknown"


def get_digest(conn: sqlite3.Connection, days: int, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    period_since = (now - timedelta(days=days)).isoformat()

    base = get_stats(conn, days)
    outcomes = base.get("outcomes", {})
    total_cost_usd = base.get("total_cost_usd", 0.0)

    runs = conn.execute(
        "SELECT * FROM runs WHERE started_at >= datetime('now', ?) ORDER BY started_at DESC",
        [f"-{days} days"],
    ).fetchall()

    by_project: dict[str, dict] = {}
    for r in runs:
        project = _project_name(r["cwd"])
        bucket = by_project.setdefault(project, {
            "runs": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "outcomes": {},
        })
        bucket["runs"] += 1
        bucket["cost_usd"] += r["cost_usd"]
        bucket["tokens_in"] += r["tokens_in"]
        bucket["tokens_out"] += r["tokens_out"]
        bucket["outcomes"][r["outcome"]] = bucket["outcomes"].get(r["outcome"], 0) + 1

    abandoned_streak = 0
    for r in runs:  # already newest-first
        if r["outcome"] in _STREAK_OUTCOMES:
            abandoned_streak += 1
        else:
            break

    shipped_count = outcomes.get("shipped", 0)
    cost_per_shipped = (total_cost_usd / shipped_count) if shipped_count else None

    sessions = [
        {
            "id": r["id"][:8],
            "started_at": r["started_at"],
            "cwd_project": _project_name(r["cwd"]),
            "outcome": r["outcome"],
            "user_goal": r["user_goal"],
            "final_summary": r["final_summary"],
        }
        for r in runs
    ]

    return {
        "period_days": days,
        "period_since": period_since,
        "total_runs": base.get("total_runs", 0),
        "total_cost_usd": total_cost_usd,
        "total_tokens_in": base.get("total_tokens_in", 0),
        "total_tokens_out": base.get("total_tokens_out", 0),
        "outcomes": outcomes,
        "cost_per_shipped": cost_per_shipped,
        "abandoned_streak": abandoned_streak,
        "by_project": by_project,
        "sessions": sessions,
    }
