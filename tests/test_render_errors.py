from rich.console import Console
from agent_flight_recorder.render.terminal import print_errors


def _capture(entries, min_count=2):
    console = Console(record=True, width=120)
    import agent_flight_recorder.render.terminal as terminal
    old = terminal.console
    terminal.console = console
    try:
        print_errors(entries, min_count)
    finally:
        terminal.console = old
    return console.export_text()


def test_print_errors_empty_shows_message():
    out = _capture([])
    assert "No recurring failures found" in out


def test_print_errors_shows_entry_fields():
    entries = [{
        "command": "pytest tests/test_foo.py", "exit_code": 1, "count": 5,
        "first_seen": "2026-06-01T10:00:00Z", "last_seen": "2026-07-20T10:00:00Z",
        "run_ids": ["6c84c429", "a1b2c3d4"],
    }]
    out = _capture(entries)
    assert "pytest tests/test_foo.py" in out
    assert "5" in out
    assert "6c84c429" in out


def test_print_errors_truncates_long_command():
    long_cmd = "x" * 100
    entries = [{
        "command": long_cmd, "exit_code": 1, "count": 2,
        "first_seen": "2026-07-01T10:00:00Z", "last_seen": "2026-07-02T10:00:00Z",
        "run_ids": ["aaaaaaaa"],
    }]
    out = _capture(entries)
    assert long_cmd not in out
