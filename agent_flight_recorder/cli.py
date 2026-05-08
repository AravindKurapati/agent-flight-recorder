import sys
if sys.platform == "win32":
    import ctypes
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from typing import Optional
import typer
from .db import get_connection, init_db, DB_PATH, list_runs, get_run, get_run_events, search_runs, set_outcome
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
def stats(days: Optional[int] = typer.Option(None, "--days", "-d")):
    """Show session statistics."""
    conn = get_connection()
    init_db(conn)
    s = get_stats(conn, days)
    conn.close()
    print_stats(s)


@app.command()
def tag(
    run_id: str = typer.Argument(...),
    outcome: str = typer.Argument(..., help="shipped | blocked | abandoned | exploratory"),
):
    """Tag a run with an outcome."""
    if outcome not in _VALID_OUTCOMES:
        console.print(f"[red]Invalid outcome. Choose: {', '.join(sorted(_VALID_OUTCOMES))}[/red]")
        raise typer.Exit(1)
    conn = get_connection()
    init_db(conn)
    row = conn.execute("SELECT id FROM runs WHERE id LIKE ?", (f"{run_id}%",)).fetchone()
    if not row:
        console.print(f"[red]Run not found: {run_id}[/red]")
        conn.close()
        raise typer.Exit(1)
    set_outcome(conn, row["id"], outcome)
    conn.close()
    console.print(f"[green]Tagged {row['id'][:8]} as {outcome}.[/green]")


@app.command("extract-skills")
def extract_skills(
    min_runs: int = typer.Option(3, "--min-runs", help="Minimum sessions per cluster"),
):
    """Cluster sessions and propose SKILL.md candidates."""
    conn = get_connection()
    init_db(conn)
    run_extraction(conn, min_runs)
    conn.close()
