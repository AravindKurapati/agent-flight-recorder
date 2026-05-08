import sqlite3
from typing import Optional


def get_stats(conn: sqlite3.Connection, days: Optional[int] = None) -> dict:
    where, params = "", []
    if days:
        where = "WHERE started_at >= datetime('now', ?)"
        params = [f'-{days} days']

    runs = conn.execute(f"SELECT * FROM runs {where}", params).fetchall()
    if not runs:
        return {}

    outcomes: dict[str, int] = {}
    for r in runs:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1

    run_ids = [r["id"] for r in runs]
    ph = ",".join("?" * len(run_ids))

    top_tools = conn.execute(
        f"SELECT tool_name, COUNT(*) as cnt FROM tool_calls WHERE run_id IN ({ph}) GROUP BY tool_name ORDER BY cnt DESC LIMIT 10",
        run_ids,
    ).fetchall()

    error_count = conn.execute(
        f"SELECT COUNT(*) FROM errors WHERE run_id IN ({ph})", run_ids
    ).fetchone()[0]

    shell_failures = conn.execute(
        f"SELECT COUNT(*) FROM shell_commands WHERE run_id IN ({ph}) AND exit_code IS NOT NULL AND exit_code != 0",
        run_ids,
    ).fetchone()[0]

    return {
        "total_runs": len(runs),
        "outcomes": outcomes,
        "total_cost_usd": sum(r["cost_usd"] for r in runs),
        "total_tokens_in": sum(r["tokens_in"] for r in runs),
        "total_tokens_out": sum(r["tokens_out"] for r in runs),
        "top_tools": [(r["tool_name"], r["cnt"]) for r in top_tools],
        "error_count": error_count,
        "shell_failures": shell_failures,
    }
