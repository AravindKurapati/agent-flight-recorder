from rich.console import Console
from agent_flight_recorder.render.terminal import print_diff


def _capture(run_a, run_b, summary_a, summary_b, alignment=None):
    console = Console(record=True, width=100)
    import agent_flight_recorder.render.terminal as terminal
    old = terminal.console
    terminal.console = console
    try:
        print_diff(run_a, run_b, summary_a, summary_b, alignment)
    finally:
        terminal.console = old
    return console.export_text()


def _summary(**overrides):
    base = {
        "cost_usd": 1.0, "tokens_in": 100, "tokens_out": 50,
        "duration_seconds": 840.0, "outcome": "shipped",
        "tool_call_count": 2, "error_count": 0, "shell_failure_count": 0,
    }
    base.update(overrides)
    return base


def test_print_diff_shows_both_ids_and_outcomes():
    out = _capture({"id": "run-aaaa1111"}, {"id": "run-bbbb2222"},
                    _summary(outcome="shipped"), _summary(outcome="abandoned"))
    assert "run-aaaa" in out
    assert "run-bbbb" in out
    assert "shipped" in out
    assert "abandoned" in out


def test_print_diff_in_progress_when_duration_none():
    out = _capture({"id": "run-a"}, {"id": "run-b"},
                    _summary(duration_seconds=None), _summary())
    assert "in progress" in out


def test_print_diff_without_alignment_omits_sequence_section():
    out = _capture({"id": "run-a"}, {"id": "run-b"}, _summary(), _summary())
    assert "Tool-call sequence" not in out


def test_print_diff_with_alignment_shows_sequence_section():
    out = _capture({"id": "run-a"}, {"id": "run-b"}, _summary(), _summary(),
                    alignment=[("Read", "Read"), (None, "Grep"), ("Edit", "Edit")])
    assert "Tool-call sequence" in out
    assert "Grep" in out
