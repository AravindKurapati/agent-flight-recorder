import json
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


def _seed(db_path):
    conn = get_connection(db_path)
    upsert_session(conn, ParsedSession(run=Run(
        id="run-0001", source="claude", outcome="shipped", cost_usd=2.0,
        cwd=r"D:\Aru\NYU\agent-flight-recorder",
        started_at="2026-07-22T10:00:00Z", ended_at="2026-07-22T11:00:00Z",
        user_goal="ship the digest feature",
    )))
    conn.close()


def test_digest_default_is_week(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["digest"])
    assert result.exit_code == 0, result.output
    assert "Digest" in result.output
    assert "agent-flight-recorder" in result.output


def test_digest_month_flag(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["digest", "--month"])
    assert result.exit_code == 0, result.output
    assert "30 days" in result.output


def test_digest_json_outputs_valid_json(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["digest", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_runs"] == 1
    assert payload["by_project"]["agent-flight-recorder"]["runs"] == 1
    assert payload["sessions"][0]["user_goal"] == "ship the digest feature"


def test_digest_json_empty_db_is_valid_json(tmp_db):
    result = CliRunner().invoke(app, ["digest", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_runs"] == 0
    assert payload["sessions"] == []


def test_digest_empty_db_human_message(tmp_db):
    result = CliRunner().invoke(app, ["digest"])
    assert result.exit_code == 0
    assert "No sessions" in result.output
