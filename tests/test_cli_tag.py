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


def _seed_for_bulk(db_path):
    conn = get_connection(db_path)
    upsert_session(conn, ParsedSession(run=Run(id="run-old-1", source="claude", user_goal="g",
                                                started_at="2020-01-01T00:00:00Z", ended_at="2020-01-01T01:00:00Z")))
    upsert_session(conn, ParsedSession(run=Run(id="run-old-2", source="claude", user_goal="g",
                                                started_at="2020-01-02T00:00:00Z", ended_at="2020-01-02T01:00:00Z")))
    upsert_session(conn, ParsedSession(run=Run(id="run-new-1", source="claude", user_goal="g",
                                                started_at="2099-01-01T00:00:00Z", ended_at="2099-01-01T01:00:00Z")))
    conn.close()


def test_bulk_tag_requires_confirmation(tmp_db):
    _seed_for_bulk(tmp_db)
    # Decline the prompt — no rows should be updated.
    result = CliRunner().invoke(app, ["tag", "--untagged", "abandoned"], input="n\n")
    assert result.exit_code == 1
    assert "will tag 3" in result.output
    assert "Cancelled" in result.output
    conn = get_connection(tmp_db)
    outcomes = [r["outcome"] for r in conn.execute("SELECT outcome FROM runs").fetchall()]
    conn.close()
    assert all(o == "untagged" for o in outcomes)


def test_bulk_tag_with_yes_skips_prompt(tmp_db):
    _seed_for_bulk(tmp_db)
    result = CliRunner().invoke(app, ["tag", "--untagged", "abandoned", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Tagged 3" in result.output
    conn = get_connection(tmp_db)
    outcomes = [r["outcome"] for r in conn.execute("SELECT outcome FROM runs").fetchall()]
    conn.close()
    assert all(o == "abandoned" for o in outcomes)


def test_bulk_tag_older_than(tmp_db):
    _seed_for_bulk(tmp_db)
    result = CliRunner().invoke(app, ["tag", "--untagged", "--older-than", "30d", "abandoned", "-y"])
    assert result.exit_code == 0, result.output
    assert "Tagged 2" in result.output
    conn = get_connection(tmp_db)
    outcomes = {r["id"]: r["outcome"] for r in conn.execute("SELECT id, outcome FROM runs").fetchall()}
    conn.close()
    assert outcomes["run-old-1"] == "abandoned"
    assert outcomes["run-old-2"] == "abandoned"
    assert outcomes["run-new-1"] == "untagged"


def test_bulk_tag_no_matches(tmp_db):
    result = CliRunner().invoke(app, ["tag", "--untagged", "abandoned", "-y"])
    assert result.exit_code == 0
    assert "nothing to do" in result.output


def test_bulk_tag_rejects_run_id(tmp_db):
    _seed_for_bulk(tmp_db)
    result = CliRunner().invoke(app, ["tag", "deadbeef", "--untagged", "abandoned", "-y"])
    assert result.exit_code == 1
    assert "does not accept a run ID" in result.output


def test_bulk_tag_rejects_invalid_duration(tmp_db):
    _seed_for_bulk(tmp_db)
    result = CliRunner().invoke(app, ["tag", "--untagged", "--older-than", "bananas", "abandoned", "-y"])
    assert result.exit_code == 1
    assert "Invalid --older-than" in result.output


def test_bulk_tag_with_note(tmp_db):
    _seed_for_bulk(tmp_db)
    result = CliRunner().invoke(app, ["tag", "--untagged", "abandoned", "-y", "--note", "auto-cleanup"])
    assert result.exit_code == 0, result.output
    conn = get_connection(tmp_db)
    notes = [r["tag_note"] for r in conn.execute("SELECT tag_note FROM runs").fetchall()]
    conn.close()
    assert all(n == "auto-cleanup" for n in notes)


def test_bulk_tag_duration_units(tmp_db):
    # Make all three runs ancient so any unit picks them up.
    conn = get_connection(tmp_db)
    upsert_session(conn, ParsedSession(run=Run(id="run-x", source="claude", user_goal="g",
                                                started_at="2010-01-01T00:00:00Z", ended_at="2010-01-01T01:00:00Z")))
    conn.close()
    for spec, expected_label in [("7d", "7d"), ("1w", "7d"), ("1m", "30d")]:
        # Reset
        conn = get_connection(tmp_db)
        conn.execute("UPDATE runs SET outcome='untagged'")
        conn.commit()
        conn.close()
        result = CliRunner().invoke(app, ["tag", "--untagged", "--older-than", spec, "abandoned", "-y"])
        assert result.exit_code == 0, result.output
        assert "Tagged 1" in result.output


def _seed_two_matching(db_path):
    conn = get_connection(db_path)
    upsert_session(conn, ParsedSession(run=Run(
        id="run-aaaa-1111", source="claude", user_goal="medium effort refactor",
        started_at="2026-05-01T10:00:00Z", ended_at="2026-05-01T11:00:00Z",
    )))
    upsert_session(conn, ParsedSession(run=Run(
        id="run-bbbb-2222", source="claude", user_goal="medium priority bug",
        started_at="2026-05-02T10:00:00Z", ended_at="2026-05-02T11:00:00Z",
    )))
    conn.close()


def _tagged_outcomes(db_path):
    conn = get_connection(db_path)
    out = {r["id"]: r["outcome"] for r in list_runs(conn)}
    conn.close()
    return out


def test_tag_picker_selects_first_match(tmp_db):
    _seed_two_matching(tmp_db)
    result = CliRunner().invoke(app, ["tag", "medium", "shipped"], input="1\n")
    assert result.exit_code == 0, result.output
    assert "Pick one" in result.output
    outcomes = _tagged_outcomes(tmp_db)
    # Exactly one tagged — index 1 maps to whichever row the picker listed first.
    assert sum(1 for v in outcomes.values() if v == "shipped") == 1
    assert sum(1 for v in outcomes.values() if v == "untagged") == 1


def test_tag_picker_selects_second_match(tmp_db):
    _seed_two_matching(tmp_db)
    result1 = CliRunner().invoke(app, ["tag", "medium", "shipped"], input="1\n")
    first_pick = next(rid for rid, o in _tagged_outcomes(tmp_db).items() if o == "shipped")

    # Reset and pick #2 this time — must be the other run.
    conn = get_connection(tmp_db)
    conn.execute("UPDATE runs SET outcome='untagged'")
    conn.commit()
    conn.close()

    result2 = CliRunner().invoke(app, ["tag", "medium", "shipped"], input="2\n")
    assert result2.exit_code == 0, result2.output
    second_pick = next(rid for rid, o in _tagged_outcomes(tmp_db).items() if o == "shipped")
    assert second_pick != first_pick


def test_tag_picker_quit_cancels(tmp_db):
    _seed_two_matching(tmp_db)
    result = CliRunner().invoke(app, ["tag", "medium", "shipped"], input="q\n")
    assert result.exit_code == 1
    assert "Cancelled" in result.output
    conn = get_connection(tmp_db)
    runs = {r["id"]: r["outcome"] for r in list_runs(conn)}
    conn.close()
    assert all(o == "untagged" for o in runs.values())


def test_tag_picker_rejects_invalid_then_accepts(tmp_db):
    _seed_two_matching(tmp_db)
    # First "9" is out of range, then "1" picks the first match.
    result = CliRunner().invoke(app, ["tag", "medium", "shipped"], input="9\n1\n")
    assert result.exit_code == 0, result.output
    assert "Invalid choice" in result.output


def test_tag_picker_empty_input_cancels(tmp_db):
    _seed_two_matching(tmp_db)
    # No input → EOF → cancel without crashing.
    result = CliRunner().invoke(app, ["tag", "medium", "shipped"], input="")
    assert result.exit_code == 1
    assert "Cancelled" in result.output


def test_tag_with_note_stores_note(tmp_db):
    conn = get_connection(tmp_db)
    upsert_session(conn, ParsedSession(run=Run(id="abcdef1234567890", source="claude", user_goal="g")))
    conn.close()
    result = CliRunner().invoke(app, ["tag", "abcdef12", "shipped", "--note", "merged as PR #42"])
    assert result.exit_code == 0, result.output
    conn = get_connection(tmp_db)
    row = conn.execute("SELECT outcome, tag_note FROM runs WHERE id='abcdef1234567890'").fetchone()
    conn.close()
    assert row["outcome"] == "shipped"
    assert row["tag_note"] == "merged as PR #42"


def test_tag_latest_with_note(tmp_db, tmp_path):
    from unittest.mock import patch
    conn = get_connection(tmp_db)
    upsert_session(conn, ParsedSession(run=Run(
        id="run-latest", source="claude", project_path="myproj",
        started_at="2026-05-02T09:00:00Z", ended_at="2026-05-02T10:00:00Z",
    )))
    conn.close()
    cwd = tmp_path / "myproj"
    cwd.mkdir()
    with patch("agent_flight_recorder.cli.Path") as mock_path:
        mock_path.cwd.return_value = cwd
        result = CliRunner().invoke(app, ["tag", "--latest", "shipped", "-n", "shipped via PR"])
    assert result.exit_code == 0, result.output
    conn = get_connection(tmp_db)
    row = conn.execute("SELECT tag_note FROM runs WHERE id='run-latest'").fetchone()
    conn.close()
    assert row["tag_note"] == "shipped via PR"


def test_tag_single_match_no_picker(tmp_db):
    conn = get_connection(tmp_db)
    upsert_session(conn, ParsedSession(run=Run(
        id="run-solo-9999", source="claude", user_goal="unique-marker-xyz",
    )))
    conn.close()
    result = CliRunner().invoke(app, ["tag", "unique-marker-xyz", "shipped"])
    assert result.exit_code == 0, result.output
    assert "Pick one" not in result.output
