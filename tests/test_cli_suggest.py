from unittest.mock import patch
import pytest
from typer.testing import CliRunner

from agent_flight_recorder.cli import app
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.models import ParsedSession, Run, ShellCommand, ToolCall


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "afr.db"
    monkeypatch.setattr("agent_flight_recorder.cli.get_connection", lambda: get_connection(db_path))
    conn = get_connection(db_path)
    init_db(conn)
    conn.close()
    return db_path


def _seed_shipped(db_path, run_id="run-ship-0001", project_path=""):
    conn = get_connection(db_path)
    upsert_session(conn, ParsedSession(
        run=Run(id=run_id, source="claude", project_path=project_path, user_goal="g",
                started_at="2026-05-02T09:00:00Z", ended_at="2026-05-02T10:00:00Z"),
        shell_commands=[ShellCommand(id="s1", run_id=run_id, command="git commit -m 'feat: x'", exit_code=0)],
    ))
    conn.close()


def test_suggest_by_id_prints_outcome(tmp_db):
    _seed_shipped(tmp_db)
    result = CliRunner().invoke(app, ["suggest", "run-ship"])
    assert result.exit_code == 0, result.output
    assert "shipped" in result.output
    assert "git commit" in result.output


def test_suggest_latest_filters_by_cwd(tmp_db, tmp_path):
    _seed_shipped(tmp_db, project_path="myproj")
    cwd = tmp_path / "myproj"
    cwd.mkdir()
    with patch("agent_flight_recorder.cli.Path") as mock_path:
        mock_path.cwd.return_value = cwd
        result = CliRunner().invoke(app, ["suggest", "--latest"])
    assert result.exit_code == 0, result.output
    assert "shipped" in result.output


def test_suggest_does_not_mutate(tmp_db):
    _seed_shipped(tmp_db)
    CliRunner().invoke(app, ["suggest", "run-ship"])
    conn = get_connection(tmp_db)
    row = conn.execute("SELECT outcome FROM runs WHERE id LIKE 'run-ship%'").fetchone()
    conn.close()
    # outcome must still be untagged — suggest is read-only.
    assert row["outcome"] == "untagged"


def test_suggest_no_signal(tmp_db):
    conn = get_connection(tmp_db)
    upsert_session(conn, ParsedSession(run=Run(id="run-quiet", source="claude", user_goal="g")))
    conn.close()
    result = CliRunner().invoke(app, ["suggest", "run-quiet"])
    assert result.exit_code == 0
    assert "unclear" in result.output


def test_suggest_run_not_found(tmp_db):
    result = CliRunner().invoke(app, ["suggest", "deadbeef"])
    assert result.exit_code == 1


def test_suggest_missing_args(tmp_db):
    result = CliRunner().invoke(app, ["suggest"])
    assert result.exit_code == 1
    assert "run ID or --latest" in result.output
