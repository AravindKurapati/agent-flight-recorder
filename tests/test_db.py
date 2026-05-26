import sqlite3
import pytest
from pathlib import Path
from agent_flight_recorder.db import get_connection, init_db, upsert_session, list_runs, search_runs, set_outcome, get_run_events, get_latest_run
from agent_flight_recorder.models import ParsedSession, Run, ToolCall, ShellCommand, FileEvent, Error


@pytest.fixture
def tmp_db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def _make_session(run_id="run-001", source="claude", project_path="", started_at="", ended_at=""):
    run = Run(id=run_id, source=source, user_goal="Fix the Modal bug", final_summary="Done",
              project_path=project_path, started_at=started_at, ended_at=ended_at)
    tc = ToolCall(id="tc-001", run_id=run_id, tool_name="Bash", input_summary="modal run", status="success", timestamp="2026-05-01T10:00:00Z")
    sc = ShellCommand(id="sc-001", run_id=run_id, command="modal run finsight.py", exit_code=0, timestamp="2026-05-01T10:00:00Z")
    fe = FileEvent(id="fe-001", run_id=run_id, path="finsight.py", action="read")
    err = Error(id="err-001", run_id=run_id, source="tool", message="missing secret", timestamp="2026-05-01T10:00:00Z")
    return ParsedSession(run=run, tool_calls=[tc], shell_commands=[sc], files=[fe], errors=[err])


def test_upsert_new_session(tmp_db):
    session = _make_session()
    result = upsert_session(tmp_db, session)
    assert result is True


def test_upsert_idempotent(tmp_db):
    session = _make_session()
    upsert_session(tmp_db, session)
    result = upsert_session(tmp_db, session)
    assert result is False


def test_list_runs_returns_inserted(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    upsert_session(tmp_db, _make_session("run-002"))
    runs = list_runs(tmp_db)
    assert len(runs) == 2


def test_search_runs_fts(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    results = search_runs(tmp_db, "Modal")
    assert len(results) == 1


def test_set_outcome(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    set_outcome(tmp_db, "run-001", "shipped")
    runs = list_runs(tmp_db)
    assert runs[0]["outcome"] == "shipped"


def test_get_run_events(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    events = get_run_events(tmp_db, "run-001")
    assert len(events["tool_calls"]) == 1
    assert len(events["shell_commands"]) == 1
    assert len(events["files"]) == 1
    assert len(events["errors"]) == 1


def test_get_latest_run_picks_most_recent_by_ended_at(tmp_db):
    upsert_session(tmp_db, _make_session("run-old", started_at="2026-05-01T10:00:00Z", ended_at="2026-05-01T11:00:00Z"))
    upsert_session(tmp_db, _make_session("run-new", started_at="2026-05-02T09:00:00Z", ended_at="2026-05-02T09:30:00Z"))
    row = get_latest_run(tmp_db)
    assert row["id"] == "run-new"


def test_get_latest_run_prefers_ended_at_over_started_at(tmp_db):
    # run-A started first but is still active (ended_at empty); run-B started later and finished.
    # last-active semantics: run-A wins because it has no end yet — but with empty ended_at it falls
    # back to started_at, so run-B (later start) wins. This documents the fallback behavior.
    upsert_session(tmp_db, _make_session("run-A", started_at="2026-05-01T10:00:00Z", ended_at=""))
    upsert_session(tmp_db, _make_session("run-B", started_at="2026-05-01T12:00:00Z", ended_at="2026-05-01T13:00:00Z"))
    row = get_latest_run(tmp_db)
    assert row["id"] == "run-B"


def test_get_latest_run_filters_by_cwd_match(tmp_db):
    upsert_session(tmp_db, _make_session("run-other", project_path="D--some-other-project",
                                          started_at="2026-05-02T10:00:00Z", ended_at="2026-05-02T11:00:00Z"))
    upsert_session(tmp_db, _make_session("run-mine", project_path="D--Aru-NYU-agent-flight-recorder",
                                          started_at="2026-05-01T10:00:00Z", ended_at="2026-05-01T11:00:00Z"))
    # Most recent overall is run-other, but cwd filter picks run-mine.
    row = get_latest_run(tmp_db, cwd_match="agent-flight-recorder")
    assert row["id"] == "run-mine"


def test_get_latest_run_returns_none_when_empty(tmp_db):
    assert get_latest_run(tmp_db) is None
    assert get_latest_run(tmp_db, cwd_match="nope") is None
