import sqlite3
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

_OUTCOME_COLORS = {
    "shipped": "green", "blocked": "red",
    "abandoned": "yellow", "exploratory": "blue", "untagged": "dim",
}


def print_run_list(runs: list) -> None:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Source", width=8)
    table.add_column("Goal", width=42)
    table.add_column("Outcome", width=12)
    table.add_column("Tokensâ†‘", justify="right", width=9)
    table.add_column("Date", width=17)
    for r in runs:
        color = _OUTCOME_COLORS.get(r["outcome"], "dim")
        goal = r["user_goal"]
        goal_cell = goal[:40] + ".." if len(goal) > 42 else goal
        table.add_row(
            r["id"][:8],
            r["source"],
            goal_cell,
            f"[{color}]{r['outcome']}[/{color}]",
            str(r["tokens_in"]),
            r["started_at"][:16] if r["started_at"] else "",
        )
    console.print(table)


def print_run_detail(run, events: dict) -> None:
    console.print(Panel(
        f"[bold]{run['user_goal']}[/bold]\n"
        f"[dim]{run['source']} | {run['started_at'][:16]} â†’ {run['ended_at'][:16]} | {run['outcome']}[/dim]"
    ))
    if events["tool_calls"]:
        console.print("\n[bold]Tool Calls[/bold]")
        for tc in events["tool_calls"]:
            icon = "[green]âœ“[/green]" if tc["status"] == "success" else "[red]âœ—[/red]"
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
            console.print(f"  [red]â€¢[/red] {err['message'][:80]}")
    console.print(
        f"\n[dim]Tokens: {run['tokens_in']} in / {run['tokens_out']} out"
        f" | Cache read: {run['cache_read']} | Cost: ${run['cost_usd']:.4f}[/dim]"
    )


def print_stats(stats: dict) -> None:
    if not stats:
        console.print("[yellow]No runs found. Run `afr ingest claude` first.[/yellow]")
        return
    console.print(Panel("[bold]Agent Flight Recorder â€” Stats[/bold]"))
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
