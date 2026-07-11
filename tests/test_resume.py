import sqlite3
import pytest
from typer.testing import CliRunner

from agent_flight_recorder.cli import app
from agent_flight_recorder.db import (
    get_connection, init_db, upsert_session, resolve_runs_by_prefix,
)
from agent_flight_recorder.models import ParsedSession, Run


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "afr.db"
    monkeypatch.setattr("agent_flight_recorder.cli.get_connection", lambda: get_connection(db_path))
    conn = get_connection(db_path)
    init_db(conn)
    conn.close()
    return db_path


def _seed(db_path, **kw):
    conn = get_connection(db_path)
    upsert_session(conn, ParsedSession(run=Run(**kw)))
    conn.close()


# --- db layer -------------------------------------------------------------

def test_upsert_persists_cwd_and_branch(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    init_db(conn)
    upsert_session(conn, ParsedSession(run=Run(
        id="run-cwd-1", source="claude", cwd="D:\\Aru\\NYU", git_branch="main")))
    row = conn.execute("SELECT cwd, git_branch FROM runs WHERE id='run-cwd-1'").fetchone()
    conn.close()
    assert row["cwd"] == "D:\\Aru\\NYU"
    assert row["git_branch"] == "main"


def test_reingest_backfills_cwd_onto_existing_row(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    init_db(conn)
    # First ingest recorded no location (pre-0.2.0 behaviour).
    assert upsert_session(conn, ParsedSession(run=Run(id="run-bf", source="claude"))) is True
    # Re-ingest of the same session, now carrying cwd/branch, must backfill (still "not new").
    assert upsert_session(conn, ParsedSession(run=Run(
        id="run-bf", source="claude", cwd="D:\\Aru\\NYU", git_branch="main"))) is False
    row = conn.execute("SELECT cwd, git_branch FROM runs WHERE id='run-bf'").fetchone()
    conn.close()
    assert row["cwd"] == "D:\\Aru\\NYU"
    assert row["git_branch"] == "main"


def test_reingest_does_not_clobber_existing_cwd(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    init_db(conn)
    upsert_session(conn, ParsedSession(run=Run(id="run-keep", source="claude", cwd="/original")))
    upsert_session(conn, ParsedSession(run=Run(id="run-keep", source="claude", cwd="/different")))
    row = conn.execute("SELECT cwd FROM runs WHERE id='run-keep'").fetchone()
    conn.close()
    assert row["cwd"] == "/original"


def test_resolve_runs_by_prefix_unique(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    init_db(conn)
    upsert_session(conn, ParsedSession(run=Run(id="abc12345-1111", source="claude")))
    upsert_session(conn, ParsedSession(run=Run(id="def67890-2222", source="claude")))
    matches = resolve_runs_by_prefix(conn, "abc12345")
    conn.close()
    assert [m["id"] for m in matches] == ["abc12345-1111"]


def test_resolve_runs_by_prefix_ambiguous(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    init_db(conn)
    upsert_session(conn, ParsedSession(run=Run(id="dead0001", source="claude",
                                               started_at="2026-05-01T10:00:00Z")))
    upsert_session(conn, ParsedSession(run=Run(id="dead0002", source="claude",
                                               started_at="2026-05-02T10:00:00Z")))
    matches = resolve_runs_by_prefix(conn, "dead")
    conn.close()
    assert len(matches) == 2


def test_init_db_migrates_cwd_columns_onto_old_schema(tmp_path):
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute("""CREATE TABLE runs (id TEXT PRIMARY KEY, source TEXT NOT NULL,
        project_path TEXT DEFAULT '', started_at TEXT DEFAULT '', ended_at TEXT DEFAULT '',
        user_goal TEXT DEFAULT '', final_summary TEXT DEFAULT '', outcome TEXT DEFAULT 'untagged',
        cost_usd REAL DEFAULT 0.0, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
        cache_read INTEGER DEFAULT 0, cache_write INTEGER DEFAULT 0)""")
    raw.execute("INSERT INTO runs (id, source) VALUES ('run-x','claude')")
    raw.commit()
    raw.close()
    conn = get_connection(db_path)
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    row = conn.execute("SELECT cwd, git_branch FROM runs WHERE id='run-x'").fetchone()
    conn.close()
    assert "cwd" in cols and "git_branch" in cols
    assert row["cwd"] == "" and row["git_branch"] == ""


# --- cli ------------------------------------------------------------------

def test_resume_prints_full_id_and_command(tmp_db):
    _seed(tmp_db, id="feedface-aaaa-bbbb", source="claude", cwd="D:\\Aru\\NYU", user_goal="g")
    result = CliRunner().invoke(app, ["resume", "feedface"])
    assert result.exit_code == 0, result.output
    # full id, not the 8-char prefix
    assert "feedface-aaaa-bbbb" in result.output
    assert "claude --resume feedface-aaaa-bbbb" in result.output
    assert "D:\\Aru\\NYU" in result.output


def test_resume_ambiguous_prefix_exits_1(tmp_db):
    _seed(tmp_db, id="dead0001", source="claude", started_at="2026-05-01T10:00:00Z", user_goal="g")
    _seed(tmp_db, id="dead0002", source="claude", started_at="2026-05-02T10:00:00Z", user_goal="g")
    result = CliRunner().invoke(app, ["resume", "dead"])
    assert result.exit_code == 1
    assert "Multiple runs match" in result.output


def test_resume_not_found_exits_1(tmp_db):
    result = CliRunner().invoke(app, ["resume", "nope"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_resume_codex_uses_codex_command(tmp_db):
    _seed(tmp_db, id="codex-1234", source="codex", cwd="/home/u/proj", user_goal="g")
    result = CliRunner().invoke(app, ["resume", "codex-1234"])
    assert result.exit_code == 0, result.output
    assert "codex" in result.output
    assert "codex-1234" in result.output


def test_resume_without_cwd_still_gives_full_id(tmp_db):
    _seed(tmp_db, id="nocwd-9999", source="claude", cwd="", user_goal="g")
    result = CliRunner().invoke(app, ["resume", "nocwd-9999"])
    assert result.exit_code == 0, result.output
    assert "nocwd-9999" in result.output
    assert "claude --resume nocwd-9999" in result.output
