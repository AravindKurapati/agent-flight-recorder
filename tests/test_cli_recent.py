import pytest
from typer.testing import CliRunner

from agent_flight_recorder.cli import app
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.models import ParsedSession, Run


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "afr.db"
    monkeypatch.setattr("agent_flight_recorder.cli.get_connection", lambda: get_connection(db_path))
    conn = get_connection(db_path)
    init_db(conn)
    conn.close()
    return db_path


def _seed(db_path, n=5):
    conn = get_connection(db_path)
    for i in range(n):
        upsert_session(conn, ParsedSession(run=Run(
            id=f"run-{i:04d}", source="claude", user_goal=f"task {i}",
            started_at=f"2026-05-{i+1:02d}T10:00:00Z",
            ended_at=f"2026-05-{i+1:02d}T11:00:00Z",
        )))
    conn.close()


def test_recent_default_shows_up_to_10(tmp_db):
    _seed(tmp_db, n=3)
    result = CliRunner().invoke(app, ["recent"])
    assert result.exit_code == 0, result.output
    for i in range(3):
        assert f"run-{i:04d}"[:8] in result.output


def test_recent_n_limits_output(tmp_db):
    _seed(tmp_db, n=5)
    result = CliRunner().invoke(app, ["recent", "2"])
    assert result.exit_code == 0, result.output
    # Newest two: run-0004 (2026-05-05) and run-0003 (2026-05-04).
    assert "run-0004"[:8] in result.output
    assert "run-0003"[:8] in result.output
    assert "run-0000"[:8] not in result.output
    assert "run-0001"[:8] not in result.output


def test_recent_empty_db(tmp_db):
    result = CliRunner().invoke(app, ["recent"])
    assert result.exit_code == 0
    assert "No runs" in result.output


def test_recent_rejects_zero(tmp_db):
    result = CliRunner().invoke(app, ["recent", "0"])
    assert result.exit_code == 1
    assert "must be positive" in result.output


def test_recent_rejects_negative(tmp_db):
    result = CliRunner().invoke(app, ["recent", "-3"])
    # Typer parses -3 as a flag-like token; either it errors at parse or our check fires.
    assert result.exit_code != 0
