import pytest
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.analyzers.errors import get_recurring_errors
from agent_flight_recorder.models import ParsedSession, Run, ShellCommand


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    init_db(c)
    return c


def _seed_run(conn, run_id, started_at, shell_commands):
    upsert_session(conn, ParsedSession(
        run=Run(id=run_id, source="claude", started_at=started_at, ended_at=started_at),
        shell_commands=shell_commands,
    ))


def test_no_failures_returns_empty(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="ls", exit_code=0, timestamp="2026-07-20T10:00:00Z"),
    ])
    assert get_recurring_errors(conn) == []


def test_single_failure_below_min_count_excluded(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="pytest x", exit_code=1, timestamp="2026-07-20T10:00:00Z"),
    ])
    assert get_recurring_errors(conn, min_count=2) == []


def test_min_count_boundary_included(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="pytest x", exit_code=1, timestamp="2026-07-20T10:00:00Z"),
    ])
    _seed_run(conn, "run-2", "2026-07-21T10:00:00Z", [
        ShellCommand(id="sc-2", run_id="run-2", command="pytest x", exit_code=1, timestamp="2026-07-21T10:00:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2)
    assert len(entries) == 1
    assert entries[0]["count"] == 2
    assert entries[0]["command"] == "pytest x"
    assert entries[0]["exit_code"] == 1
    assert entries[0]["first_seen"] == "2026-07-20T10:00:00Z"
    assert entries[0]["last_seen"] == "2026-07-21T10:00:00Z"
    assert entries[0]["run_ids"] == ["run-1"[:8], "run-2"[:8]]


def test_same_command_different_exit_codes_kept_separate(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="npm run build", exit_code=1, timestamp="2026-07-20T10:00:00Z"),
        ShellCommand(id="sc-2", run_id="run-1", command="npm run build", exit_code=1, timestamp="2026-07-20T10:01:00Z"),
    ])
    _seed_run(conn, "run-2", "2026-07-21T10:00:00Z", [
        ShellCommand(id="sc-3", run_id="run-2", command="npm run build", exit_code=127, timestamp="2026-07-21T10:00:00Z"),
        ShellCommand(id="sc-4", run_id="run-2", command="npm run build", exit_code=127, timestamp="2026-07-21T10:01:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2)
    assert len(entries) == 2
    exit_codes = {e["exit_code"] for e in entries}
    assert exit_codes == {1, 127}


def test_run_ids_deduplicated_within_same_run(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="flaky", exit_code=1, timestamp="2026-07-20T10:00:00Z"),
        ShellCommand(id="sc-2", run_id="run-1", command="flaky", exit_code=1, timestamp="2026-07-20T10:01:00Z"),
    ])
    _seed_run(conn, "run-2", "2026-07-21T10:00:00Z", [
        ShellCommand(id="sc-3", run_id="run-2", command="flaky", exit_code=1, timestamp="2026-07-21T10:00:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2)
    assert entries[0]["count"] == 3
    assert entries[0]["run_ids"] == ["run-1"[:8], "run-2"[:8]]


def test_days_window_filters_by_run_started_at(conn):
    _seed_run(conn, "run-old-1", "2020-01-01T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-old-1", command="old fail", exit_code=1, timestamp="2020-01-01T10:00:00Z"),
    ])
    _seed_run(conn, "run-old-2", "2020-01-02T10:00:00Z", [
        ShellCommand(id="sc-2", run_id="run-old-2", command="old fail", exit_code=1, timestamp="2020-01-02T10:00:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2, days=7)
    assert entries == []


def test_sort_order_count_desc_then_last_seen_desc(conn):
    _seed_run(conn, "run-1", "2026-07-15T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="a", exit_code=1, timestamp="2026-07-15T10:00:00Z"),
        ShellCommand(id="sc-2", run_id="run-1", command="a", exit_code=1, timestamp="2026-07-15T10:01:00Z"),
    ])
    _seed_run(conn, "run-2", "2026-07-16T10:00:00Z", [
        ShellCommand(id="sc-3", run_id="run-2", command="b", exit_code=1, timestamp="2026-07-16T10:00:00Z"),
        ShellCommand(id="sc-4", run_id="run-2", command="b", exit_code=1, timestamp="2026-07-16T10:01:00Z"),
        ShellCommand(id="sc-5", run_id="run-2", command="b", exit_code=1, timestamp="2026-07-16T10:02:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2)
    assert entries[0]["command"] == "b"  # count 3 beats count 2
    assert entries[1]["command"] == "a"
