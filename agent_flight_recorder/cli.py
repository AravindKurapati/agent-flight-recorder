import sys
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from pathlib import Path
from typing import Optional
import typer
from .db import get_connection, init_db, DB_PATH, list_runs, get_run, get_run_events, search_runs, set_outcome, get_latest_run
from .ingester import ingest_claude, ingest_codex
from .analyzers.stats import get_stats
from .analyzers.skill_extractor import run_extraction
from .render.terminal import console, print_run_list, print_run_detail, print_stats

app = typer.Typer(name="afr", help="Agent Flight Recorder — local AI session observability", add_completion=False)

_VALID_OUTCOMES = {"shipped", "blocked", "abandoned", "exploratory"}


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


def _is_hex_id(s: str) -> bool:
    return len(s) >= 6 and all(c in "0123456789abcdefABCDEF" for c in s)


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
    run_id: Optional[str] = typer.Argument(None, help="Run ID/prefix, or search text. Omit with --latest."),
    outcome: Optional[str] = typer.Argument(None, help="shipped | blocked | abandoned | exploratory"),
    latest: bool = typer.Option(False, "--latest", "-l", help="Tag the most recently active session in the current directory."),
    any_cwd: bool = typer.Option(False, "--any-cwd", help="With --latest, do not restrict to the current directory."),
    note: Optional[str] = typer.Option(None, "--note", "-n", help="Free-text annotation stored alongside the outcome."),
):
    """Tag a run with an outcome. Accepts a hex run ID, a search query, or --latest."""
    # --latest shifts args: `afr tag --latest shipped` puts the outcome in run_id.
    if latest and outcome is None and run_id is not None:
        outcome, run_id = run_id, None

    if outcome is None or outcome not in _VALID_OUTCOMES:
        console.print(f"[red]Invalid outcome. Choose: {', '.join(sorted(_VALID_OUTCOMES))}[/red]")
        raise typer.Exit(1)

    conn = get_connection()
    init_db(conn)

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
