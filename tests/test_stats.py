import pytest
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.analyzers.stats import get_stats
from agent_flight_recorder.models import ParsedSession, Run, ToolCall, Error


@pytest.fixture
def db_with_data(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    for i in range(3):
        run = Run(id=f"run-{i}", source="claude", user_goal=f"task {i}",
                  tokens_in=100, tokens_out=50)
        tc = ToolCall(id=f"tc-{i}", run_id=f"run-{i}", tool_name="Bash", status="success")
        err = Error(id=f"err-{i}", run_id=f"run-{i}", source="tool", message="oops")
        upsert_session(conn, ParsedSession(run=run, tool_calls=[tc], errors=[err]))
    return conn


def test_stats_total_runs(db_with_data):
    s = get_stats(db_with_data)
    assert s["total_runs"] == 3


def test_stats_top_tools(db_with_data):
    s = get_stats(db_with_data)
    assert s["top_tools"][0][0] == "Bash"
    assert s["top_tools"][0][1] == 3


def test_stats_error_count(db_with_data):
    s = get_stats(db_with_data)
    assert s["error_count"] == 3


def test_stats_token_totals(db_with_data):
    s = get_stats(db_with_data)
    assert s["total_tokens_in"] == 300
    assert s["total_tokens_out"] == 150


def test_stats_empty_db(tmp_path):
    conn = get_connection(tmp_path / "empty.db")
    init_db(conn)
    assert get_stats(conn) == {}
