# FEATURE: Recurring error fingerprinting (`afr errors`)

Status: design approved 2026-07-23.

## Problem

`afr` records every failed shell command (`shell_commands.exit_code != 0`), but
there's no rollup answering "have I hit this exact failure before, and how many
times?" Recurring failures (a flaky test command, a misremembered CLI flag, an
env issue) currently only surface by re-reading `afr show` output session by
session.

## Goals

- `afr errors` — list recurring failed-shell-command fingerprints across all
  recorded sessions, ranked by how often they've occurred.
- Fingerprint = exact `(command, exit_code)` pair. No normalization/fuzzy
  matching in v1 — simple, no false-positive grouping.
- `--min-count N` (default 2) — only show fingerprints seen at least N times;
  a single failure isn't "recurring."
- `--days N` — restrict to a recent window, same convention as `afr stats
  --days`.
- Each entry shows count, first/last seen, and which sessions (8-char id
  prefixes) hit it, so the user can jump to `afr show <id>` for full context.

## Non-goals

- No fuzzy/normalized command matching (e.g. stripping variable args like file
  paths) — exact string match only. Can be revisited later if exact-match
  proves too narrow.
- No coverage of the `errors` table (tool/agent-sourced errors) — shell
  command failures only, per the design decision. Free-text error messages are
  noisier to dedupe and are a separate future scope if wanted.
- No integration into `afr show` or `afr digest` — standalone command only for
  v1, to avoid coupling two features before the standalone view proves useful.
- No new DB tables/columns — pure read + aggregation over `shell_commands`.

## Approach

New pure-logic analyzer `analyzers/errors.py::get_recurring_errors(conn,
min_count=2, days=None) -> list[dict]`, mirroring `analyzers/stats.py`'s
optional `days` window parameter and `analyzers/skill_extractor.py`'s
`min_runs` threshold pattern:

- Query `shell_commands` (optionally joined to `runs` for the `days` filter on
  `runs.started_at`, consistent with how `stats.get_stats` scopes by run) where
  `exit_code IS NOT NULL AND exit_code != 0`.
- Group by `(command, exit_code)`. For each group with `count >= min_count`,
  build:
  `{command: str, exit_code: int, count: int, first_seen: str, last_seen: str,
  run_ids: list[str] (8-char prefixes, de-duplicated, ordered by first
  occurrence)}`.
- Sort by `count` descending, then `last_seen` descending as a tiebreaker.

Thin CLI command `afr errors [--min-count N] [--days N]` in `cli.py`:
1. Call `get_recurring_errors(conn, min_count, days)`.
2. Render via new `render/terminal.py::print_errors(entries: list[dict])`.

### Data flow

`afr errors [--min-count N] [--days N]` → `get_recurring_errors` (SQL over
`shell_commands` + `runs` for the day filter) → group/sort in Python →
`print_errors`.

## DB impact

None. Read-only over `shell_commands` and `runs` (for the optional `days`
filter, joining on `run_id`). No new tables, no new columns.

## CLI surface

- `afr errors` — recurring failures, all-time, min-count 2.
- `afr errors --min-count 3` — raise the threshold.
- `afr errors --days 30` — restrict to a recent window.
- Flags combine: `afr errors --min-count 3 --days 30`.

## Output (sketch)

```
Recurring shell failures (min 2 occurrences)

  Count  Exit  Command                          First seen   Last seen    Sessions
  5      1     pytest tests/test_foo.py          2026-06-01   2026-07-20   6c84c429, a1b2c3d4, ...
  3      127   npm run build                     2026-06-15   2026-07-18   f3e2d1c0, ...
```

## Error handling

- No failed shell commands at all: print "No recurring failures found." and
  exit 0 (not an error — a clean run history is a good outcome).
- `min_count` less than 1: treat as 1 (every failure shows, including
  one-offs) rather than crashing; no need for explicit validation error.
- `--days` with no runs in range: same "No recurring failures found." message.
- Very long `command` strings: truncate for terminal display (reuse the
  existing truncation pattern from `print_run_list`'s goal column), full value
  still available if the user greps the DB directly.

## Testing (TDD — tests written first)

- `get_recurring_errors`: no failures (empty list), single failure below
  min-count threshold (excluded), same command with different exit codes
  (kept as separate fingerprints), same command+exit_code across multiple runs
  (counted and deduplicated into one entry with all run ids), `--days` window
  filtering, `min_count` boundary (exactly at threshold is included).
- Sort order: highest count first, `last_seen` as tiebreaker.
- CLI: default invocation, `--min-count`, `--days`, empty-result message,
  combined flags.

## Conventions

- Spec: this file (`FEATURE_errors.md`), per the user's global rule.
- Implementation follows TDD (tests before code).
- Independent of `FEATURE_digest.md`, `FEATURE_diff.md`, and the afr↔sidekick
  integration — no shared surface.
