from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest
from agent_flight_recorder.analyzers.windows import (
    parse_started_at, reconstruct_windows,
    count_today, count_since,
    most_recent_reset, next_reset,
    availability,
    parse_weekly_reset, resolve_tz,
    build_report,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


# --- Task 2: parse + reconstruct ---

def test_parse_started_at():
    assert parse_started_at("2026-06-02T02:10:20.041Z") == datetime(2026, 6, 2, 2, 10, 20, 41000, tzinfo=UTC)
    assert parse_started_at("") is None
    assert parse_started_at("not-a-date") is None


def test_reconstruct_windows_basic():
    base = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    starts = [
        base,                                    # opens window 1
        base + timedelta(hours=1),               # attaches to window 1
        base + timedelta(hours=4, minutes=59),   # attaches to window 1
        base + timedelta(hours=5),               # exactly 5h -> opens window 2
        base + timedelta(hours=11),              # gap -> opens window 3
    ]
    anchors = reconstruct_windows(starts)
    assert anchors == [base, base + timedelta(hours=5), base + timedelta(hours=11)]


def test_reconstruct_windows_edges():
    assert reconstruct_windows([]) == []
    t = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    assert reconstruct_windows([t]) == [t]
    assert reconstruct_windows([t + timedelta(hours=6), t]) == [t, t + timedelta(hours=6)]


# --- Task 3: count today / since ---

def test_count_today_uses_local_day():
    anchors = [
        datetime(2026, 6, 2, 3, 0, tzinfo=UTC),    # Jun 1 local (NY)
        datetime(2026, 6, 2, 14, 0, tzinfo=UTC),   # Jun 2 local 10am
        datetime(2026, 6, 2, 20, 0, tzinfo=UTC),   # Jun 2 local 4pm
    ]
    now = datetime(2026, 6, 2, 21, 0, tzinfo=UTC)  # Jun 2 local
    assert count_today(anchors, now, NY) == 2
    assert count_today(anchors, now, UTC) == 3


def test_count_since():
    since = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
    anchors = [
        datetime(2026, 6, 1, 3, 0, tzinfo=UTC),   # before -> excluded
        datetime(2026, 6, 1, 4, 0, tzinfo=UTC),   # at -> included
        datetime(2026, 6, 2, 9, 0, tzinfo=UTC),   # included
    ]
    assert count_since(anchors, since) == 2


# --- Task 4: reset math ---

def test_reset_math_weekday_and_time():
    now = datetime(2026, 6, 2, 21, 0, tzinfo=UTC)  # Tue Jun 2, 5pm NY
    mr = most_recent_reset(now, weekday=2, hhmm=(0, 0), tz=NY)
    nr = next_reset(now, weekday=2, hhmm=(0, 0), tz=NY)
    assert mr == datetime(2026, 5, 27, 0, 0, tzinfo=NY).astimezone(UTC)
    assert nr == datetime(2026, 6, 3, 0, 0, tzinfo=NY).astimezone(UTC)
    assert nr - mr == timedelta(days=7)


def test_reset_same_weekday_before_time_uses_prior_week():
    now = datetime(2026, 6, 3, 3, 0, tzinfo=UTC)  # Tue Jun 2 11pm NY
    mr = most_recent_reset(now, weekday=2, hhmm=(0, 0), tz=NY)
    assert mr == datetime(2026, 5, 27, 0, 0, tzinfo=NY).astimezone(UTC)


# --- Task 5: availability ---

def test_availability_no_active_window():
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    reset = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)  # 12h away
    anchors = [datetime(2026, 6, 2, 0, 0, tzinfo=UTC)]  # ended 05:00, not active
    a = availability(anchors, now, reset)
    assert a["active_remaining_h"] == 0.0
    assert a["full_windows"] == 2
    assert round(a["tail_h"], 2) == 2.0


def test_availability_with_active_window():
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    reset = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)
    anchors = [datetime(2026, 6, 2, 10, 0, tzinfo=UTC)]  # ends 15:00 -> active, 3h left
    a = availability(anchors, now, reset)
    assert a["active_remaining_h"] == 3.0
    assert a["full_windows"] == 1
    assert round(a["tail_h"], 2) == 4.0


def test_availability_past_reset():
    now = datetime(2026, 6, 3, 1, 0, tzinfo=UTC)
    reset = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)
    a = availability([], now, reset)
    assert a == {"active_remaining_h": 0.0, "full_windows": 0, "tail_h": 0.0}


# --- Task 6: config value parsing ---

def test_parse_weekly_reset():
    assert parse_weekly_reset("Wed 00:00") == (2, (0, 0))
    assert parse_weekly_reset("wednesday 9:30") == (2, (9, 30))
    assert parse_weekly_reset("MON 23:15") == (0, (23, 15))
    with pytest.raises(ValueError):
        parse_weekly_reset("Funday 00:00")
    with pytest.raises(ValueError):
        parse_weekly_reset("Wed")
    with pytest.raises(ValueError):
        parse_weekly_reset("Wed 25:00")


def test_resolve_tz_falls_back_to_utc():
    assert resolve_tz("America/New_York").key == "America/New_York"
    assert resolve_tz(None) == UTC
    assert resolve_tz("Not/AZone") == UTC


# --- Task 7: build_report ---

def _starts():
    return [
        "2026-06-02T03:00:00.000Z",   # Jun 1 NY
        "2026-06-02T14:00:00.000Z",   # Jun 2 NY 10am
        "2026-06-02T20:00:00.000Z",   # Jun 2 NY 4pm
        "garbage",                    # skipped
        "",                           # skipped
    ]


def test_build_report_with_config():
    now = datetime(2026, 6, 2, 21, 0, tzinfo=UTC)  # Jun 2 NY 5pm
    cfg = {"timezone": "America/New_York", "weekly_reset_weekday": "2", "weekly_reset_time": "00:00"}
    r = build_report(_starts(), cfg, now)
    assert r["reset_configured"] is True
    assert r["today"] == 2
    assert r["week"] >= 2
    assert r["tz_label"] == "America/New_York"
    assert "full_windows" in r["available"]


def test_build_report_without_reset_config():
    now = datetime(2026, 6, 2, 21, 0, tzinfo=UTC)
    r = build_report(_starts(), {"timezone": "America/New_York"}, now)
    assert r["reset_configured"] is False
    assert r["today"] == 2
    assert r["week"] is None
    assert r["available"] is None


# --- Task 8: render ---

def test_print_windows_smoke(capsys):
    from agent_flight_recorder.render.terminal import print_windows
    report = {
        "tz_label": "America/New_York", "today": 2, "total_windows": 7,
        "reset_configured": True, "week": 7,
        "available": {"active_remaining_h": 3.0, "full_windows": 2, "tail_h": 0.4},
        "next_reset_local": datetime(2026, 6, 3, 0, 0, tzinfo=NY),
    }
    print_windows(report)
    out = capsys.readouterr().out
    assert "Today" in out and "2" in out
    assert "available" in out.lower() or "Available" in out

    report2 = dict(report, reset_configured=False, week=None, available=None, next_reset_local=None)
    print_windows(report2)
    out2 = capsys.readouterr().out
    assert "afr config set" in out2


# --- Task 9: CLI ---

def test_cli_config_and_windows(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from agent_flight_recorder.cli import app
    from agent_flight_recorder import db as afrdb
    runner = CliRunner()
    test_db = tmp_path / "afr.db"
    monkeypatch.setattr(afrdb, "DB_PATH", test_db)
    res = runner.invoke(app, ["config", "set", "weekly-reset", "Wed 00:00"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["config", "set", "timezone", "America/New_York"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["config", "set", "weekly-reset", "Funday"])
    assert res.exit_code != 0
    res = runner.invoke(app, ["config", "show"])
    assert "America/New_York" in res.output
    res = runner.invoke(app, ["windows"])
    assert res.exit_code == 0, res.output
    assert "5-hour windows" in res.output or "Today" in res.output
