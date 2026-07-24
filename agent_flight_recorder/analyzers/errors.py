"""Recurring failed shell-command fingerprints across recorded runs.

Fingerprint = exact (command, exit_code) pair, no normalization. Read-only
over shell_commands joined to runs (for the optional days window).
"""
import sqlite3
from typing import Optional


def get_recurring_errors(
    conn: sqlite3.Connection, min_count: int = 2, days: Optional[int] = None
) -> list[dict]:
    min_count = max(min_count, 1)

    where = "WHERE sc.exit_code IS NOT NULL AND sc.exit_code != 0"
    params: list = []
    if days:
        where += " AND r.started_at >= datetime('now', ?)"
        params.append(f"-{days} days")

    rows = conn.execute(
        f"""
        SELECT sc.command, sc.exit_code, sc.timestamp, sc.run_id
        FROM shell_commands sc
        JOIN runs r ON r.id = sc.run_id
        {where}
        ORDER BY sc.timestamp ASC
        """,
        params,
    ).fetchall()

    groups: dict[tuple, dict] = {}
    for row in rows:
        key = (row["command"], row["exit_code"])
        g = groups.get(key)
        if g is None:
            g = {
                "command": row["command"], "exit_code": row["exit_code"],
                "count": 0, "first_seen": row["timestamp"], "last_seen": row["timestamp"],
                "run_ids": [],
            }
            groups[key] = g
        g["count"] += 1
        g["last_seen"] = row["timestamp"]
        run_prefix = row["run_id"][:8]
        if run_prefix not in g["run_ids"]:
            g["run_ids"].append(run_prefix)

    entries = [g for g in groups.values() if g["count"] >= min_count]
    entries.sort(key=lambda e: (e["count"], e["last_seen"] or ""), reverse=True)
    return entries
