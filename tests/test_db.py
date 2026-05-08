import sqlite3
import pytest
from pathlib import Path
from agent_flight_recorder.db import get_connection, init_db, upsert_session, list_runs, search_runs, set_outcome, get_run_events
from agent_flight_recorder.models import ParsedSession, Run, ToolCall, ShellCommand, FileEvent, Error


@pytest.fixture
def tmp_db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def _make_session(run_id="run-001", source="claude"):
    run = Run(id=run_id, source=source, user_goal="Fix the Modal bug", final_summary="Done")
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
