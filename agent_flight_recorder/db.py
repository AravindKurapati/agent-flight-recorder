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
            cache_write INTEGER DEFAULT 0
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
    conn.commit()


def upsert_session(conn: sqlite3.Connection, session: ParsedSession) -> bool:
    existing = conn.execute("SELECT id FROM runs WHERE id = ?", (session.run.id,)).fetchone()
    if existing:
        return False
    r = session.run
    conn.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
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


def list_runs(conn: sqlite3.Connection, days: Optional[int] = None) -> list:
    sql, params = "SELECT * FROM runs", []
    if days:
        sql += " WHERE started_at >= datetime('now', ?)"
        params = [f'-{days} days']
    return conn.execute(sql + " ORDER BY started_at DESC", params).fetchall()


def search_runs(conn: sqlite3.Connection, query: str, days: Optional[int] = None) -> list:
    sql = "SELECT runs.* FROM runs_fts JOIN runs ON runs_fts.id = runs.id WHERE runs_fts MATCH ?"
    params: list = [query]
    if days:
        sql += " AND runs.started_at >= datetime('now', ?)"
        params.append(f'-{days} days')
    return conn.execute(sql, params).fetchall()


def get_run(conn: sqlite3.Connection, run_id: str):
    return conn.execute("SELECT * FROM runs WHERE id LIKE ?", (f"{run_id}%",)).fetchone()


def get_run_events(conn: sqlite3.Connection, run_id: str) -> dict:
    return {
        "tool_calls": conn.execute("SELECT * FROM tool_calls WHERE run_id=? ORDER BY timestamp", (run_id,)).fetchall(),
        "shell_commands": conn.execute("SELECT * FROM shell_commands WHERE run_id=? ORDER BY timestamp", (run_id,)).fetchall(),
        "files": conn.execute("SELECT * FROM files WHERE run_id=?", (run_id,)).fetchall(),
        "errors": conn.execute("SELECT * FROM errors WHERE run_id=? ORDER BY timestamp", (run_id,)).fetchall(),
    }


def set_outcome(conn: sqlite3.Connection, run_id: str, outcome: str) -> None:
    conn.execute("UPDATE runs SET outcome=? WHERE id=?", (outcome, run_id))
    conn.commit()
