import pytest
from typer.testing import CliRunner

from agent_flight_recorder.cli import app
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.models import ParsedSession, Run, ToolCall


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "afr.db"
    monkeypatch.setattr("agent_flight_recorder.cli.get_connection", lambda: get_connection(db_path))
    conn = get_connection(db_path)
    init_db(conn)
    conn.close()
    return db_path


def _seed_two(db_path):
    conn = get_connection(db_path)
    upsert_session(conn, ParsedSession(
        run=Run(id="run-aaaa1111", source="claude", outcome="shipped", cost_usd=2.0,
                started_at="2026-07-20T10:00:00Z", ended_at="2026-07-20T10:10:00Z"),
        tool_calls=[ToolCall(id="tc-a1", run_id="run-aaaa1111", tool_name="Bash")],
    ))
    upsert_session(conn, ParsedSession(
        run=Run(id="run-bbbb2222", source="claude", outcome="abandoned", cost_usd=1.0,
                started_at="2026-07-21T10:00:00Z", ended_at="2026-07-21T10:05:00Z"),
        tool_calls=[ToolCall(id="tc-b1", run_id="run-bbbb2222", tool_name="Read")],
    ))
    conn.close()


def test_diff_two_valid_ids(tmp_db):
    _seed_two(tmp_db)
    result = CliRunner().invoke(app, ["diff", "run-aaaa1111", "run-bbbb2222"])
    assert result.exit_code == 0, result.output
    assert "shipped" in result.output
    assert "abandoned" in result.output


def test_diff_not_found_id(tmp_db):
    _seed_two(tmp_db)
    result = CliRunner().invoke(app, ["diff", "run-aaaa1111", "nonexistent"])
    assert result.exit_code == 1
    assert "No run matches" in result.output


def test_diff_ambiguous_first_id(tmp_db):
    conn = get_connection(tmp_db)
    upsert_session(conn, ParsedSession(run=Run(id="run-dup-1", source="claude")))
    upsert_session(conn, ParsedSession(run=Run(id="run-dup-2", source="claude")))
    upsert_session(conn, ParsedSession(run=Run(id="run-solo", source="claude")))
    conn.close()
    result = CliRunner().invoke(app, ["diff", "run-dup", "run-solo"])
    assert result.exit_code == 1
    assert "Multiple runs match" in result.output


def test_diff_both_ambiguous_reports_both(tmp_db):
    conn = get_connection(tmp_db)
    upsert_session(conn, ParsedSession(run=Run(id="run-foo-1", source="claude")))
    upsert_session(conn, ParsedSession(run=Run(id="run-foo-2", source="claude")))
    upsert_session(conn, ParsedSession(run=Run(id="run-bar-1", source="claude")))
    upsert_session(conn, ParsedSession(run=Run(id="run-bar-2", source="claude")))
    conn.close()
    result = CliRunner().invoke(app, ["diff", "run-foo", "run-bar"])
    assert result.exit_code == 1
    assert result.output.count("Multiple runs match") == 2


def test_diff_full_flag_shows_sequence(tmp_db):
    _seed_two(tmp_db)
    result = CliRunner().invoke(app, ["diff", "run-aaaa1111", "run-bbbb2222", "--full"])
    assert result.exit_code == 0, result.output
    assert "Tool-call sequence" in result.output


def test_diff_identical_id_twice(tmp_db):
    _seed_two(tmp_db)
    result = CliRunner().invoke(app, ["diff", "run-aaaa1111", "run-aaaa1111"])
    assert result.exit_code == 0, result.output
