import sqlite3
import pytest
from pathlib import Path
from agent_flight_recorder.db import get_connection, init_db, upsert_session, list_runs, search_runs, set_outcome, get_run_events, get_latest_run, bulk_set_outcome, count_runs_for_bulk
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


def test_list_runs_respects_limit(tmp_db):
    upsert_session(tmp_db, _make_session("run-1", started_at="2026-05-01T10:00:00Z"))
    upsert_session(tmp_db, _make_session("run-2", started_at="2026-05-02T10:00:00Z"))
    upsert_session(tmp_db, _make_session("run-3", started_at="2026-05-03T10:00:00Z"))
    runs = list_runs(tmp_db, limit=2)
    assert [r["id"] for r in runs] == ["run-3", "run-2"]


def test_list_runs_limit_zero_returns_all(tmp_db):
    # Defensive: limit<=0 should not apply LIMIT (matches existing days=None semantics).
    upsert_session(tmp_db, _make_session("run-1"))
    upsert_session(tmp_db, _make_session("run-2"))
    assert len(list_runs(tmp_db, limit=0)) == 2


def test_search_runs_fts(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    results = search_runs(tmp_db, "Modal")
    assert len(results) == 1


def test_search_runs_orders_by_started_at_desc(tmp_db):
    upsert_session(tmp_db, _make_session("run-old", started_at="2026-05-01T09:00:00Z", ended_at="2026-05-01T10:00:00Z"))
    upsert_session(tmp_db, _make_session("run-mid", started_at="2026-05-03T09:00:00Z", ended_at="2026-05-03T10:00:00Z"))
    upsert_session(tmp_db, _make_session("run-new", started_at="2026-05-05T09:00:00Z", ended_at="2026-05-05T10:00:00Z"))
    results = search_runs(tmp_db, "Modal")
    assert [r["id"] for r in results] == ["run-new", "run-mid", "run-old"]


def test_set_outcome(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    set_outcome(tmp_db, "run-001", "shipped")
    runs = list_runs(tmp_db)
    assert runs[0]["outcome"] == "shipped"


def test_set_outcome_with_note(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    set_outcome(tmp_db, "run-001", "shipped", note="merged as PR #42")
    row = list_runs(tmp_db)[0]
    assert row["outcome"] == "shipped"
    assert row["tag_note"] == "merged as PR #42"


def test_set_outcome_no_note_preserves_existing(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    set_outcome(tmp_db, "run-001", "shipped", note="initial note")
    set_outcome(tmp_db, "run-001", "blocked")  # no note arg — should keep the note
    row = list_runs(tmp_db)[0]
    assert row["outcome"] == "blocked"
    assert row["tag_note"] == "initial note"


def test_set_outcome_empty_note_clears(tmp_db):
    upsert_session(tmp_db, _make_session("run-001"))
    set_outcome(tmp_db, "run-001", "shipped", note="initial")
    set_outcome(tmp_db, "run-001", "shipped", note="")
    row = list_runs(tmp_db)[0]
    assert row["tag_note"] == ""


def test_bulk_set_outcome_untagged_only(tmp_db):
    upsert_session(tmp_db, _make_session("run-1"))
    upsert_session(tmp_db, _make_session("run-2"))
    upsert_session(tmp_db, _make_session("run-3"))
    set_outcome(tmp_db, "run-2", "shipped")  # already tagged — must be skipped
    n = bulk_set_outcome(tmp_db, "abandoned", untagged_only=True)
    assert n == 2
    outcomes = {r["id"]: r["outcome"] for r in list_runs(tmp_db)}
    assert outcomes["run-1"] == "abandoned"
    assert outcomes["run-3"] == "abandoned"
    assert outcomes["run-2"] == "shipped"  # preserved


def test_bulk_set_outcome_older_than(tmp_db):
    upsert_session(tmp_db, _make_session("run-old", started_at="2020-01-01T00:00:00Z"))
    upsert_session(tmp_db, _make_session("run-new", started_at="2099-01-01T00:00:00Z"))
    n = bulk_set_outcome(tmp_db, "abandoned", older_than_days=30)
    assert n == 1
    outcomes = {r["id"]: r["outcome"] for r in list_runs(tmp_db)}
    assert outcomes["run-old"] == "abandoned"
    assert outcomes["run-new"] == "untagged"


def test_bulk_set_outcome_combined_filters(tmp_db):
    upsert_session(tmp_db, _make_session("run-old-untagged", started_at="2020-01-01T00:00:00Z"))
    upsert_session(tmp_db, _make_session("run-old-tagged", started_at="2020-01-01T00:00:00Z"))
    upsert_session(tmp_db, _make_session("run-new-untagged", started_at="2099-01-01T00:00:00Z"))
    set_outcome(tmp_db, "run-old-tagged", "shipped")
    n = bulk_set_outcome(tmp_db, "abandoned", untagged_only=True, older_than_days=30)
    assert n == 1
    outcomes = {r["id"]: r["outcome"] for r in list_runs(tmp_db)}
    assert outcomes["run-old-untagged"] == "abandoned"
    assert outcomes["run-old-tagged"] == "shipped"
    assert outcomes["run-new-untagged"] == "untagged"


def test_bulk_set_outcome_with_note(tmp_db):
    upsert_session(tmp_db, _make_session("run-1"))
    bulk_set_outcome(tmp_db, "abandoned", untagged_only=True, note="bulk cleanup 2026-05")
    assert list_runs(tmp_db)[0]["tag_note"] == "bulk cleanup 2026-05"


def test_count_runs_for_bulk_matches_update(tmp_db):
    upsert_session(tmp_db, _make_session("run-1", started_at="2020-01-01T00:00:00Z"))
    upsert_session(tmp_db, _make_session("run-2", started_at="2099-01-01T00:00:00Z"))
    assert count_runs_for_bulk(tmp_db, untagged_only=True, older_than_days=30) == 1
    assert count_runs_for_bulk(tmp_db, untagged_only=True) == 2
    assert count_runs_for_bulk(tmp_db) == 2


def test_init_db_idempotent_adds_tag_note_to_old_schema(tmp_path):
    # Simulate a pre-tag_note DB by creating the table without the column, then re-init.
    import sqlite3
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    raw.execute("""
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, source TEXT NOT NULL,
            project_path TEXT DEFAULT '', started_at TEXT DEFAULT '', ended_at TEXT DEFAULT '',
            user_goal TEXT DEFAULT '', final_summary TEXT DEFAULT '',
            outcome TEXT DEFAULT 'untagged', cost_usd REAL DEFAULT 0.0,
            tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
            cache_read INTEGER DEFAULT 0, cache_write INTEGER DEFAULT 0
        )
    """)
    raw.execute("INSERT INTO runs (id, source) VALUES ('run-x', 'claude')")
    raw.commit()
    raw.close()

    from agent_flight_recorder.db import init_db, get_connection
    conn = get_connection(db_path)
    init_db(conn)  # should add tag_note column without error
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    assert "tag_note" in cols
    row = conn.execute("SELECT tag_note FROM runs WHERE id='run-x'").fetchone()
    assert row["tag_note"] == ""
    # Running init_db twice should still be safe.
    init_db(conn)
    conn.close()


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
