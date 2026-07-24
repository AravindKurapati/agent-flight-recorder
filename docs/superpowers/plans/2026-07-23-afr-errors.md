# afr errors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `afr errors [--min-count N] [--days N]` — a ranked list of recurring failed shell-command fingerprints (exact `command` + `exit_code`) across recorded sessions.

**Architecture:** New pure-logic analyzer `analyzers/errors.py::get_recurring_errors(conn, min_count=2, days=None)` groups failed `shell_commands` rows (joined to `runs` for the optional `days` window). A new `render/terminal.py::print_errors` renders the ranked table. A thin `afr errors` CLI command wires the two together.

**Tech Stack:** Python 3.11+, `sqlite3`, `typer`, `rich`, `pytest`.

## Global Constraints

- Fingerprint = exact `(command, exit_code)` pair, no normalization (FEATURE_errors.md "Goals"/"Non-goals").
- Shell command failures only — not the `errors` table (FEATURE_errors.md "Non-goals").
- No new DB tables/columns — read-only over `shell_commands` and `runs` (FEATURE_errors.md "DB impact").
- No integration into `afr show`/`afr digest` in this plan — standalone command only.
- `min_count` below 1 is clamped to 1, never an error (FEATURE_errors.md "Error handling").
- TDD: write the failing test before implementation, every task.

---

### Task 1: `analyzers/errors.py` — recurring-failure aggregation

**Files:**
- Create: `agent_flight_recorder/analyzers/errors.py`
- Test: `tests/test_errors_analyzer.py`

**Interfaces:**
- Consumes: nothing new — reads `shell_commands` joined to `runs` directly via `conn.execute`.
- Produces: `get_recurring_errors(conn: sqlite3.Connection, min_count: int = 2, days: Optional[int] = None) -> list[dict]`. Each dict: `{"command": str, "exit_code": int, "count": int, "first_seen": str, "last_seen": str, "run_ids": list[str]}` (8-char id prefixes, de-duplicated, insertion order). Sorted by `count` descending, then `last_seen` descending. Task 2 and Task 3 consume this exact shape and these exact keys.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_errors_analyzer.py
import pytest
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.analyzers.errors import get_recurring_errors
from agent_flight_recorder.models import ParsedSession, Run, ShellCommand


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    init_db(c)
    return c


def _seed_run(conn, run_id, started_at, shell_commands):
    upsert_session(conn, ParsedSession(
        run=Run(id=run_id, source="claude", started_at=started_at, ended_at=started_at),
        shell_commands=shell_commands,
    ))


def test_no_failures_returns_empty(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="ls", exit_code=0, timestamp="2026-07-20T10:00:00Z"),
    ])
    assert get_recurring_errors(conn) == []


def test_single_failure_below_min_count_excluded(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="pytest x", exit_code=1, timestamp="2026-07-20T10:00:00Z"),
    ])
    assert get_recurring_errors(conn, min_count=2) == []


def test_min_count_boundary_included(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="pytest x", exit_code=1, timestamp="2026-07-20T10:00:00Z"),
    ])
    _seed_run(conn, "run-2", "2026-07-21T10:00:00Z", [
        ShellCommand(id="sc-2", run_id="run-2", command="pytest x", exit_code=1, timestamp="2026-07-21T10:00:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2)
    assert len(entries) == 1
    assert entries[0]["count"] == 2
    assert entries[0]["command"] == "pytest x"
    assert entries[0]["exit_code"] == 1
    assert entries[0]["first_seen"] == "2026-07-20T10:00:00Z"
    assert entries[0]["last_seen"] == "2026-07-21T10:00:00Z"
    assert entries[0]["run_ids"] == ["run-1"[:8], "run-2"[:8]]


def test_same_command_different_exit_codes_kept_separate(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="npm run build", exit_code=1, timestamp="2026-07-20T10:00:00Z"),
        ShellCommand(id="sc-2", run_id="run-1", command="npm run build", exit_code=1, timestamp="2026-07-20T10:01:00Z"),
    ])
    _seed_run(conn, "run-2", "2026-07-21T10:00:00Z", [
        ShellCommand(id="sc-3", run_id="run-2", command="npm run build", exit_code=127, timestamp="2026-07-21T10:00:00Z"),
        ShellCommand(id="sc-4", run_id="run-2", command="npm run build", exit_code=127, timestamp="2026-07-21T10:01:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2)
    assert len(entries) == 2
    exit_codes = {e["exit_code"] for e in entries}
    assert exit_codes == {1, 127}


def test_run_ids_deduplicated_within_same_run(conn):
    _seed_run(conn, "run-1", "2026-07-20T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="flaky", exit_code=1, timestamp="2026-07-20T10:00:00Z"),
        ShellCommand(id="sc-2", run_id="run-1", command="flaky", exit_code=1, timestamp="2026-07-20T10:01:00Z"),
    ])
    _seed_run(conn, "run-2", "2026-07-21T10:00:00Z", [
        ShellCommand(id="sc-3", run_id="run-2", command="flaky", exit_code=1, timestamp="2026-07-21T10:00:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2)
    assert entries[0]["count"] == 3
    assert entries[0]["run_ids"] == ["run-1"[:8], "run-2"[:8]]


def test_days_window_filters_by_run_started_at(conn):
    _seed_run(conn, "run-old-1", "2020-01-01T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-old-1", command="old fail", exit_code=1, timestamp="2020-01-01T10:00:00Z"),
    ])
    _seed_run(conn, "run-old-2", "2020-01-02T10:00:00Z", [
        ShellCommand(id="sc-2", run_id="run-old-2", command="old fail", exit_code=1, timestamp="2020-01-02T10:00:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2, days=7)
    assert entries == []


def test_sort_order_count_desc_then_last_seen_desc(conn):
    _seed_run(conn, "run-1", "2026-07-15T10:00:00Z", [
        ShellCommand(id="sc-1", run_id="run-1", command="a", exit_code=1, timestamp="2026-07-15T10:00:00Z"),
        ShellCommand(id="sc-2", run_id="run-1", command="a", exit_code=1, timestamp="2026-07-15T10:01:00Z"),
    ])
    _seed_run(conn, "run-2", "2026-07-16T10:00:00Z", [
        ShellCommand(id="sc-3", run_id="run-2", command="b", exit_code=1, timestamp="2026-07-16T10:00:00Z"),
        ShellCommand(id="sc-4", run_id="run-2", command="b", exit_code=1, timestamp="2026-07-16T10:01:00Z"),
        ShellCommand(id="sc-5", run_id="run-2", command="b", exit_code=1, timestamp="2026-07-16T10:02:00Z"),
    ])
    entries = get_recurring_errors(conn, min_count=2)
    assert entries[0]["command"] == "b"  # count 3 beats count 2
    assert entries[1]["command"] == "a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_errors_analyzer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_flight_recorder.analyzers.errors'`

- [ ] **Step 3: Implement `analyzers/errors.py`**

```python
"""Recurring failed shell-command fingerprints across recorded runs.

Fingerprint = exact (command, exit_code) pair, no normalization. Read-only
over shell_commands joined to runs (for the optional days window).
"""
import sqlite3
from typing import Optional


def get_recurring_errors(
    conn: sqlite3.Connection, min_count: int = 2, days: Optional[int] = None
) -> list[dict]:
    min_count = max(min_count, 1)

    where = "WHERE sc.exit_code IS NOT NULL AND sc.exit_code != 0"
    params: list = []
    if days:
        where += " AND r.started_at >= datetime('now', ?)"
        params.append(f"-{days} days")

    rows = conn.execute(
        f"""
        SELECT sc.command, sc.exit_code, sc.timestamp, sc.run_id
        FROM shell_commands sc
        JOIN runs r ON r.id = sc.run_id
        {where}
        ORDER BY sc.timestamp ASC
        """,
        params,
    ).fetchall()

    groups: dict[tuple, dict] = {}
    for row in rows:
        key = (row["command"], row["exit_code"])
        g = groups.get(key)
        if g is None:
            g = {
                "command": row["command"], "exit_code": row["exit_code"],
                "count": 0, "first_seen": row["timestamp"], "last_seen": row["timestamp"],
                "run_ids": [],
            }
            groups[key] = g
        g["count"] += 1
        g["last_seen"] = row["timestamp"]
        run_prefix = row["run_id"][:8]
        if run_prefix not in g["run_ids"]:
            g["run_ids"].append(run_prefix)

    entries = [g for g in groups.values() if g["count"] >= min_count]
    entries.sort(key=lambda e: (e["count"], e["last_seen"] or ""), reverse=True)
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_errors_analyzer.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/analyzers/errors.py tests/test_errors_analyzer.py
git commit -m "feat(errors): add get_recurring_errors analyzer"
```

---

### Task 2: `render/terminal.py::print_errors` — ranked table

**Files:**
- Modify: `agent_flight_recorder/render/terminal.py` (append `print_errors`)
- Test: `tests/test_render_errors.py`

**Interfaces:**
- Consumes: `entries: list[dict]` in the exact shape from Task 1's `get_recurring_errors`; `min_count: int` (for the header label only).
- Produces: `print_errors(entries: list[dict], min_count: int) -> None`. Task 3 imports this name.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_errors.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_render_errors.py -v`
Expected: FAIL with `ImportError: cannot import name 'print_errors'`

- [ ] **Step 3: Implement in `render/terminal.py`**

Append to the end of `agent_flight_recorder/render/terminal.py` (after `print_diff`):

```python
def print_errors(entries: list[dict], min_count: int = 2) -> None:
    if not entries:
        console.print("[yellow]No recurring failures found.[/yellow]")
        return

    console.print(Panel(f"[bold]Recurring shell failures (min {min_count} occurrences)[/bold]"))
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Count", justify="right", no_wrap=True)
    table.add_column("Exit", justify="right", no_wrap=True)
    table.add_column("Command", min_width=30)
    table.add_column("First seen", no_wrap=True)
    table.add_column("Last seen", no_wrap=True)
    table.add_column("Sessions")
    for e in entries:
        cmd = e["command"]
        cmd_cell = cmd[:60] + ".." if len(cmd) > 62 else cmd
        sessions = ", ".join(e["run_ids"][:5])
        if len(e["run_ids"]) > 5:
            sessions += ", .."
        table.add_row(
            str(e["count"]), str(e["exit_code"]), cmd_cell,
            e["first_seen"][:10] if e["first_seen"] else "",
            e["last_seen"][:10] if e["last_seen"] else "",
            sessions,
        )
    console.print(table)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_render_errors.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/render/terminal.py tests/test_render_errors.py
git commit -m "feat(errors): add print_errors terminal panel"
```

---

### Task 3: `afr errors` CLI command

**Files:**
- Modify: `agent_flight_recorder/cli.py` (add imports + new command, append-only near the `diff` command)
- Test: `tests/test_cli_errors.py`

**Interfaces:**
- Consumes: `get_recurring_errors` (Task 1), `print_errors` (Task 2).
- Produces: `afr errors [--min-count N] [--days N]` CLI command.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_errors.py
import pytest
from typer.testing import CliRunner

from agent_flight_recorder.cli import app
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.models import ParsedSession, Run, ShellCommand


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "afr.db"
    monkeypatch.setattr("agent_flight_recorder.cli.get_connection", lambda: get_connection(db_path))
    conn = get_connection(db_path)
    init_db(conn)
    conn.close()
    return db_path


def _seed(db_path):
    conn = get_connection(db_path)
    upsert_session(conn, ParsedSession(
        run=Run(id="run-1", source="claude", started_at="2026-07-20T10:00:00Z", ended_at="2026-07-20T10:00:00Z"),
        shell_commands=[ShellCommand(id="sc-1", run_id="run-1", command="pytest x", exit_code=1,
                                      timestamp="2026-07-20T10:00:00Z")],
    ))
    upsert_session(conn, ParsedSession(
        run=Run(id="run-2", source="claude", started_at="2026-07-21T10:00:00Z", ended_at="2026-07-21T10:00:00Z"),
        shell_commands=[ShellCommand(id="sc-2", run_id="run-2", command="pytest x", exit_code=1,
                                      timestamp="2026-07-21T10:00:00Z")],
    ))
    conn.close()


def test_errors_default_shows_recurring(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["errors"])
    assert result.exit_code == 0, result.output
    assert "pytest x" in result.output


def test_errors_min_count_excludes_below_threshold(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["errors", "--min-count", "3"])
    assert result.exit_code == 0, result.output
    assert "No recurring failures found" in result.output


def test_errors_days_filter(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["errors", "--days", "1"])
    assert result.exit_code == 0, result.output
    assert "No recurring failures found" in result.output


def test_errors_empty_db(tmp_db):
    result = CliRunner().invoke(app, ["errors"])
    assert result.exit_code == 0
    assert "No recurring failures found" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_errors.py -v`
Expected: FAIL with "No such command 'errors'" (exit code 2)

- [ ] **Step 3: Wire the command in `cli.py`**

Modify the import block:

```python
from .analyzers.stats import get_stats
from .analyzers.digest import get_digest
from .analyzers.diff import compute_summary, align_tool_calls
from .analyzers.errors import get_recurring_errors
from .analyzers.skill_extractor import run_extraction
from .analyzers.outcome_suggester import suggest_outcome
from .analyzers.windows import build_report, parse_weekly_reset
from .render.terminal import (console, print_run_list, print_run_detail, print_stats,
                              print_windows, print_digest, print_diff, print_errors)
```

Add the command directly after the `diff` command:

```python
@app.command("errors")
def errors_cmd(
    min_count: int = typer.Option(2, "--min-count", help="Minimum occurrences to show."),
    days: Optional[int] = typer.Option(None, "--days", help="Restrict to the last N days."),
):
    """Show recurring failed shell commands, ranked by occurrence count."""
    conn = get_connection()
    init_db(conn)
    entries = get_recurring_errors(conn, min_count=min_count, days=days)
    conn.close()
    print_errors(entries, min_count)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_errors.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add agent_flight_recorder/cli.py tests/test_cli_errors.py
git commit -m "feat(errors): add afr errors CLI command"
```

---

### Task 4: README documentation

**Files:**
- Modify: `README.md` (append a new section after the "Compare two sessions" section)

**Interfaces:**
- Consumes: nothing (docs only). Final task in the plan.

- [ ] **Step 1: Add the README section**

Insert after the "Compare two sessions" section (immediately before "### See patterns across sessions"):

```markdown

### Find recurring failures

```bash
afr errors                        # recurring failed commands, min 2 occurrences
afr errors --min-count 3          # raise the threshold
afr errors --days 30              # restrict to a recent window
```

Groups failed shell commands by exact `(command, exit_code)` — no fuzzy
matching — so you can see "you've hit this exact failure N times" instead of
rediscovering it session by session.
```

- [ ] **Step 2: Verify the doc renders sensibly**

Run: no automated check; visually confirm the inserted section reads correctly in context.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document afr errors command"
```

---

## Self-Review Notes

- **Spec coverage:** exact `(command, exit_code)` fingerprinting (Task 1), `--min-count` default 2 and clamping (Task 1 + 3), `--days` window (Task 1 + 3), run-id list per fingerprint (Task 1), sort order count-desc/last_seen-desc (Task 1), empty-result message (Task 2 + 3), truncated long commands (Task 2), no DB changes (confirmed — `errors.py` has no `CREATE`/`ALTER`), README (Task 4). No gaps found.
- **Placeholder scan:** none — every step has runnable code and exact commands.
- **Type consistency:** `get_recurring_errors`'s return keys (`command`, `exit_code`, `count`, `first_seen`, `last_seen`, `run_ids`) match exactly what `print_errors` (Task 2) and the CLI (Task 3) read — verified against every consuming test.
