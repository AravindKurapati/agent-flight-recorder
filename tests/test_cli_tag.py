from unittest.mock import patch
from pathlib import Path
import pytest
from typer.testing import CliRunner

from agent_flight_recorder.cli import app
from agent_flight_recorder.db import get_connection, init_db, upsert_session, list_runs
from agent_flight_recorder.models import ParsedSession, Run


def _session(run_id, project_path="", ended_at="2026-05-01T10:00:00Z", started_at="2026-05-01T09:00:00Z"):
    return ParsedSession(run=Run(
        id=run_id, source="claude", project_path=project_path,
        started_at=started_at, ended_at=ended_at, user_goal="g",
    ))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "afr.db"
    monkeypatch.setattr("agent_flight_recorder.cli.get_connection", lambda: get_connection(db_path))
    conn = get_connection(db_path)
    init_db(conn)
    conn.close()
    return db_path


def test_tag_latest_picks_most_recent_in_cwd(tmp_db, tmp_path):
    conn = get_connection(tmp_db)
    upsert_session(conn, _session("run-old", project_path="myproj",
                                   started_at="2026-05-01T09:00:00Z", ended_at="2026-05-01T10:00:00Z"))
    upsert_session(conn, _session("run-new", project_path="myproj",
                                   started_at="2026-05-02T09:00:00Z", ended_at="2026-05-02T10:00:00Z"))
    conn.close()

    cwd = tmp_path / "myproj"
    cwd.mkdir()
    with patch("agent_flight_recorder.cli.Path") as mock_path:
        mock_path.cwd.return_value = cwd
        result = CliRunner().invoke(app, ["tag", "--latest", "shipped"])

    assert result.exit_code == 0, result.output
    assert "run-new"[:8] in result.output

    conn = get_connection(tmp_db)
    runs = {r["id"]: r["outcome"] for r in list_runs(conn)}
    conn.close()
    assert runs["run-new"] == "shipped"
    assert runs["run-old"] == "untagged"


def test_tag_latest_errors_when_no_cwd_match(tmp_db, tmp_path):
    conn = get_connection(tmp_db)
    upsert_session(conn, _session("run-elsewhere", project_path="other-proj"))
    conn.close()

    cwd = tmp_path / "myproj"
    cwd.mkdir()
    with patch("agent_flight_recorder.cli.Path") as mock_path:
        mock_path.cwd.return_value = cwd
        result = CliRunner().invoke(app, ["tag", "--latest", "shipped"])

    assert result.exit_code == 1
    assert "No sessions found for cwd" in result.output


def test_tag_latest_any_cwd_finds_across_projects(tmp_db, tmp_path):
    conn = get_connection(tmp_db)
    upsert_session(conn, _session("run-elsewhere", project_path="other-proj"))
    conn.close()

    cwd = tmp_path / "myproj"
    cwd.mkdir()
    with patch("agent_flight_recorder.cli.Path") as mock_path:
        mock_path.cwd.return_value = cwd
        result = CliRunner().invoke(app, ["tag", "--latest", "--any-cwd", "shipped"])

    assert result.exit_code == 0, result.output
    conn = get_connection(tmp_db)
    runs = {r["id"]: r["outcome"] for r in list_runs(conn)}
    conn.close()
    assert runs["run-elsewhere"] == "shipped"


def test_tag_by_id_still_works(tmp_db):
    conn = get_connection(tmp_db)
    upsert_session(conn, _session("abcdef1234567890"))
    conn.close()

    result = CliRunner().invoke(app, ["tag", "abcdef12", "shipped"])
    assert result.exit_code == 0, result.output
    conn = get_connection(tmp_db)
    runs = {r["id"]: r["outcome"] for r in list_runs(conn)}
    conn.close()
    assert runs["abcdef1234567890"] == "shipped"


def test_tag_rejects_invalid_outcome(tmp_db):
    result = CliRunner().invoke(app, ["tag", "--latest", "bogus"])
    assert result.exit_code == 1
    assert "Invalid outcome" in result.output


def test_tag_without_args_errors(tmp_db):
    result = CliRunner().invoke(app, ["tag"])
    assert result.exit_code != 0
