"""Reconstruct Anthropic 5-hour usage windows from recorded run timestamps.

Pure logic only: no DB access, no I/O. Every function takes its inputs (and
`now` where relevant) as parameters so tests are deterministic.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def count_today(anchors: list[datetime], now: datetime, tz) -> int:
    """Windows whose anchor falls on the current calendar day in `tz`."""
    today = now.astimezone(tz).date()
    return sum(1 for a in anchors if a.astimezone(tz).date() == today)


def count_since(anchors: list[datetime], since: datetime) -> int:
    """Windows whose anchor is at or after `since` (an absolute instant)."""
    return sum(1 for a in anchors if a >= since)


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
