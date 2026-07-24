# FEATURE: Session diff (`afr diff`)

Status: design approved 2026-07-23.

## Problem

`afr show <id>` inspects one session at a time. There's no way to answer "why
did this attempt take 3 sessions to fix, and what did the agent do differently
between them?" without manually re-reading each `afr show` output side by side.

## Goals

- `afr diff <id1> <id2>` — resolve two id/prefixes the same way `afr resume`
  does, and print a side-by-side summary: cost, tokens, duration, outcome,
  tool-call count, error count, shell-failure count.
- `afr diff <id1> <id2> --full` — additionally show the two sessions' tool-call
  sequences side by side, aligned so matching tool calls (by name) land on the
  same row and divergent stretches show blanks on the other side (`diff -y`
  style), computed with stdlib `difflib`.
- Reuse existing resolution/event-fetching code (`resolve_runs_by_prefix`,
  `get_run_events`) rather than duplicating it.

## Non-goals

- No semantic diff of tool call *arguments* — matching is by tool name only,
  per the design decision (exact args rarely repeat between two independent
  attempts at the same problem; structural divergence is the useful signal).
- No diffing of more than two sessions at once.
- No new DB tables/columns — pure read + pure aggregation.

## Approach

New pure-logic module `analyzers/diff.py` (no DB access, mirrors the style of
`analyzers/windows.py`):

- `compute_summary(run, events: dict) -> dict` — one side of the comparison.
  Takes a `runs` row and the `get_run_events` dict, returns:
  `{cost_usd, tokens_in, tokens_out, duration_seconds (Optional[float], None if
  ended_at is empty), outcome, tool_call_count, error_count, shell_failure_count}`.
  `shell_failure_count` = shell_commands with non-null, non-zero `exit_code`
  (same definition `analyzers/stats.py` already uses).
- `align_tool_calls(names_a: list[str], names_b: list[str]) -> list[tuple[Optional[str], Optional[str]]]`
  — uses `difflib.SequenceMatcher(None, names_a, names_b).get_opcodes()`.
  For `equal` opcodes, emit `(a, b)` pairs (same tool, same row). For
  `replace`/`delete`/`insert`, emit rows padding the shorter side with `None`
  so both columns stay visually aligned to their divergence point.

Thin CLI command `afr diff <id1> <id2> [--full]` in `cli.py`:
1. Resolve each id via `resolve_runs_by_prefix`. If a prefix matches 0 runs,
   print an error and exit 1. If it matches >1, print the matches (reusing
   `print_run_list`) and exit 1 — no arbitrary pick, consistent with `afr
   resume`'s ambiguity handling.
2. `get_run_events` for both resolved runs, `compute_summary` for both.
3. New `render/terminal.py::print_diff(run_a, run_b, summary_a, summary_b,
   alignment: Optional[list] = None)` renders the summary table, and if
   `alignment` is provided (only when `--full` is passed), renders the
   tool-call sequence panel below it.

### Data flow

`afr diff <id1> <id2> [--full]` → resolve both ids → `get_run_events` ×2 →
`compute_summary` ×2 → (if `--full`) collect tool-call name lists →
`align_tool_calls` → `print_diff`.

## DB impact

None. Read-only over `runs` and `tool_calls`/`shell_commands`/`errors` via the
existing `get_run_events` query. No new tables, no new columns.

## CLI surface

- `afr diff <id1> <id2>` — summary table only.
- `afr diff <id1> <id2> --full` — summary table + aligned tool-call sequence.

## Output (sketch)

Summary (default):

```
Diff: 6c84c429 vs a1b2c3d4

                    6c84c429              a1b2c3d4
  Outcome:          abandoned             shipped
  Cost:             $1.20                 $2.80
  Tokens:           40k in / 12k out      95k in / 30k out
  Duration:         14m                   38m
  Tool calls:       9                     22
  Errors:           2                     0
  Shell failures:   3                     1
```

`--full` appends:

```
Tool-call sequence
  6c84c429              a1b2c3d4
  Read                  Read
  Bash                  Bash
  Bash                  Grep
  -                      Edit
  Edit                  Edit
  Bash                  Bash
```

(`-` marks a row where one side has no matching call — the aligned-blank
behavior from `align_tool_calls`.)

## Error handling

- Prefix matches 0 runs: `console.print("[red]No run matches '<id>'.[/red]")`,
  exit 1.
- Prefix matches >1 runs: print the ambiguous matches (`print_run_list`) and a
  hint to add characters, exit 1 — for *either* id independently; if both are
  ambiguous, report both before exiting.
- `ended_at` empty (session still open / never finalized): `duration_seconds`
  is `None`; rendered as "in progress" rather than a crash or "0s".
- Identical id passed twice: allowed (diffs a run against itself — trivially
  all-equal), no special-casing needed.

## Testing (TDD — tests written first)

- `compute_summary`: normal run, run with empty `ended_at` (duration `None`),
  run with zero tool calls/errors, shell-failure counting matches
  `analyzers/stats.py`'s existing definition.
- `align_tool_calls`: identical sequences, completely disjoint sequences,
  one-sided insert/delete in the middle, empty vs non-empty, both empty.
- CLI: valid two-id diff (exit 0, both outcomes/costs appear in output),
  ambiguous prefix (exit 1, matches listed), not-found id (exit 1),
  `--full` output includes the tool-call sequence section, identical id
  passed twice.

## Conventions

- Spec: this file (`FEATURE_diff.md`), per the user's global rule.
- Implementation follows TDD (tests before code).
- Independent of `FEATURE_digest.md` and the other in-flight ideas (error
  fingerprinting, afr↔sidekick integration) — no shared surface.
