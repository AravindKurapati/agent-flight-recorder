import sys
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import json
from pathlib import Path
from typing import Optional
import typer
from .db import (get_connection, init_db, DB_PATH, list_runs, get_run, get_run_events,
                 search_runs, set_outcome, get_latest_run, bulk_set_outcome,
                 count_runs_for_bulk, get_config, set_config, get_all_config,
                 resolve_runs_by_prefix)
from .ingester import ingest_claude, ingest_codex
from .analyzers.stats import get_stats
from .analyzers.digest import get_digest
from .analyzers.diff import compute_summary, align_tool_calls
from .analyzers.skill_extractor import run_extraction
from .analyzers.outcome_suggester import suggest_outcome
from .analyzers.windows import build_report, parse_weekly_reset
from .render.terminal import (console, print_run_list, print_run_detail, print_stats,
                              print_windows, print_digest, print_diff)

app = typer.Typer(name="afr", help="Agent Flight Recorder — local AI session observability", add_completion=False)

config_app = typer.Typer(help="Manage afr configuration (weekly reset, timezone).")
app.add_typer(config_app, name="config")

_VALID_OUTCOMES = {"shipped", "blocked", "abandoned", "exploratory"}
_CONFIG_KEYS = {"weekly-reset", "timezone"}


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


@app.command()
def ingest(source: str = typer.Argument(..., help="claude or codex")):
    """Ingest sessions from Claude Code or Codex."""
    if source == "claude":
        new, skipped = ingest_claude()
    elif source == "codex":
        new, skipped = ingest_codex()
    else:
        console.print(f"[red]Unknown source '{source}'. Use 'claude' or 'codex'.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Done.[/green] {new} new, {skipped} skipped.")


@app.command("list")
def list_cmd(days: Optional[int] = typer.Option(None, "--days", "-d", help="Last N days")):
    """List all recorded runs."""
    conn = get_connection()
    init_db(conn)
    runs = list_runs(conn, days)
    conn.close()
    if not runs:
        console.print("[yellow]No runs. Try `afr ingest claude`.[/yellow]")
    else:
        print_run_list(runs)


@app.command()
def show(run_id: str = typer.Argument(..., help="Run ID or prefix")):
    """Show full detail for a run."""
    conn = get_connection()
    init_db(conn)
    row = conn.execute("SELECT * FROM runs WHERE id LIKE ?", (f"{run_id}%",)).fetchone()
    if not row:
        console.print(f"[red]Run not found: {run_id}[/red]")
        conn.close()
        raise typer.Exit(1)
    events = get_run_events(conn, row["id"])
    conn.close()
    print_run_detail(row, events)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run ID or prefix, as shown by `afr list`/`afr search`."),
    run: bool = typer.Option(False, "--run", help="Launch the resume command now instead of just printing it."),
):
    """Print (or run) the exact command to resume a recorded session in its original directory.

    `afr list`/`afr search` show only an 8-char id prefix, but `claude --resume` / `codex resume`
    need the full session id and must run from the directory the session started in. This resolves
    the prefix to the full id + recorded cwd and hands you (or runs) the ready-to-paste command.
    """
    conn = get_connection()
    init_db(conn)
    matches = resolve_runs_by_prefix(conn, run_id)
    conn.close()

    if not matches:
        console.print(f"[red]Run not found: {run_id}[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[yellow]Multiple runs match '{run_id}'. Use more characters:[/yellow]")
        print_run_list(matches)
        raise typer.Exit(1)

    row = matches[0]
    full_id = row["id"]
    cwd = (row["cwd"] or "")
    branch = (row["git_branch"] or "")
    tool_cmd = f"codex resume {full_id}" if row["source"] == "codex" else f"claude --resume {full_id}"

    console.print(f"[bold]Session:[/bold] {full_id}  [dim]({row['source']})[/dim]")
    if row["user_goal"]:
        console.print(f"[dim]{row['user_goal'][:80]}[/dim]")
    if branch:
        console.print(f"[dim]branch: {branch}[/dim]")

    if cwd:
        console.print("\n[dim]Resume with:[/dim]")
        console.print(f'  cd "{cwd}" && {tool_cmd}')
    else:
        console.print("\n[yellow]Original directory not recorded (older session, or re-ingest needed).[/yellow]")
        console.print(f"[dim]Run this from the project directory:[/dim]\n  {tool_cmd}")

    if run:
        import subprocess
        try:
            rc = subprocess.call(tool_cmd.split(), cwd=cwd or None)
        except FileNotFoundError:
            console.print(f"[red]Could not launch '{tool_cmd.split()[0]}'. Is it on PATH?[/red]")
            raise typer.Exit(1)
        raise typer.Exit(rc)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    days: Optional[int] = typer.Option(None, "--days", "-d"),
):
    """Full-text search across run goals and summaries."""
    conn = get_connection()
    init_db(conn)
    runs = search_runs(conn, query, days)
    conn.close()
    if not runs:
        console.print(f"[yellow]No results for: {query}[/yellow]")
    else:
        print_run_list(runs)


@app.command()
def suggest(
    run_id: Optional[str] = typer.Argument(None, help="Run ID/prefix. Omit with --latest."),
    latest: bool = typer.Option(False, "--latest", "-l", help="Suggest for the most recently active session in the current directory."),
    any_cwd: bool = typer.Option(False, "--any-cwd", help="With --latest, do not restrict to the current directory."),
):
    """Suggest an outcome based on session signals (does not apply it)."""
    conn = get_connection()
    init_db(conn)

    if latest:
        cwd_basename = None if any_cwd else Path.cwd().name
        row = get_latest_run(conn, cwd_basename)
        if not row:
            conn.close()
            scope = "any cwd" if any_cwd else f"cwd '{cwd_basename}'"
            console.print(f"[red]No sessions found in {scope}.[/red]")
            raise typer.Exit(1)
    elif run_id:
        row = conn.execute("SELECT * FROM runs WHERE id LIKE ?", (f"{run_id}%",)).fetchone()
        if not row:
            console.print(f"[red]Run not found: {run_id}[/red]")
            conn.close()
            raise typer.Exit(1)
    else:
        console.print("[red]Pass a run ID or --latest.[/red]")
        conn.close()
        raise typer.Exit(1)

    events = get_run_events(conn, row["id"])
    conn.close()
    rid = row["id"][:8]

    suggestion = suggest_outcome(events)
    if suggestion is None:
        console.print(f"[dim]{rid}: no strong signal — outcome unclear.[/dim]")
        return
    outcome, reason = suggestion
    color = {"shipped": "green", "blocked": "red", "exploratory": "blue", "abandoned": "yellow"}.get(outcome, "white")
    console.print(f"[{color}]{rid}: suggest [bold]{outcome}[/bold] — {reason}[/{color}]")
    console.print(f"[dim]Apply with:[/dim] afr tag {rid} {outcome}")


@app.command()
def recent(
    n: int = typer.Argument(10, help="Number of most recent runs to show."),
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Restrict to the last N days."),
):
    """Show the most recently active runs (newest first)."""
    if n <= 0:
        console.print("[red]N must be positive.[/red]")
        raise typer.Exit(1)
    conn = get_connection()
    init_db(conn)
    runs = list_runs(conn, days=days, limit=n)
    conn.close()
    if not runs:
        console.print("[yellow]No runs. Try `afr ingest claude`.[/yellow]")
    else:
        print_run_list(runs)


@app.command()
def stats(days: Optional[int] = typer.Option(None, "--days", "-d")):
    """Show session statistics."""
    conn = get_connection()
    init_db(conn)
    s = get_stats(conn, days)
    conn.close()
    print_stats(s)


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


def _resolve_diff_id(conn, id_str: str):
    """Resolve one id/prefix for `afr diff`. Returns (run_or_None, ok: bool)."""
    matches = resolve_runs_by_prefix(conn, id_str)
    if not matches:
        console.print(f"[red]No run matches '{id_str}'.[/red]")
        return None, False
    if len(matches) > 1:
        console.print(f"[yellow]Multiple runs match '{id_str}'. Add more characters:[/yellow]")
        print_run_list(matches)
        return None, False
    return matches[0], True


@app.command()
def diff(
    id1: str = typer.Argument(..., help="First run ID/prefix."),
    id2: str = typer.Argument(..., help="Second run ID/prefix."),
    full: bool = typer.Option(False, "--full", help="Also show aligned tool-call sequence."),
):
    """Compare two sessions side by side."""
    conn = get_connection()
    init_db(conn)

    run_a, ok_a = _resolve_diff_id(conn, id1)
    run_b, ok_b = _resolve_diff_id(conn, id2)
    if not ok_a or not ok_b:
        conn.close()
        raise typer.Exit(1)

    events_a = get_run_events(conn, run_a["id"])
    events_b = get_run_events(conn, run_b["id"])
    conn.close()

    summary_a = compute_summary(run_a, events_a)
    summary_b = compute_summary(run_b, events_b)

    alignment = None
    if full:
        names_a = [tc["tool_name"] for tc in events_a["tool_calls"]]
        names_b = [tc["tool_name"] for tc in events_b["tool_calls"]]
        alignment = align_tool_calls(names_a, names_b)

    print_diff(run_a, run_b, summary_a, summary_b, alignment)


def _is_hex_id(s: str) -> bool:
    return len(s) >= 6 and all(c in "0123456789abcdefABCDEF" for c in s)


def _parse_duration_to_days(s: str) -> Optional[int]:
    """Parse '30', '30d', '2w', '1m' into days. Returns None on invalid input."""
    if not s:
        return None
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    if len(s) < 2 or not s[:-1].isdigit():
        return None
    n, unit = int(s[:-1]), s[-1]
    if unit == "d":
        return n
    if unit == "w":
        return n * 7
    if unit == "m":
        return n * 30
    return None


def _pick_run_interactively(matches: list, query: str):
    """Prompt the user to pick from multiple matches. Returns the row or None if cancelled."""
    console.print(f"[yellow]Multiple runs match '{query}'. Pick one:[/yellow]")
    print_run_list(matches, numbered=True)
    while True:
        try:
            choice = typer.prompt(f"Select [1-{len(matches)}, q to cancel]", default="q", show_default=False)
        except (EOFError, typer.Abort):
            return None
        choice = (choice or "").strip().lower()
        if choice in ("q", "quit", "exit", ""):
            return None
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(matches):
                return matches[idx - 1]
        console.print(f"[red]Invalid choice. Enter 1-{len(matches)} or q.[/red]")


@app.command()
def tag(
    run_id: Optional[str] = typer.Argument(None, help="Run ID/prefix, or search text. Omit with --latest or bulk filters."),
    outcome: Optional[str] = typer.Argument(None, help="shipped | blocked | abandoned | exploratory"),
    latest: bool = typer.Option(False, "--latest", "-l", help="Tag the most recently active session in the current directory."),
    any_cwd: bool = typer.Option(False, "--any-cwd", help="With --latest, do not restrict to the current directory."),
    note: Optional[str] = typer.Option(None, "--note", "-n", help="Free-text annotation stored alongside the outcome."),
    untagged: bool = typer.Option(False, "--untagged", help="Bulk mode: apply to runs currently tagged 'untagged'."),
    older_than: Optional[str] = typer.Option(None, "--older-than", help="Bulk mode: restrict to runs older than e.g. '30d', '2w', '1m'."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the bulk confirmation prompt."),
):
    """Tag a run with an outcome. Accepts a hex run ID, a search query, --latest, or bulk filters."""
    bulk = untagged or older_than is not None
    # In bulk or --latest mode, the outcome comes in via the run_id positional (since the
    # outcome positional is empty). Shift it.
    if (latest or bulk) and outcome is None and run_id is not None:
        outcome, run_id = run_id, None

    if outcome is None or outcome not in _VALID_OUTCOMES:
        console.print(f"[red]Invalid outcome. Choose: {', '.join(sorted(_VALID_OUTCOMES))}[/red]")
        raise typer.Exit(1)

    conn = get_connection()
    init_db(conn)

    if bulk:
        if run_id is not None:
            console.print("[red]Bulk mode does not accept a run ID. Drop it, or run individually without --untagged/--older-than.[/red]")
            conn.close()
            raise typer.Exit(1)
        days = None
        if older_than is not None:
            days = _parse_duration_to_days(older_than)
            if days is None or days <= 0:
                console.print(f"[red]Invalid --older-than '{older_than}'. Use forms like '30d', '2w', '1m'.[/red]")
                conn.close()
                raise typer.Exit(1)
        count = count_runs_for_bulk(conn, untagged_only=untagged, older_than_days=days)
        if count == 0:
            console.print("[yellow]No runs match the bulk filter — nothing to do.[/yellow]")
            conn.close()
            return
        filters = []
        if untagged:
            filters.append("untagged")
        if days is not None:
            filters.append(f"older than {days}d")
        scope = ", ".join(filters)
        console.print(f"[yellow]This will tag {count} run{'s' if count != 1 else ''} ({scope}) as [bold]{outcome}[/bold].[/yellow]")
        if not yes:
            try:
                ok = typer.confirm("Proceed?", default=False)
            except (EOFError, typer.Abort):
                ok = False
            if not ok:
                console.print("[yellow]Cancelled.[/yellow]")
                conn.close()
                raise typer.Exit(1)
        n = bulk_set_outcome(conn, outcome, untagged_only=untagged, older_than_days=days, note=note)
        conn.close()
        console.print(f"[green]Tagged {n} run{'s' if n != 1 else ''} as {outcome}.[/green]")
        return

    if latest:
        cwd_basename = None if any_cwd else Path.cwd().name
        row = get_latest_run(conn, cwd_basename)
        if not row and cwd_basename:
            conn.close()
            console.print(f"[red]No sessions found for cwd '{cwd_basename}'. Try --any-cwd.[/red]")
            raise typer.Exit(1)
        if not row:
            conn.close()
            console.print("[red]No sessions found.[/red]")
            raise typer.Exit(1)
        set_outcome(conn, row["id"], outcome, note=note)
        conn.close()
        scope = "any cwd" if any_cwd else f"cwd '{cwd_basename}'"
        console.print(f"[green]Tagged {row['id'][:8]} as {outcome}[/green] (latest in {scope}).")
        return

    if run_id is None:
        console.print("[red]Missing run_id. Pass a run ID, a search query, or use --latest.[/red]")
        conn.close()
        raise typer.Exit(1)

    if _is_hex_id(run_id):
        row = conn.execute("SELECT id FROM runs WHERE id LIKE ?", (f"{run_id}%",)).fetchone()
        if not row:
            console.print(f"[red]Run not found: {run_id}[/red]")
            conn.close()
            raise typer.Exit(1)
        set_outcome(conn, row["id"], outcome, note=note)
        conn.close()
        console.print(f"[green]Tagged {row['id'][:8]} as {outcome}.[/green]")
        return

    # Treat as search query
    matches = search_runs(conn, run_id)
    if not matches:
        console.print(f"[red]No runs found matching: {run_id}[/red]")
        conn.close()
        raise typer.Exit(1)
    if len(matches) > 1:
        row = _pick_run_interactively(matches, run_id)
        if row is None:
            console.print("[yellow]Cancelled.[/yellow]")
            conn.close()
            raise typer.Exit(1)
    else:
        row = matches[0]
    set_outcome(conn, row["id"], outcome, note=note)
    conn.close()
    console.print(f"[green]Tagged {row['id'][:8]} as {outcome}.[/green]")


@app.command()
def export(
    run_id: str = typer.Argument(..., help="Run ID or prefix"),
    out: Optional[str] = typer.Option(None, "--out", "-o", help="Output file (default: stdout)"),
):
    """Export a run as a markdown report."""
    conn = get_connection()
    init_db(conn)
    row = conn.execute("SELECT * FROM runs WHERE id LIKE ?", (f"{run_id}%",)).fetchone()
    if not row:
        console.print(f"[red]Run not found: {run_id}[/red]")
        conn.close()
        raise typer.Exit(1)
    events = get_run_events(conn, row["id"])
    conn.close()

    try:
        tag_note = row["tag_note"] or ""
    except (IndexError, KeyError):
        tag_note = ""
    lines = [
        f"# Session: {row['id'][:8]}",
        f"**Source:** {row['source']}  ",
        f"**Date:** {row['started_at'][:16]} → {row['ended_at'][:16]}  ",
        f"**Outcome:** {row['outcome']}  ",
    ]
    if tag_note:
        lines.append(f"**Note:** {tag_note}  ")
    lines += [
        f"**Tokens:** {row['tokens_in']:,} in / {row['tokens_out']:,} out | Cache read: {row['cache_read']:,}  ",
        f"**API-equiv:** ${row['cost_usd']:.4f} _(what this would cost on the API; Max/Pro subscribers pay a flat fee)_",
        "",
        "## Goal",
        row["user_goal"] or "_no goal recorded_",
        "",
    ]
    if events["tool_calls"]:
        lines += ["## Tool Calls", ""]
        for tc in events["tool_calls"]:
            icon = "✓" if tc["status"] == "success" else "✗"
            lines.append(f"- {icon} **{tc['tool_name']}** `{tc['input_summary'][:80]}`")
        lines.append("")
    if events["shell_commands"]:
        lines += ["## Shell Commands", ""]
        for sc in events["shell_commands"]:
            lines.append(f"- `[{sc['exit_code']}]` `{sc['command'][:80]}`")
        lines.append("")
    if events["errors"]:
        lines += ["## Errors", ""]
        for err in events["errors"]:
            lines.append(f"- {err['message'][:120]}")
        lines.append("")
    if row["final_summary"]:
        lines += ["## Summary", row["final_summary"], ""]

    md = "\n".join(lines)
    if out:
        Path(out).write_text(md, encoding="utf-8")
        console.print(f"[green]Written to {out}[/green]")
    else:
        print(md)


@app.command()
def info():
    """Show version, DB location, run counts, and command reference."""
    from importlib.metadata import version as pkg_version, PackageNotFoundError
    try:
        v = pkg_version("agent-recorder")
    except PackageNotFoundError:
        v = "dev"

    conn = get_connection()
    init_db(conn)
    total = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    untagged = conn.execute("SELECT COUNT(*) FROM runs WHERE outcome='untagged'").fetchone()[0]
    last_row = conn.execute("SELECT started_at FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    conn.close()

    db_size = DB_PATH.stat().st_size // 1024 if DB_PATH.exists() else 0
    last = last_row[0][:16] if last_row else "—"

    from rich.table import Table as RTable
    from rich.panel import Panel as RPanel

    console.print(RPanel(
        f"[bold cyan]Agent Flight Recorder[/bold cyan]  v{v}\n"
        f"[dim]DB:[/dim] {DB_PATH}  [dim]({db_size} KB)[/dim]\n"
        f"[dim]Runs:[/dim] {total} total, {untagged} untagged  [dim]| Last:[/dim] {last}",
        title="afr info",
    ))

    cmds = RTable(show_header=False, box=None, padding=(0, 2))
    cmds.add_column("cmd", style="cyan", no_wrap=True)
    cmds.add_column("desc")
    rows = [
        ("afr ingest claude",           "Pull latest Claude Code sessions into DB"),
        ("afr list [-d N]",             "List runs (optionally last N days)"),
        ("afr search <query>",          "Full-text search across goals/summaries"),
        ("afr show <id>",               "Full detail for a run"),
        ("afr tag <id|query> <outcome>","Tag a run (shipped/blocked/abandoned/exploratory)"),
        ("afr export <id> [-o file]",   "Export run as markdown"),
        ("afr stats [-d N]",            "Token/tool/error summary"),
        ("afr extract-skills",          "Cluster sessions → propose skills"),
    ]
    for cmd, desc in rows:
        cmds.add_row(cmd, desc)
    console.print(cmds)


@app.command("install-hooks")
def install_hooks(apply: bool = typer.Option(False, "--apply", help="Write to ~/.claude/settings.json. Prints snippet otherwise.")):
    """Print or install the Claude Code Stop hook config."""
    from .hooks import install
    typer.echo(install(apply=apply))


@app.command("extract-skills")
def extract_skills(
    min_runs: int = typer.Option(3, "--min-runs", help="Minimum sessions per cluster"),
):
    """Cluster sessions and propose SKILL.md candidates."""
    conn = get_connection()
    init_db(conn)
    run_extraction(conn, min_runs)
    conn.close()
