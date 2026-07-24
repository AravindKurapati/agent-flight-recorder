import pytest
from typer.testing import CliRunner

from agent_flight_recorder.cli import app
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.models import ParsedSession, Run, ShellCommand


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "afr.db"
    monkeypatch.setattr("agent_flight_recorder.cli.get_connection", lambda: get_connection(db_path))
    conn = get_connection(db_path)
    init_db(conn)
    conn.close()
    return db_path


def _seed(db_path):
    conn = get_connection(db_path)
    upsert_session(conn, ParsedSession(
        run=Run(id="run-1", source="claude", started_at="2026-07-20T10:00:00Z", ended_at="2026-07-20T10:00:00Z"),
        shell_commands=[ShellCommand(id="sc-1", run_id="run-1", command="pytest x", exit_code=1,
                                      timestamp="2026-07-20T10:00:00Z")],
    ))
    upsert_session(conn, ParsedSession(
        run=Run(id="run-2", source="claude", started_at="2026-07-21T10:00:00Z", ended_at="2026-07-21T10:00:00Z"),
        shell_commands=[ShellCommand(id="sc-2", run_id="run-2", command="pytest x", exit_code=1,
                                      timestamp="2026-07-21T10:00:00Z")],
    ))
    conn.close()


def test_errors_default_shows_recurring(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["errors"])
    assert result.exit_code == 0, result.output
    assert "pytest x" in result.output


def test_errors_min_count_excludes_below_threshold(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["errors", "--min-count", "3"])
    assert result.exit_code == 0, result.output
    assert "No recurring failures found" in result.output


def test_errors_days_filter(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["errors", "--days", "1"])
    assert result.exit_code == 0, result.output
    assert "No recurring failures found" in result.output


def test_errors_empty_db(tmp_db):
    result = CliRunner().invoke(app, ["errors"])
    assert result.exit_code == 0
    assert "No recurring failures found" in result.output
