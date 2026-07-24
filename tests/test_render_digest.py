from rich.console import Console
from agent_flight_recorder.render.terminal import print_digest


def _capture(digest):
    console = Console(record=True, width=100)
    import agent_flight_recorder.render.terminal as terminal
    old = terminal.console
    terminal.console = console
    try:
        print_digest(digest)
    finally:
        terminal.console = old
    return console.export_text()


def test_print_digest_empty_shows_no_sessions_message():
    digest = {
        "period_days": 7, "period_since": "2026-07-16T00:00:00+00:00",
        "total_runs": 0, "total_cost_usd": 0.0, "total_tokens_in": 0, "total_tokens_out": 0,
        "outcomes": {}, "cost_per_shipped": None, "abandoned_streak": 0,
        "by_project": {}, "sessions": [],
    }
    out = _capture(digest)
    assert "No sessions" in out


def test_print_digest_shows_totals_and_cost_per_shipped():
    digest = {
        "period_days": 7, "period_since": "2026-07-16T00:00:00+00:00",
        "total_runs": 2, "total_cost_usd": 3.5, "total_tokens_in": 100, "total_tokens_out": 50,
        "outcomes": {"shipped": 1, "abandoned": 1}, "cost_per_shipped": 3.5, "abandoned_streak": 0,
        "by_project": {"agent-flight-recorder": {"runs": 2, "cost_usd": 3.5, "tokens_in": 100,
                                                  "tokens_out": 50, "outcomes": {"shipped": 1, "abandoned": 1}}},
        "sessions": [],
    }
    out = _capture(digest)
    assert "2" in out  # total_runs
    assert "3.50" in out  # cost formatted to 2dp
    assert "agent-flight-recorder" in out


def test_print_digest_shows_abandoned_streak_warning():
    digest = {
        "period_days": 7, "period_since": "2026-07-16T00:00:00+00:00",
        "total_runs": 3, "total_cost_usd": 1.0, "total_tokens_in": 10, "total_tokens_out": 5,
        "outcomes": {"abandoned": 3}, "cost_per_shipped": None, "abandoned_streak": 3,
        "by_project": {}, "sessions": [],
    }
    out = _capture(digest)
    assert "abandoned" in out.lower()
    assert "3" in out


def test_print_digest_cost_per_shipped_na_when_none():
    digest = {
        "period_days": 7, "period_since": "2026-07-16T00:00:00+00:00",
        "total_runs": 1, "total_cost_usd": 1.0, "total_tokens_in": 10, "total_tokens_out": 5,
        "outcomes": {"abandoned": 1}, "cost_per_shipped": None, "abandoned_streak": 1,
        "by_project": {}, "sessions": [],
    }
    out = _capture(digest)
    assert "n/a" in out
