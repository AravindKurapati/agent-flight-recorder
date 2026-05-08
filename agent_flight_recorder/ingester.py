from pathlib import Path
from typing import Literal
import sqlite3
from .db import get_connection, init_db, upsert_session, DB_PATH
from .adapters import claude, codex


def ingest_from_paths(
    paths: list[Path],
    source: Literal["claude", "codex"],
    conn: sqlite3.Connection,
) -> tuple[int, int]:
    new, skipped = 0, 0
    parser = claude.parse_session_file if source == "claude" else codex.parse_session_file
    for p in paths:
        try:
            session = parser(Path(p))
            if upsert_session(conn, session):
                new += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    return new, skipped


def ingest_claude(db_path: Path = DB_PATH) -> tuple[int, int]:
    conn = get_connection(db_path)
    init_db(conn)
    paths = list(claude.iter_sessions())
    result = ingest_from_paths(paths, "claude", conn)
    conn.close()
    return result


def ingest_codex(db_path: Path = DB_PATH) -> tuple[int, int]:
    conn = get_connection(db_path)
    init_db(conn)
    paths = list(codex.iter_sessions())
    result = ingest_from_paths(paths, "codex", conn)
    conn.close()
    return result
