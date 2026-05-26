import sqlite3
from pathlib import Path
from typing import Optional
from .models import ParsedSession

DB_PATH = Path.home() / ".afr" / "afr.db"


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Idempotent ADD COLUMN — SQLite has no ADD COLUMN IF NOT EXISTS pre-3.35."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            project_path TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            ended_at TEXT DEFAULT '',
            user_goal TEXT DEFAULT '',
            final_summary TEXT DEFAULT '',
            outcome TEXT DEFAULT 'untagged',
            cost_usd REAL DEFAULT 0.0,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            cache_read INTEGER DEFAULT 0,
            cache_write INTEGER DEFAULT 0,
            tag_note TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tool_calls (
            id TEXT,
            run_id TEXT NOT NULL REFERENCES runs(id),
            tool_name TEXT DEFAULT '',
            input_summary TEXT DEFAULT '',
            output_summary TEXT DEFAULT '',
            status TEXT DEFAULT 'success',
            duration_ms INTEGER,
            timestamp TEXT DEFAULT '',
            raw_json TEXT DEFAULT '',
            PRIMARY KEY (run_id, id)
        );
        CREATE TABLE IF NOT EXISTS shell_commands (
            id TEXT,
            run_id TEXT NOT NULL REFERENCES runs(id),
            command TEXT DEFAULT '',
            exit_code INTEGER,
            duration_ms INTEGER,
            stdout_excerpt TEXT DEFAULT '',
            stderr_excerpt TEXT DEFAULT '',
            timestamp TEXT DEFAULT '',
            PRIMARY KEY (run_id, id)
        );
        CREATE TABLE IF NOT EXISTS files (
            id TEXT,
            run_id TEXT NOT NULL REFERENCES runs(id),
            path TEXT DEFAULT '',
            action TEXT DEFAULT '',
            PRIMARY KEY (run_id, id)
        );
        CREATE TABLE IF NOT EXISTS errors (
            id TEXT,
            run_id TEXT NOT NULL REFERENCES runs(id),
            source TEXT DEFAULT '',
            message TEXT DEFAULT '',
            raw_json TEXT DEFAULT '',
            timestamp TEXT DEFAULT '',
            PRIMARY KEY (run_id, id)
        );
        CREATE TABLE IF NOT EXISTS lessons (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            lesson TEXT DEFAULT '',
            category TEXT DEFAULT '',
            confidence REAL DEFAULT 0.0,
            approved INTEGER DEFAULT 0
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS runs_fts USING fts5(
            id UNINDEXED,
            user_goal,
            final_summary,
            content='runs',
            content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS runs_ai AFTER INSERT ON runs BEGIN
            INSERT INTO runs_fts(rowid, id, user_goal, final_summary)
            VALUES (new.rowid, new.id, new.user_goal, new.final_summary);
        END;
    """)
    # Migrations for existing DBs created before column-add features.
    _ensure_column(conn, "runs", "tag_note", "TEXT DEFAULT ''")
    conn.commit()


def upsert_session(conn: sqlite3.Connection, session: ParsedSession) -> bool:
    existing = conn.execute("SELECT id FROM runs WHERE id = ?", (session.run.id,)).fetchone()
    if existing:
        return False
    r = session.run
    conn.execute(
        """INSERT INTO runs (id, source, project_path, started_at, ended_at, user_goal,
                             final_summary, outcome, cost_usd, tokens_in, tokens_out,
                             cache_read, cache_write)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (r.id, r.source, r.project_path, r.started_at, r.ended_at, r.user_goal,
         r.final_summary, r.outcome, r.cost_usd, r.tokens_in, r.tokens_out, r.cache_read, r.cache_write)
    )
    for tc in session.tool_calls:
        conn.execute(
            "INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?)",
            (tc.id, tc.run_id, tc.tool_name, tc.input_summary, tc.output_summary,
             tc.status, tc.duration_ms, tc.timestamp, tc.raw_json)
        )
    for sc in session.shell_commands:
        conn.execute(
            "INSERT INTO shell_commands VALUES (?,?,?,?,?,?,?,?)",
            (sc.id, sc.run_id, sc.command, sc.exit_code, sc.duration_ms,
             sc.stdout_excerpt, sc.stderr_excerpt, sc.timestamp)
        )
    for fe in session.files:
        conn.execute("INSERT INTO files VALUES (?,?,?,?)", (fe.id, fe.run_id, fe.path, fe.action))
    for err in session.errors:
        conn.execute(
            "INSERT INTO errors VALUES (?,?,?,?,?,?)",
            (err.id, err.run_id, err.source, err.message, err.raw_json, err.timestamp)
        )
    conn.commit()
    return True


def list_runs(conn: sqlite3.Connection, days: Optional[int] = None, limit: Optional[int] = None) -> list:
    sql, params = "SELECT * FROM runs", []
    if days:
        sql += " WHERE started_at >= datetime('now', ?)"
        params = [f'-{days} days']
    sql += " ORDER BY started_at DESC"
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def search_runs(conn: sqlite3.Connection, query: str, days: Optional[int] = None) -> list:
    base = "SELECT runs.* FROM runs_fts JOIN runs ON runs_fts.id = runs.id WHERE runs_fts MATCH ?"
    suffix = ""
    params: list = [query]
    if days:
        suffix = " AND runs.started_at >= datetime('now', ?)"
        params.append(f'-{days} days')
    order = " ORDER BY runs.started_at DESC"
    try:
        return conn.execute(base + suffix + order, params).fetchall()
    except sqlite3.OperationalError:
        # FTS5 special chars (e.g. hyphens) — retry as a quoted phrase
        params[0] = f'"{query}"'
        return conn.execute(base + suffix + order, params).fetchall()


def get_run(conn: sqlite3.Connection, run_id: str):
    return conn.execute("SELECT * FROM runs WHERE id LIKE ?", (f"{run_id}%",)).fetchone()


def get_latest_run(conn: sqlite3.Connection, cwd_match: Optional[str] = None):
    """Return most recently active run, optionally filtered by project_path containing cwd_match.

    Orders by ended_at DESC with started_at as tiebreaker — last-active wins over last-started.
    """
    sql = "SELECT * FROM runs"
    params: list = []
    if cwd_match:
        sql += " WHERE project_path LIKE ?"
        params.append(f"%{cwd_match}%")
    sql += " ORDER BY COALESCE(NULLIF(ended_at,''), started_at) DESC, started_at DESC LIMIT 1"
    return conn.execute(sql, params).fetchone()


def get_run_events(conn: sqlite3.Connection, run_id: str) -> dict:
    return {
        "tool_calls": conn.execute("SELECT * FROM tool_calls WHERE run_id=? ORDER BY timestamp", (run_id,)).fetchall(),
        "shell_commands": conn.execute("SELECT * FROM shell_commands WHERE run_id=? ORDER BY timestamp", (run_id,)).fetchall(),
        "files": conn.execute("SELECT * FROM files WHERE run_id=?", (run_id,)).fetchall(),
        "errors": conn.execute("SELECT * FROM errors WHERE run_id=? ORDER BY timestamp", (run_id,)).fetchall(),
    }


def bulk_set_outcome(
    conn: sqlite3.Connection,
    outcome: str,
    untagged_only: bool = False,
    older_than_days: Optional[int] = None,
    note: Optional[str] = None,
) -> int:
    """Apply an outcome to many runs. Returns the count of rows updated."""
    conds, params = [], []
    if untagged_only:
        conds.append("outcome = 'untagged'")
    if older_than_days is not None and older_than_days > 0:
        conds.append("started_at != '' AND started_at < datetime('now', ?)")
        params.append(f"-{older_than_days} days")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    if note is None:
        sql = f"UPDATE runs SET outcome = ?{where}"
        cur = conn.execute(sql, [outcome] + params)
    else:
        sql = f"UPDATE runs SET outcome = ?, tag_note = ?{where}"
        cur = conn.execute(sql, [outcome, note] + params)
    conn.commit()
    return cur.rowcount


def count_runs_for_bulk(
    conn: sqlite3.Connection,
    untagged_only: bool = False,
    older_than_days: Optional[int] = None,
) -> int:
    """Count rows that bulk_set_outcome would touch with the same filters."""
    conds, params = [], []
    if untagged_only:
        conds.append("outcome = 'untagged'")
    if older_than_days is not None and older_than_days > 0:
        conds.append("started_at != '' AND started_at < datetime('now', ?)")
        params.append(f"-{older_than_days} days")
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return conn.execute(f"SELECT COUNT(*) AS n FROM runs{where}", params).fetchone()["n"]


def set_outcome(conn: sqlite3.Connection, run_id: str, outcome: str, note: Optional[str] = None) -> None:
    if note is None:
        conn.execute("UPDATE runs SET outcome=? WHERE id=?", (outcome, run_id))
    else:
        conn.execute("UPDATE runs SET outcome=?, tag_note=? WHERE id=?", (outcome, note, run_id))
    conn.commit()
