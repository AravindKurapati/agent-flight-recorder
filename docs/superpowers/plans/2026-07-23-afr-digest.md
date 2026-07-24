# afr digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `afr digest [--week|--month] [--json]`, a rollup report of recent sessions (totals, per-project breakdown, abandoned streak, cost-per-shipped) reusing the existing `get_stats` analyzer.

**Architecture:** New pure-logic analyzer `analyzers/digest.py::get_digest(conn, days, now=None)` builds on `analyzers/stats.get_stats`, adding per-project grouping and two derived metrics. A new `render/terminal.py::print_digest(digest)` renders the human panel. A thin `afr digest` Typer command wires flags to both, and to `json.dumps` for `--json`.

**Tech Stack:** Python 3.11+, `sqlite3`, `pydantic` (existing `Run` model), `typer`, `rich`, `pytest`.

## Global Constraints

- No new DB tables/columns — read-only over `runs` and existing child tables (FEATURE_digest.md "DB impact").
- No LLM calls, no network calls, no API keys inside afr (FEATURE_digest.md "Non-goals").
- Additive only: existing `afr stats` command and `analyzers/stats.py` are unchanged, only imported.
- `--json` output must be valid JSON on an empty DB (zeroed totals, empty lists), never an error (FEATURE_digest.md "Error handling").
- TDD: write the failing test before implementation, every task.

---

### Task 1: `analyzers/digest.py` — digest aggregation logic

**Files:**
- Create: `agent_flight_recorder/analyzers/digest.py`
- Test: `tests/test_digest.py`

**Interfaces:**
- Consumes: `agent_flight_recorder.analyzers.stats.get_stats(conn, days) -> dict` (existing, returns `{}` when no runs in range, otherwise `{"total_runs", "outcomes", "total_cost_usd", "total_tokens_in", "total_tokens_out", "top_tools", "error_count", "shell_failures"}`).
- Produces: `get_digest(conn: sqlite3.Connection, days: int, now: Optional[datetime] = None) -> dict` with keys:
  `period_days: int`, `period_since: str` (ISO8601 UTC), `total_runs: int`, `total_cost_usd: float`,
  `total_tokens_in: int`, `total_tokens_out: int`, `outcomes: dict[str, int]`,
  `cost_per_shipped: Optional[float]`, `abandoned_streak: int`,
  `by_project: dict[str, dict]` (each value: `{"runs": int, "cost_usd": float, "tokens_in": int, "tokens_out": int, "outcomes": dict[str, int]}`),
  `sessions: list[dict]` (each: `{"id": str (8 chars), "started_at": str, "cwd_project": str, "outcome": str, "user_goal": str, "final_summary": str}`, ordered newest-first).
  Later tasks (2 and 3) call this function and read exactly these keys.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_digest.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_digest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_flight_recorder.analyzers.digest'`

- [ ] **Step 3: Implement `analyzers/digest.py`**

```python
"""Weekly/monthly rollup over recorded runs.

Builds on analyzers.stats.get_stats for totals, adding per-project grouping
and two derived signals (abandoned streak, cost-per-shipped). Read-only;
takes `now` as a parameter so tests are deterministic.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from .stats import get_stats

_STREAK_OUTCOMES = {"abandoned", "blocked"}


def _project_name(cwd: str) -> str:
    if not cwd:
        return "unknown"
    normalized = cwd.rstrip("/\\").replace("\\", "/")
    return normalized.rsplit("/", 1)[-1] or "unknown"


def get_digest(conn: sqlite3.Connection, days: int, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    period_since = (now - timedelta(days=days)).isoformat()

    base = get_stats(conn, days)
    outcomes = base.get("outcomes", {})
    total_cost_usd = base.get("total_cost_usd", 0.0)

    runs = conn.execute(
        "SELECT * FROM runs WHERE started_at >= datetime('now', ?) ORDER BY started_at DESC",
        [f"-{days} days"],
    ).fetchall()

    by_project: dict[str, dict] = {}
    for r in runs:
        project = _project_name(r["cwd"])
        bucket = by_project.setdefault(project, {
            "runs": 0, "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "outcomes": {},
        })
        bucket["runs"] += 1
        bucket["cost_usd"] += r["cost_usd"]
        bucket["tokens_in"] += r["tokens_in"]
        bucket["tokens_out"] += r["tokens_out"]
        bucket["outcomes"][r["outcome"]] = bucket["outcomes"].get(r["outcome"], 0) + 1

    abandoned_streak = 0
    for r in runs:  # already newest-first
        if r["outcome"] in _STREAK_OUTCOMES:
            abandoned_streak += 1
        else:
            break

    shipped_count = outcomes.get("shipped", 0)
    cost_per_shipped = (total_cost_usd / shipped_count) if shipped_count else None

    sessions = [
        {
            "id": r["id"][:8],
            "started_at": r["started_at"],
            "cwd_project": _project_name(r["cwd"]),
            "outcome": r["outcome"],
            "user_goal": r["user_goal"],
            "final_summary": r["final_summary"],
        }
        for r in runs
    ]

    return {
        "period_days": days,
        "period_since": period_since,
        "total_runs": base.get("total_runs", 0),
        "total_cost_usd": total_cost_usd,
        "total_tokens_in": base.get("total_tokens_in", 0),
        "total_tokens_out": base.get("total_tokens_out", 0),
        "outcomes": outcomes,
        "cost_per_shipped": cost_per_shipped,
        "abandoned_streak": abandoned_streak,
        "by_project": by_project,
        "sessions": sessions,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_digest.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/analyzers/digest.py tests/test_digest.py
git commit -m "feat(digest): add get_digest analyzer with per-project breakdown"
```

---

### Task 2: `render/terminal.py::print_digest` — human-readable panel

**Files:**
- Modify: `agent_flight_recorder/render/terminal.py` (add `print_digest`, append-only)
- Test: `tests/test_render_digest.py`

**Interfaces:**
- Consumes: the `dict` shape produced by `get_digest` in Task 1 (exact keys above). Does not call `get_digest` itself — takes the dict as a parameter, matching `print_stats(stats: dict)` and `print_windows(report: dict)`'s existing pattern.
- Produces: `print_digest(digest: dict) -> None`, printed via the module's shared `console` (`rich.console.Console`). Task 3 imports this name.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_digest.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_render_digest.py -v`
Expected: FAIL with `ImportError: cannot import name 'print_digest'`

- [ ] **Step 3: Implement `print_digest` in `render/terminal.py`**

Append to the end of `agent_flight_recorder/render/terminal.py` (after the existing `print_windows` function):

```python
def print_digest(digest: dict) -> None:
    days = digest["period_days"]
    console.print(Panel(f"[bold]Digest — last {days} days[/bold]"))
    if digest["total_runs"] == 0:
        console.print(f"[yellow]No sessions in the last {days} days.[/yellow]")
        return

    console.print(
        f"  Sessions: [bold]{digest['total_runs']}[/bold]   "
        f"Cost: [bold]${digest['total_cost_usd']:.2f}[/bold]   "
        f"Tokens: {_fmt_tokens(digest['total_tokens_in'])} in / {_fmt_tokens(digest['total_tokens_out'])} out"
    )
    outcome_parts = []
    for outcome, count in digest["outcomes"].items():
        color = _OUTCOME_COLORS.get(outcome, "dim")
        outcome_parts.append(f"[{color}]{outcome}[/{color}] {count}")
    console.print("  Outcomes: " + " | ".join(outcome_parts))

    cps = digest["cost_per_shipped"]
    cps_str = f"${cps:.2f}" if cps is not None else "n/a"
    console.print(f"  Cost/shipped: [bold]{cps_str}[/bold]")

    if digest["abandoned_streak"] >= 2:
        console.print(
            f"\n  [yellow]⚠ Last {digest['abandoned_streak']} sessions in a row were "
            f"abandoned/blocked — worth a look.[/yellow]"
        )

    if digest["by_project"]:
        console.print("\n[bold]By project[/bold]")
        for project, stats in sorted(digest["by_project"].items(), key=lambda kv: -kv[1]["runs"]):
            outcome_str = ", ".join(f"{o} {c}" for o, c in stats["outcomes"].items())
            console.print(
                f"  {project:<28} {stats['runs']:>2} runs   ${stats['cost_usd']:.2f}   {outcome_str}"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_render_digest.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add agent_flight_recorder/render/terminal.py tests/test_render_digest.py
git commit -m "feat(digest): add print_digest terminal panel"
```

---

### Task 3: `afr digest` CLI command

**Files:**
- Modify: `agent_flight_recorder/cli.py` (add imports + new command, append-only near the `stats` command)
- Test: `tests/test_cli_digest.py`

**Interfaces:**
- Consumes: `get_digest(conn, days, now=None)` from Task 1; `print_digest(digest)` from Task 2.
- Produces: `afr digest` CLI command, flags `--week` (default), `--month`, `--json`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_digest.py
import json
import pytest
from typer.testing import CliRunner

from agent_flight_recorder.cli import app
from agent_flight_recorder.db import get_connection, init_db, upsert_session
from agent_flight_recorder.models import ParsedSession, Run


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
    upsert_session(conn, ParsedSession(run=Run(
        id="run-0001", source="claude", outcome="shipped", cost_usd=2.0,
        cwd=r"D:\Aru\NYU\agent-flight-recorder",
        started_at="2026-07-22T10:00:00Z", ended_at="2026-07-22T11:00:00Z",
        user_goal="ship the digest feature",
    )))
    conn.close()


def test_digest_default_is_week(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["digest"])
    assert result.exit_code == 0, result.output
    assert "Digest" in result.output
    assert "agent-flight-recorder" in result.output


def test_digest_month_flag(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["digest", "--month"])
    assert result.exit_code == 0, result.output
    assert "30 days" in result.output


def test_digest_json_outputs_valid_json(tmp_db):
    _seed(tmp_db)
    result = CliRunner().invoke(app, ["digest", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_runs"] == 1
    assert payload["by_project"]["agent-flight-recorder"]["runs"] == 1
    assert payload["sessions"][0]["user_goal"] == "ship the digest feature"


def test_digest_json_empty_db_is_valid_json(tmp_db):
    result = CliRunner().invoke(app, ["digest", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["total_runs"] == 0
    assert payload["sessions"] == []


def test_digest_empty_db_human_message(tmp_db):
    result = CliRunner().invoke(app, ["digest"])
    assert result.exit_code == 0
    assert "No sessions" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_digest.py -v`
Expected: FAIL with `AssertionError` / Typer "No such command 'digest'" (exit code 2)

- [ ] **Step 3: Wire the command in `cli.py`**

Modify the import block (`agent_flight_recorder/cli.py:14-18`):

```python
from .analyzers.stats import get_stats
from .analyzers.digest import get_digest
from .analyzers.skill_extractor import run_extraction
from .analyzers.outcome_suggester import suggest_outcome
from .analyzers.windows import build_report, parse_weekly_reset
from .render.terminal import (console, print_run_list, print_run_detail, print_stats,
                              print_windows, print_digest)
```

Also add `import json` to the top-level imports (`agent_flight_recorder/cli.py:1`, alongside `sys`/`pathlib`/`typing`).

Add the command directly after the existing `stats` command (`agent_flight_recorder/cli.py:256-263`):

```python
@app.command()
def digest(
    week: bool = typer.Option(True, "--week", help="Last 7 days (default)."),
    month: bool = typer.Option(False, "--month", help="Last 30 days."),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
):
    """Show a weekly/monthly rollup: sessions, cost, outcomes, per-project breakdown."""
    days = 30 if month else 7
    conn = get_connection()
    init_db(conn)
    d = get_digest(conn, days)
    conn.close()
    if json_output:
        print(json.dumps(d))
    else:
        print_digest(d)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_digest.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest`
Expected: PASS, no regressions in existing tests (`test_stats.py`, `test_windows.py`, etc.)

- [ ] **Step 6: Commit**

```bash
git add agent_flight_recorder/cli.py tests/test_cli_digest.py
git commit -m "feat(digest): add afr digest CLI command (--week/--month/--json)"
```

---

### Task 4: README documentation

**Files:**
- Modify: `README.md` (append a new section after the existing "Resume a session" section, `README.md:100`)

**Interfaces:**
- Consumes: nothing (docs only).
- Produces: nothing consumed by other tasks; final task in the plan.

- [ ] **Step 1: Add the README section**

Insert after line 100 (end of the "Resume a session" section, before any following section or EOF):

```markdown

### Weekly/monthly digest

```bash
afr digest              # last 7 days
afr digest --month      # last 30 days
afr digest --json       # machine-readable, for piping into another agent
```

Shows total sessions/cost/tokens, an outcome breakdown, a per-project split, and
two derived signals: a warning if your most recent sessions were abandoned/blocked
back-to-back, and cost-per-shipped-session. `afr digest` never calls an LLM or
sends anything anywhere — `--json` is meant to be read by a separate process
(e.g. a scheduled agent) that wants to turn the numbers into a narrative summary
or email, since `afr` itself holds no API keys.
```

- [ ] **Step 2: Verify the doc renders sensibly**

Run: no automated check; visually confirm the markdown fence/example is well-formed by reading the modified section back.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document afr digest command"
```

---

## Self-Review Notes

- **Spec coverage:** `--week`/`--month` (Task 3), `--json` (Task 3), per-project breakdown (Task 1), abandoned streak (Task 1 + 2), cost-per-shipped (Task 1 + 2), sessions list for external summarization (Task 1), no DB changes (confirmed — no `_ensure_column` calls anywhere), no LLM calls (confirmed — `digest.py` has no network/API code), README update (Task 4). No gaps found.
- **Placeholder scan:** none — every step has runnable code and exact commands.
- **Type consistency:** `get_digest` return keys match exactly what `print_digest` (Task 2) and the CLI/tests (Task 3) read (`period_days`, `period_since`, `total_runs`, `total_cost_usd`, `total_tokens_in`, `total_tokens_out`, `outcomes`, `cost_per_shipped`, `abandoned_streak`, `by_project`, `sessions`) — verified against every consuming test.
