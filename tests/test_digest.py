import pytest
from datetime import datetime, timezone
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.analyzers.digest import get_digest
from agent_flight_recorder.models import ParsedSession, Run


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    init_db(c)
    return c


def _run(id, outcome, cwd="", cost=1.0, tokens_in=100, tokens_out=50,
         started_at="2026-07-20T10:00:00Z", goal="do thing", summary="did thing"):
    return ParsedSession(run=Run(
        id=id, source="claude", outcome=outcome, cwd=cwd,
        cost_usd=cost, tokens_in=tokens_in, tokens_out=tokens_out,
        started_at=started_at, ended_at=started_at, user_goal=goal, final_summary=summary,
    ))


def test_digest_empty_db_returns_zeroed_shape(conn):
    d = get_digest(conn, days=7)
    assert d["total_runs"] == 0
    assert d["total_cost_usd"] == 0.0
    assert d["outcomes"] == {}
    assert d["cost_per_shipped"] is None
    assert d["abandoned_streak"] == 0
    assert d["by_project"] == {}
    assert d["sessions"] == []


def test_digest_totals_and_outcomes(conn):
    upsert_session(conn, _run("run-1", "shipped", cost=2.0))
    upsert_session(conn, _run("run-2", "abandoned", cost=1.0))
    d = get_digest(conn, days=7)
    assert d["total_runs"] == 2
    assert d["total_cost_usd"] == 3.0
    assert d["outcomes"] == {"shipped": 1, "abandoned": 1}


def test_digest_cost_per_shipped(conn):
    upsert_session(conn, _run("run-1", "shipped", cost=2.0))
    upsert_session(conn, _run("run-2", "shipped", cost=4.0))
    upsert_session(conn, _run("run-3", "abandoned", cost=1.0))
    d = get_digest(conn, days=7)
    assert d["cost_per_shipped"] == pytest.approx(7.0 / 2)


def test_digest_cost_per_shipped_none_when_zero_shipped(conn):
    upsert_session(conn, _run("run-1", "abandoned"))
    d = get_digest(conn, days=7)
    assert d["cost_per_shipped"] is None


def test_digest_abandoned_streak_from_most_recent(conn):
    upsert_session(conn, _run("run-1", "shipped", started_at="2026-07-20T10:00:00Z"))
    upsert_session(conn, _run("run-2", "abandoned", started_at="2026-07-21T10:00:00Z"))
    upsert_session(conn, _run("run-3", "blocked", started_at="2026-07-22T10:00:00Z"))
    d = get_digest(conn, days=7)
    assert d["abandoned_streak"] == 2


def test_digest_abandoned_streak_zero_when_latest_is_shipped(conn):
    upsert_session(conn, _run("run-1", "abandoned", started_at="2026-07-20T10:00:00Z"))
    upsert_session(conn, _run("run-2", "shipped", started_at="2026-07-21T10:00:00Z"))
    d = get_digest(conn, days=7)
    assert d["abandoned_streak"] == 0


def test_digest_by_project_groups_by_cwd_basename(conn):
    upsert_session(conn, _run("run-1", "shipped", cwd=r"D:\Aru\NYU\agent-flight-recorder", cost=1.0))
    upsert_session(conn, _run("run-2", "abandoned", cwd=r"D:\Aru\NYU\agent-flight-recorder", cost=2.0))
    upsert_session(conn, _run("run-3", "shipped", cwd="/home/user/locus", cost=3.0))
    d = get_digest(conn, days=7)
    assert d["by_project"]["agent-flight-recorder"]["runs"] == 2
    assert d["by_project"]["agent-flight-recorder"]["cost_usd"] == pytest.approx(3.0)
    assert d["by_project"]["locus"]["runs"] == 1


def test_digest_by_project_empty_cwd_bucketed_as_unknown(conn):
    upsert_session(conn, _run("run-1", "shipped", cwd=""))
    d = get_digest(conn, days=7)
    assert d["by_project"]["unknown"]["runs"] == 1


def test_digest_sessions_ordered_newest_first(conn):
    upsert_session(conn, _run("run-1", "shipped", started_at="2026-07-20T10:00:00Z"))
    upsert_session(conn, _run("run-2", "shipped", started_at="2026-07-22T10:00:00Z"))
    d = get_digest(conn, days=7)
    assert [s["id"] for s in d["sessions"]] == ["run-2"[:8], "run-1"[:8]]


def test_digest_period_since_uses_injected_now(conn):
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    d = get_digest(conn, days=7, now=now)
    assert d["period_since"] == "2026-07-16T12:00:00+00:00"
    assert d["period_days"] == 7
