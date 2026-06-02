# Session Windows (`afr windows`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `afr windows` to report 5-hour usage windows actually used today/this week (reconstructed from recorded run timestamps) and how many fresh windows fit before the weekly reset, plus `afr config` to store the reset time.

**Architecture:** A pure-logic analyzer (`analyzers/windows.py`) reconstructs windows from `runs.started_at` via greedy sessionization and computes the counts; all functions take `now`/inputs as parameters for deterministic tests. A new key-value `config` table (helpers in `db.py`) stores the weekly reset and timezone. `render/terminal.py` prints the panel; `cli.py` wires the `windows` command and a `config` sub-app.

**Tech Stack:** Python 3.11+, Typer, Rich, SQLite (stdlib `sqlite3`), stdlib `zoneinfo` (+ `tzdata` for Windows), pytest.

---

## File Structure

- Create: `agent_flight_recorder/analyzers/windows.py` — pure window logic (parse, reconstruct, count, reset math, availability, report builder). No DB or I/O.
- Modify: `agent_flight_recorder/db.py` — add `config` table to `init_db`; add `get_config` / `set_config` / `get_all_config`.
- Modify: `agent_flight_recorder/render/terminal.py` — add `print_windows(report)`.
- Modify: `agent_flight_recorder/cli.py` — add `windows` command and `config` Typer sub-app; wire imports.
- Modify: `pyproject.toml` — add `tzdata` dependency (Windows zoneinfo).
- Create: `tests/test_config.py` — config table + kv helpers + value parsing.
- Create: `tests/test_windows.py` — reconstruction, counts, reset math, availability, report.
- Modify: `README.md` — document `afr windows` and `afr config`.

Conventions: package dir is `agent_flight_recorder/`; tests run via `pytest` (pyproject sets `pythonpath=.`). DB at `~/.afr/afr.db`; tests use a temp DB via `get_connection(tmp_path/"afr.db")`. Never DROP tables; `config` is added with `CREATE TABLE IF NOT EXISTS`.

---

## Task 1: `config` table + key-value helpers

**Files:**
- Modify: `agent_flight_recorder/db.py` (add table to `init_db`; add three helpers)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from agent_flight_recorder.db import get_connection, init_db, get_config, set_config, get_all_config


def test_config_roundtrip(tmp_path):
    conn = get_connection(tmp_path / "afr.db")
    init_db(conn)
    assert get_config(conn, "timezone") is None
    assert get_config(conn, "timezone", "UTC") == "UTC"
    set_config(conn, "timezone", "America/New_York")
    assert get_config(conn, "timezone") == "America/New_York"
    set_config(conn, "timezone", "Europe/London")  # upsert
    assert get_config(conn, "timezone") == "Europe/London"
    set_config(conn, "weekly_reset_weekday", "2")
    assert get_all_config(conn) == {
        "timezone": "Europe/London",
        "weekly_reset_weekday": "2",
    }
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_config_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'get_config'`.

- [ ] **Step 3: Implement minimal code**

In `db.py`, inside the `init_db` `executescript("""..."""")` block, add this table next to the others (before the closing `"""`):

```sql
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
```

Then add these functions at module level in `db.py`:

```python
def get_config(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO config(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_all_config(conn: sqlite3.Connection) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM config").fetchall()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_config_roundtrip -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/db.py tests/test_config.py
git commit -m "feat(db): add config key-value table and helpers"
```

---

## Task 2: Parse timestamps + reconstruct 5-hour windows

**Files:**
- Create: `agent_flight_recorder/analyzers/windows.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_windows.py
from datetime import datetime, timedelta, timezone
from agent_flight_recorder.analyzers.windows import parse_started_at, reconstruct_windows

UTC = timezone.utc


def test_parse_started_at():
    assert parse_started_at("2026-06-02T02:10:20.041Z") == datetime(2026, 6, 2, 2, 10, 20, 41000, tzinfo=UTC)
    assert parse_started_at("") is None
    assert parse_started_at("not-a-date") is None


def test_reconstruct_windows_basic():
    base = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    starts = [
        base,                              # opens window 1
        base + timedelta(hours=1),         # attaches to window 1
        base + timedelta(hours=4, minutes=59),  # attaches to window 1
        base + timedelta(hours=5),         # exactly 5h -> opens window 2
        base + timedelta(hours=11),        # gap -> opens window 3
    ]
    anchors = reconstruct_windows(starts)
    assert anchors == [base, base + timedelta(hours=5), base + timedelta(hours=11)]


def test_reconstruct_windows_edges():
    assert reconstruct_windows([]) == []
    t = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    assert reconstruct_windows([t]) == [t]
    # unsorted input is handled
    assert reconstruct_windows([t + timedelta(hours=6), t]) == [t, t + timedelta(hours=6)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_windows.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_flight_recorder.analyzers.windows'`.

- [ ] **Step 3: Implement minimal code**

```python
# agent_flight_recorder/analyzers/windows.py
"""Reconstruct Anthropic 5-hour usage windows from recorded run timestamps.

Pure logic only: no DB access, no I/O. Every function takes its inputs (and
`now` where relevant) as parameters so tests are deterministic.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

WINDOW = timedelta(hours=5)


def parse_started_at(s: str) -> Optional[datetime]:
    """Parse afr's ISO8601 UTC timestamps (e.g. '2026-06-02T02:10:20.041Z')."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def reconstruct_windows(starts: list[datetime]) -> list[datetime]:
    """Greedy sessionization: a window opens on the first activity and lasts 5h.

    Returns the list of window anchors (start instants), ascending. A run at or
    after `anchor + 5h` opens a new window; earlier runs attach to the open one.
    """
    anchors: list[datetime] = []
    for t in sorted(starts):
        if not anchors or t >= anchors[-1] + WINDOW:
            anchors.append(t)
    return anchors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_windows.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/analyzers/windows.py tests/test_windows.py
git commit -m "feat(windows): parse timestamps and reconstruct 5h windows"
```

---

## Task 3: Count windows used today / since an instant

**Files:**
- Modify: `agent_flight_recorder/analyzers/windows.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_windows.py
from zoneinfo import ZoneInfo
from agent_flight_recorder.analyzers.windows import count_today, count_since

NY = ZoneInfo("America/New_York")


def test_count_today_uses_local_day():
    # 03:00 UTC on Jun 2 is still Jun 1 (11:00pm) in New York
    anchors = [
        datetime(2026, 6, 2, 3, 0, tzinfo=UTC),    # Jun 1 local
        datetime(2026, 6, 2, 14, 0, tzinfo=UTC),   # Jun 2 local (10am)
        datetime(2026, 6, 2, 20, 0, tzinfo=UTC),   # Jun 2 local (4pm)
    ]
    now = datetime(2026, 6, 2, 21, 0, tzinfo=UTC)  # Jun 2 local
    assert count_today(anchors, now, NY) == 2
    assert count_today(anchors, now, UTC) == 3     # all Jun 2 in UTC


def test_count_since():
    since = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
    anchors = [
        datetime(2026, 6, 1, 3, 0, tzinfo=UTC),   # before reset -> excluded
        datetime(2026, 6, 1, 4, 0, tzinfo=UTC),   # exactly at reset -> included
        datetime(2026, 6, 2, 9, 0, tzinfo=UTC),   # included
    ]
    assert count_since(anchors, since) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_windows.py -k "count" -v`
Expected: FAIL with `ImportError: cannot import name 'count_today'`.

- [ ] **Step 3: Implement minimal code**

Append to `windows.py`:

```python
def count_today(anchors: list[datetime], now: datetime, tz) -> int:
    """Windows whose anchor falls on the current calendar day in `tz`."""
    today = now.astimezone(tz).date()
    return sum(1 for a in anchors if a.astimezone(tz).date() == today)


def count_since(anchors: list[datetime], since: datetime) -> int:
    """Windows whose anchor is at or after `since` (an absolute instant)."""
    return sum(1 for a in anchors if a >= since)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_windows.py -k "count" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/analyzers/windows.py tests/test_windows.py
git commit -m "feat(windows): count windows used today and since an instant"
```

---

## Task 4: Weekly-reset instant math (most-recent / next)

**Files:**
- Modify: `agent_flight_recorder/analyzers/windows.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_windows.py
from agent_flight_recorder.analyzers.windows import most_recent_reset, next_reset


def test_reset_math_weekday_and_time():
    # weekday: Monday=0 .. Sunday=6. Reset Wed 00:00 in New York.
    now = datetime(2026, 6, 2, 21, 0, tzinfo=UTC)  # Tue Jun 2, 5pm NY
    mr = most_recent_reset(now, weekday=2, hhmm=(0, 0), tz=NY)
    nr = next_reset(now, weekday=2, hhmm=(0, 0), tz=NY)
    # Most recent Wed 00:00 NY before Tue Jun 2 5pm NY is Wed May 27 00:00 NY
    assert mr == datetime(2026, 5, 27, 0, 0, tzinfo=NY).astimezone(UTC)
    # Next is Wed Jun 3 00:00 NY
    assert nr == datetime(2026, 6, 3, 0, 0, tzinfo=NY).astimezone(UTC)
    assert nr - mr == timedelta(days=7)


def test_reset_same_weekday_before_time_uses_prior_week():
    # It's Wed but before reset time -> most recent reset was last Wed
    now = datetime(2026, 6, 3, 3, 0, tzinfo=UTC)  # Tue Jun 2 11pm NY (still Tue)
    mr = most_recent_reset(now, weekday=2, hhmm=(0, 0), tz=NY)
    assert mr == datetime(2026, 5, 27, 0, 0, tzinfo=NY).astimezone(UTC)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_windows.py -k "reset" -v`
Expected: FAIL with `ImportError: cannot import name 'most_recent_reset'`.

- [ ] **Step 3: Implement minimal code**

Append to `windows.py`:

```python
def most_recent_reset(now: datetime, weekday: int, hhmm: tuple, tz) -> datetime:
    """Most recent weekly-reset instant at or before `now`.

    `weekday`: Monday=0 .. Sunday=6 (matches datetime.weekday()).
    Computed in `tz` (DST-correct), returned as the equivalent UTC instant.
    """
    local_now = now.astimezone(tz)
    days_back = (local_now.weekday() - weekday) % 7
    candidate_date = local_now.date() - timedelta(days=days_back)
    candidate = datetime(candidate_date.year, candidate_date.month, candidate_date.day,
                         hhmm[0], hhmm[1], tzinfo=tz)
    if candidate > local_now:
        candidate_date = candidate_date - timedelta(days=7)
        candidate = datetime(candidate_date.year, candidate_date.month, candidate_date.day,
                             hhmm[0], hhmm[1], tzinfo=tz)
    return candidate.astimezone(timezone.utc)


def next_reset(now: datetime, weekday: int, hhmm: tuple, tz) -> datetime:
    """Next weekly-reset instant strictly after `now` (DST-correct)."""
    mr_local = most_recent_reset(now, weekday, hhmm, tz).astimezone(tz)
    nxt_date = mr_local.date() + timedelta(days=7)
    nxt = datetime(nxt_date.year, nxt_date.month, nxt_date.day, hhmm[0], hhmm[1], tzinfo=tz)
    return nxt.astimezone(timezone.utc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_windows.py -k "reset" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/analyzers/windows.py tests/test_windows.py
git commit -m "feat(windows): compute most-recent and next weekly reset instants"
```

---

## Task 5: Availability before reset

**Files:**
- Modify: `agent_flight_recorder/analyzers/windows.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_windows.py
from agent_flight_recorder.analyzers.windows import availability


def test_availability_no_active_window():
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    reset = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)  # 12h away
    anchors = [datetime(2026, 6, 2, 0, 0, tzinfo=UTC)]  # ended at 05:00, not active
    a = availability(anchors, now, reset)
    assert a["active_remaining_h"] == 0.0
    assert a["full_windows"] == 2            # 12h // 5h
    assert round(a["tail_h"], 2) == 2.0      # remainder


def test_availability_with_active_window():
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    reset = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)
    anchors = [datetime(2026, 6, 2, 10, 0, tzinfo=UTC)]  # ends 15:00 -> active, 3h left
    a = availability(anchors, now, reset)
    assert a["active_remaining_h"] == 3.0
    # effective start = 15:00; 15:00 -> 00:00 = 9h -> 1 full window + 4h tail
    assert a["full_windows"] == 1
    assert round(a["tail_h"], 2) == 4.0


def test_availability_past_reset():
    now = datetime(2026, 6, 3, 1, 0, tzinfo=UTC)
    reset = datetime(2026, 6, 3, 0, 0, tzinfo=UTC)  # already passed
    a = availability([], now, reset)
    assert a == {"active_remaining_h": 0.0, "full_windows": 0, "tail_h": 0.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_windows.py -k "availability" -v`
Expected: FAIL with `ImportError: cannot import name 'availability'`.

- [ ] **Step 3: Implement minimal code**

Append to `windows.py`:

```python
def availability(anchors: list[datetime], now: datetime, next_reset_dt: datetime) -> dict:
    """How many fresh 5h windows fit, back-to-back, before `next_reset_dt`.

    If the latest window is still open, its remaining time is reported separately
    and counting starts from that window's end.
    """
    active_remaining = timedelta(0)
    effective_start = now
    if anchors:
        last_end = anchors[-1] + WINDOW
        if now < last_end:
            active_remaining = last_end - now
            effective_start = last_end
    span = next_reset_dt - effective_start
    if span.total_seconds() <= 0:
        return {"active_remaining_h": round(active_remaining.total_seconds() / 3600, 4),
                "full_windows": 0, "tail_h": 0.0}
    full = int(span // WINDOW)
    tail = span - full * WINDOW
    return {
        "active_remaining_h": round(active_remaining.total_seconds() / 3600, 4),
        "full_windows": full,
        "tail_h": round(tail.total_seconds() / 3600, 4),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_windows.py -k "availability" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/analyzers/windows.py tests/test_windows.py
git commit -m "feat(windows): compute availability before weekly reset"
```

---

## Task 6: Config value parsing (weekday, weekly-reset, timezone)

**Files:**
- Modify: `agent_flight_recorder/analyzers/windows.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_windows.py
import pytest
from agent_flight_recorder.analyzers.windows import parse_weekly_reset, resolve_tz


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
    assert resolve_tz("Not/AZone") == UTC   # invalid -> UTC fallback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_windows.py -k "parse_weekly_reset or resolve_tz" -v`
Expected: FAIL with `ImportError: cannot import name 'parse_weekly_reset'`.

- [ ] **Step 3: Implement minimal code**

Append to `windows.py` (add `from zoneinfo import ZoneInfo, ZoneInfoNotFoundError` to the imports at top):

```python
_WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def parse_weekly_reset(value: str) -> tuple:
    """Parse '<Weekday> HH:MM' -> (weekday_int, (hour, minute)). Raises ValueError."""
    parts = value.strip().split()
    if len(parts) != 2:
        raise ValueError("Expected '<Weekday> HH:MM', e.g. 'Wed 00:00'")
    day_str, time_str = parts
    weekday = _WEEKDAYS.get(day_str.lower())
    if weekday is None:
        raise ValueError(f"Unknown weekday: {day_str}")
    try:
        hh, mm = (int(x) for x in time_str.split(":"))
    except ValueError:
        raise ValueError(f"Bad time: {time_str}")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Time out of range: {time_str}")
    return weekday, (hh, mm)


def resolve_tz(name):
    """Return a tzinfo for an IANA name, falling back to UTC if missing/invalid."""
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_windows.py -k "parse_weekly_reset or resolve_tz" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/analyzers/windows.py tests/test_windows.py
git commit -m "feat(windows): parse weekly-reset and resolve timezone with UTC fallback"
```

---

## Task 7: `build_report` (assemble the full report)

**Files:**
- Modify: `agent_flight_recorder/analyzers/windows.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_windows.py
from agent_flight_recorder.analyzers.windows import build_report


def _starts():
    return [
        "2026-06-02T03:00:00.000Z",   # Jun 1 NY
        "2026-06-02T14:00:00.000Z",   # Jun 2 NY 10am
        "2026-06-02T20:00:00.000Z",   # Jun 2 NY 4pm (active at now below)
        "garbage",                    # skipped
        "",                           # skipped
    ]


def test_build_report_with_config():
    now = datetime(2026, 6, 2, 21, 0, tzinfo=UTC)  # Jun 2 NY 5pm
    cfg = {"timezone": "America/New_York", "weekly_reset_weekday": "2", "weekly_reset_time": "00:00"}
    r = build_report(_starts(), cfg, now)
    assert r["reset_configured"] is True
    assert r["today"] == 2                 # the 14:00 and 20:00 anchors (NY Jun 2)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_windows.py -k "build_report" -v`
Expected: FAIL with `ImportError: cannot import name 'build_report'`.

- [ ] **Step 3: Implement minimal code**

Append to `windows.py`:

```python
def build_report(starts: list, config: dict, now: datetime) -> dict:
    """Assemble the windows report from raw `started_at` strings + config + now."""
    parsed = [p for p in (parse_started_at(s) for s in starts) if p is not None]
    anchors = reconstruct_windows(parsed)
    tz = resolve_tz(config.get("timezone"))
    tz_label = config.get("timezone") or "UTC"

    report = {
        "tz_label": tz_label,
        "today": count_today(anchors, now, tz),
        "total_windows": len(anchors),
    }

    weekday = config.get("weekly_reset_weekday")
    reset_time = config.get("weekly_reset_time")
    if weekday is not None and reset_time:
        hh, mm = (int(x) for x in reset_time.split(":"))
        mr = most_recent_reset(now, int(weekday), (hh, mm), tz)
        nr = next_reset(now, int(weekday), (hh, mm), tz)
        report["reset_configured"] = True
        report["week"] = count_since(anchors, mr)
        report["available"] = availability(anchors, now, nr)
        report["next_reset_local"] = nr.astimezone(tz)
    else:
        report["reset_configured"] = False
        report["week"] = None
        report["available"] = None
        report["next_reset_local"] = None
    return report
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_windows.py -k "build_report" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/analyzers/windows.py tests/test_windows.py
git commit -m "feat(windows): assemble full windows report"
```

---

## Task 8: Render `print_windows`

**Files:**
- Modify: `agent_flight_recorder/render/terminal.py`
- Test: `tests/test_windows.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_windows.py
from agent_flight_recorder.render.terminal import print_windows


def test_print_windows_smoke(capsys):
    report = {
        "tz_label": "America/New_York", "today": 2, "total_windows": 7,
        "reset_configured": True, "week": 7,
        "available": {"active_remaining_h": 3.0, "full_windows": 2, "tail_h": 0.4},
        "next_reset_local": datetime(2026, 6, 3, 0, 0, tzinfo=ZoneInfo("America/New_York")),
    }
    print_windows(report)
    out = capsys.readouterr().out
    assert "Today" in out and "2" in out
    assert "available" in out.lower() or "Available" in out

    report2 = dict(report, reset_configured=False, week=None, available=None, next_reset_local=None)
    print_windows(report2)
    out2 = capsys.readouterr().out
    assert "afr config set" in out2   # hint to configure reset
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_windows.py -k "print_windows" -v`
Expected: FAIL with `ImportError: cannot import name 'print_windows'`.

- [ ] **Step 3: Implement minimal code**

Append to `render/terminal.py`:

```python
def print_windows(report: dict) -> None:
    console.print(Panel("[bold]5-hour windows[/bold]"))
    console.print(f"  Today:        [bold]{report['today']}[/bold] used")
    if report["reset_configured"]:
        console.print(f"  This week:    [bold]{report['week']}[/bold] used "
                      f"[dim](since weekly reset, {report['tz_label']})[/dim]")
        av = report["available"]
        if av["active_remaining_h"] > 0:
            console.print(f"  Active:       [cyan]{av['active_remaining_h']:.1f}h[/cyan] left in current window")
        reset_str = report["next_reset_local"].strftime("%a %b %d, %I:%M%p").replace(" 0", " ")
        console.print(f"  Available:    [green]~{av['full_windows']}[/green] more full windows "
                      f"before reset ({reset_str} {report['tz_label']}) + {av['tail_h']:.1f}h tail")
    else:
        console.print("  This week:    [dim]set your weekly reset to see this[/dim]")
        console.print('  [yellow]Tip:[/yellow] run [bold]afr config set weekly-reset "Wed 00:00"[/bold] '
                      'and [bold]afr config set timezone "America/New_York"[/bold]')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_windows.py -k "print_windows" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/render/terminal.py tests/test_windows.py
git commit -m "feat(render): print windows report panel"
```

---

## Task 9: Wire CLI `windows` command + `config` sub-app

**Files:**
- Modify: `agent_flight_recorder/cli.py`
- Modify: `pyproject.toml` (add `tzdata`)
- Test: `tests/test_windows.py` (CliRunner smoke)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_windows.py
from typer.testing import CliRunner
from agent_flight_recorder.cli import app
from agent_flight_recorder import db as afrdb

runner = CliRunner()


def test_cli_config_and_windows(tmp_path, monkeypatch):
    test_db = tmp_path / "afr.db"
    monkeypatch.setattr(afrdb, "DB_PATH", test_db)
    # config set (valid)
    res = runner.invoke(app, ["config", "set", "weekly-reset", "Wed 00:00"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(app, ["config", "set", "timezone", "America/New_York"])
    assert res.exit_code == 0, res.output
    # config set (invalid weekly-reset) -> non-zero exit, friendly message
    res = runner.invoke(app, ["config", "set", "weekly-reset", "Funday"])
    assert res.exit_code != 0
    # config show
    res = runner.invoke(app, ["config", "show"])
    assert "America/New_York" in res.output
    # windows runs without error even with an empty runs table
    res = runner.invoke(app, ["windows"])
    assert res.exit_code == 0, res.output
    assert "5-hour windows" in res.output or "Today" in res.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_windows.py -k "cli_config_and_windows" -v`
Expected: FAIL — no `config`/`windows` commands registered (Typer exits non-zero with "No such command").

- [ ] **Step 3: Implement minimal code**

In `cli.py`, extend the `db` import on line 11 to add the config helpers and `get_all_config`:

```python
from .db import (get_connection, init_db, DB_PATH, list_runs, get_run, get_run_events,
                 search_runs, set_outcome, get_latest_run, bulk_set_outcome,
                 count_runs_for_bulk, get_config, set_config, get_all_config)
```

Add to the render import on line 16: `print_windows`. Add a windows analyzer import:

```python
from .analyzers.windows import build_report, parse_weekly_reset
from .render.terminal import console, print_run_list, print_run_detail, print_stats, print_windows
```

Add the `windows` command (anywhere among the `@app.command()` blocks). Note: read `DB_PATH` via the module so the test's monkeypatch is honored:

```python
@app.command()
def windows():
    """Show 5-hour usage windows used today/this week and how many remain before reset."""
    from datetime import datetime, timezone
    from . import db as _db
    conn = get_connection(_db.DB_PATH)
    init_db(conn)
    rows = conn.execute("SELECT started_at FROM runs WHERE started_at != ''").fetchall()
    cfg = get_all_config(conn)
    conn.close()
    report = build_report([r["started_at"] for r in rows], cfg, datetime.now(timezone.utc))
    print_windows(report)
```

Add the `config` sub-app near the top, just after `app = typer.Typer(...)` on line 18:

```python
config_app = typer.Typer(help="Manage afr configuration (weekly reset, timezone).")
app.add_typer(config_app, name="config")

_CONFIG_KEYS = {"weekly-reset", "timezone"}


@config_app.command("set")
def config_set(key: str = typer.Argument(...), value: str = typer.Argument(...)):
    """Set a config value. Keys: weekly-reset '<Weekday> HH:MM', timezone <IANA>."""
    from . import db as _db
    if key not in _CONFIG_KEYS:
        console.print(f"[red]Unknown key '{key}'. Valid: {', '.join(sorted(_CONFIG_KEYS))}[/red]")
        raise typer.Exit(1)
    conn = get_connection(_db.DB_PATH)
    init_db(conn)
    if key == "weekly-reset":
        try:
            weekday, (hh, mm) = parse_weekly_reset(value)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            conn.close()
            raise typer.Exit(1)
        set_config(conn, "weekly_reset_weekday", str(weekday))
        set_config(conn, "weekly_reset_time", f"{hh:02d}:{mm:02d}")
    else:  # timezone
        set_config(conn, "timezone", value)
    conn.close()
    console.print(f"[green]Set {key} = {value}[/green]")


@config_app.command("show")
def config_show():
    """Print current afr configuration."""
    from . import db as _db
    conn = get_connection(_db.DB_PATH)
    init_db(conn)
    cfg = get_all_config(conn)
    conn.close()
    if not cfg:
        console.print("[yellow]No config set. Try: afr config set weekly-reset \"Wed 00:00\"[/yellow]")
        return
    for k, v in cfg.items():
        console.print(f"  {k}: [bold]{v}[/bold]")
```

In `pyproject.toml`, add `tzdata` to the `dependencies` array (so `zoneinfo` works on Windows):

```toml
dependencies = [
    # ...existing entries unchanged...
    "tzdata; sys_platform == 'win32'",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_windows.py -k "cli_config_and_windows" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/cli.py pyproject.toml tests/test_windows.py
git commit -m "feat(cli): add afr windows command and afr config sub-app"
```

---

## Task 10: Docs + full suite + manual smoke

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README section**

Under Usage in `README.md`, add:

````markdown
### See your 5-hour usage windows

```bash
afr config set weekly-reset "Wed 00:00"      # when your weekly cap resets
afr config set timezone "America/New_York"   # your display timezone (IANA)
afr windows
```

`afr windows` reconstructs the Anthropic 5-hour usage windows you actually
opened (from recorded run timestamps) and shows how many fresh windows fit
before your weekly reset. This is the *time/window* view; for the token-budget
forecast, use claude-burnrate with `/usage`.
````

- [ ] **Step 2: Run the full test suite**

Run: `pytest -q`
Expected: PASS (all existing tests plus the new `test_config.py` and `test_windows.py`).

- [ ] **Step 3: Manual smoke against the real DB**

Run:
```bash
afr config set weekly-reset "Wed 00:00"
afr config set timezone "America/New_York"
afr windows
```
Expected: a panel printing today's window count, this week's count, active-window remaining, and windows available before the Jun 3 reset, with no traceback.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document afr windows and afr config"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** reconstruction (T2), used today/week (T3, T7), availability before reset (T5, T7), config-once storage (T1, T6, T9), CLI surface `windows`/`config set`/`config show` (T9), output panel + no-config hint (T8), error handling (skip bad timestamps T2/T7, UTC fallback T6, no-reset path T7/T8), TDD throughout. Non-goal (token budget) explicitly excluded from output (T8 wording).
- **Type consistency:** `availability` returns keys `active_remaining_h`/`full_windows`/`tail_h` — used identically in T5, T7, T8. `build_report` returns `today`/`week`/`available`/`reset_configured`/`tz_label`/`next_reset_local` — consumed identically in T8/T9. `parse_weekly_reset` returns `(weekday, (hh, mm))` — destructured the same way in T6 and T9.
- **Placeholder scan:** no TBD/TODO; every code step has complete code.
- **DB impact:** single additive `config` table; no change to `runs`; documented in spec + `init_db`.
