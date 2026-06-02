import sqlite3
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

_OUTCOME_COLORS = {
    "shipped": "green", "blocked": "red",
    "abandoned": "yellow", "exploratory": "blue", "untagged": "dim",
}


def _row_get(row, key, default=""):
    """sqlite3.Row.__getitem__ raises IndexError on missing keys; tolerate older DBs."""
    try:
        return row[key] or default
    except (IndexError, KeyError):
        return default


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def print_run_list(runs: list, numbered: bool = False) -> None:
    table = Table(show_header=True, header_style="bold magenta")
    if numbered:
        table.add_column("#", style="bold cyan", no_wrap=True, min_width=3)
    table.add_column("ID", style="dim", no_wrap=True, min_width=8)
    table.add_column("Source", no_wrap=True, min_width=6)
    table.add_column("Goal", min_width=30)
    table.add_column("Outcome", no_wrap=True, min_width=11)
    table.add_column("In", justify="right", no_wrap=True, min_width=6)
    table.add_column("Out", justify="right", no_wrap=True, min_width=7)
    table.add_column("Date", no_wrap=True, min_width=13)
    for i, r in enumerate(runs, start=1):
        color = _OUTCOME_COLORS.get(r["outcome"], "dim")
        goal = r["user_goal"] or ""
        goal_cell = goal[:40] + ".." if len(goal) > 42 else goal
        row = [
            r["id"][:8],
            r["source"],
            goal_cell,
            f"[{color}]{r['outcome']}[/{color}]",
            _fmt_tokens(r["tokens_in"]),
            _fmt_tokens(r["tokens_out"]),
            r["started_at"][:16] if r["started_at"] else "",
        ]
        if numbered:
            row.insert(0, str(i))
        table.add_row(*row)
    console.print(table)


def print_run_detail(run, events: dict) -> None:
    note = _row_get(run, "tag_note")
    note_line = f"\n[italic]Note:[/italic] {note}" if note else ""
    console.print(Panel(
        f"[bold]{run['user_goal']}[/bold]\n"
        f"[dim]{run['source']} | {run['started_at'][:16]} → {run['ended_at'][:16]} | {run['outcome']}[/dim]"
        f"{note_line}"
    ))
    if events["tool_calls"]:
        console.print("\n[bold]Tool Calls[/bold]")
        for tc in events["tool_calls"]:
            icon = "[green]✓[/green]" if tc["status"] == "success" else "[red]✗[/red]"
            console.print(f"  {icon} [cyan]{tc['tool_name']}[/cyan]  {tc['input_summary'][:60]}")
    if events["shell_commands"]:
        console.print("\n[bold]Shell Commands[/bold]")
        for sc in events["shell_commands"]:
            ec = sc["exit_code"]
            code_fmt = f"[green]{ec}[/green]" if ec == 0 else f"[red]{ec}[/red]"
            console.print(f"  [{code_fmt}] [yellow]{sc['command'][:70]}[/yellow]")
    if events["errors"]:
        console.print("\n[bold red]Errors[/bold red]")
        for err in events["errors"]:
            console.print(f"  [red]•[/red] {err['message'][:80]}")
    console.print(
        f"\n[dim]Tokens: {run['tokens_in']} in / {run['tokens_out']} out"
        f" | Cache read: {run['cache_read']} | API-equiv: ${run['cost_usd']:.4f}[/dim]"
    )


def print_stats(stats: dict) -> None:
    if not stats:
        console.print("[yellow]No runs found. Run `afr ingest claude` first.[/yellow]")
        return
    console.print(Panel("[bold]Agent Flight Recorder — Stats[/bold]"))
    console.print(f"Total runs: [bold]{stats['total_runs']}[/bold]")
    console.print("\n[bold]Outcomes[/bold]")
    for outcome, count in stats["outcomes"].items():
        color = _OUTCOME_COLORS.get(outcome, "dim")
        console.print(f"  [{color}]{outcome}[/{color}]: {count}")
    console.print(f"\n[bold]Tokens[/bold]  {stats['total_tokens_in']:,} in / {stats['total_tokens_out']:,} out")
    console.print(f"[bold]Errors[/bold]  {stats['error_count']} tool | {stats['shell_failures']} shell")
    if stats["top_tools"]:
        console.print("\n[bold]Top Tools[/bold]")
        for tool, count in stats["top_tools"]:
            console.print(f"  {tool}: {count}")


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
