import pytest
from agent_flight_recorder.db import get_connection, init_db, upsert_session, get_run, get_run_events
from agent_flight_recorder.analyzers.diff import compute_summary, align_tool_calls
from agent_flight_recorder.models import ParsedSession, Run, ToolCall, ShellCommand, Error


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    init_db(c)
    return c


def test_compute_summary_normal_run(conn):
    upsert_session(conn, ParsedSession(
        run=Run(id="run-1", source="claude", outcome="shipped", cost_usd=2.5,
                tokens_in=1000, tokens_out=500,
                started_at="2026-07-20T10:00:00Z", ended_at="2026-07-20T10:14:00Z"),
        tool_calls=[ToolCall(id="tc-1", run_id="run-1", tool_name="Bash"),
                    ToolCall(id="tc-2", run_id="run-1", tool_name="Edit")],
        shell_commands=[ShellCommand(id="sc-1", run_id="run-1", command="pytest", exit_code=1),
                        ShellCommand(id="sc-2", run_id="run-1", command="ls", exit_code=0)],
        errors=[Error(id="e-1", run_id="run-1", source="tool", message="oops")],
    ))
    run = get_run(conn, "run-1")
    events = get_run_events(conn, "run-1")
    s = compute_summary(run, events)
    assert s["cost_usd"] == 2.5
    assert s["tokens_in"] == 1000
    assert s["tokens_out"] == 500
    assert s["duration_seconds"] == pytest.approx(840.0)  # 14 minutes
    assert s["outcome"] == "shipped"
    assert s["tool_call_count"] == 2
    assert s["error_count"] == 1
    assert s["shell_failure_count"] == 1


def test_compute_summary_no_ended_at_is_in_progress(conn):
    upsert_session(conn, ParsedSession(run=Run(
        id="run-2", source="claude", started_at="2026-07-20T10:00:00Z", ended_at="")))
    run = get_run(conn, "run-2")
    events = get_run_events(conn, "run-2")
    s = compute_summary(run, events)
    assert s["duration_seconds"] is None


def test_compute_summary_zero_events(conn):
    upsert_session(conn, ParsedSession(run=Run(id="run-3", source="claude")))
    run = get_run(conn, "run-3")
    events = get_run_events(conn, "run-3")
    s = compute_summary(run, events)
    assert s["tool_call_count"] == 0
    assert s["error_count"] == 0
    assert s["shell_failure_count"] == 0


def test_align_tool_calls_identical_sequences():
    rows = align_tool_calls(["Read", "Bash", "Edit"], ["Read", "Bash", "Edit"])
    assert rows == [("Read", "Read"), ("Bash", "Bash"), ("Edit", "Edit")]


def test_align_tool_calls_disjoint_sequences():
    rows = align_tool_calls(["Read", "Bash"], ["Grep", "Write"])
    assert len(rows) == 2
    assert not any(a == b for a, b in rows if a is not None and b is not None)


def test_align_tool_calls_middle_insertion():
    rows = align_tool_calls(["Read", "Bash", "Edit"], ["Read", "Bash", "Grep", "Edit"])
    assert rows == [("Read", "Read"), ("Bash", "Bash"), (None, "Grep"), ("Edit", "Edit")]


def test_align_tool_calls_one_side_empty():
    rows = align_tool_calls([], ["Read", "Bash"])
    assert rows == [(None, "Read"), (None, "Bash")]


def test_align_tool_calls_both_empty():
    assert align_tool_calls([], []) == []
