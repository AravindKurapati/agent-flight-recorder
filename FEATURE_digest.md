# FEATURE: Weekly/monthly digest (`afr digest`)

Status: design approved 2026-07-23.

## Problem

`afr` records every run's cost, tokens, outcome, and goal, but there's no rollup
view answering "what did I actually do this week?" — how many sessions, how much
they cost, what got shipped vs abandoned, and whether there's a pattern worth
noticing (e.g. a run of unfinished sessions).

## Goals

- One command, `afr digest`, producing a human-readable weekly or monthly report
  from data already in the DB (no new capture logic).
- Surface totals (sessions, cost, tokens), outcome breakdown, and a per-project
  breakdown.
- Surface two derived signals not shown by `afr stats` today: an abandoned/blocked
  streak callout, and cost-per-shipped-outcome ratio.
- A `--json` mode that dumps the same data plus each run's `user_goal` /
  `final_summary`, structured for an external agent to turn into a narrative
  topic summary — afr does not call an LLM itself.

## Non-goals

- No email sending, no scheduling, no LLM calls inside afr. Per the project's
  "no external services, no API keys" rule, narrative topic summarization and
  delivery (e.g. emailing the digest) are left to a separate wrapping layer — a
  Claude Code slash command or scheduled agent that runs `afr digest --json` and
  has its own model access. Out of scope for this spec.
- No new tables or columns. Purely a read/aggregate over `runs` (and existing
  child tables via `analyzers/stats.py`).
- No topic clustering/NLP inside afr (that's `extract-skills`'s job, and out of
  scope here — v1 groups by project only).

## Approach

New analyzer function `analyzers/digest.py::get_digest(conn, days, ...)` that
calls the existing `analyzers/stats.get_stats(conn, days)` for the base numbers,
then extends the result with:

- `by_project`: dict of `project_name -> {runs, cost_usd, tokens_in, tokens_out,
  outcomes}`, grouped by `basename(cwd)` (same field `afr resume` already
  populates; falls back to `"unknown"` when `cwd` is empty, e.g. pre-0.2.0 runs).
- `abandoned_streak`: count of consecutive most-recent runs (ordered by
  `started_at` desc) whose outcome is `abandoned` or `blocked`, stopping at the
  first `shipped`/`exploratory`/other. 0 if the most recent run isn't
  abandoned/blocked.
- `cost_per_shipped`: `total_cost_usd / count(outcome == 'shipped')`, or `None`
  if zero shipped runs (avoid divide-by-zero; rendered as "n/a").
- `sessions`: list of `{id (8-char prefix), started_at, cwd_project, outcome,
  user_goal, final_summary}` for every run in range, ordered by `started_at`
  desc — this is the payload a wrapping agent reads for topic summarization.

Thin CLI command `afr digest` calls this and renders via `render/terminal.py`
(new panel function) or, with `--json`, prints `json.dumps(...)` directly to
stdout with no Rich formatting (so it pipes cleanly to another process).

### Data flow

`afr digest [--week|--month] [--json]` -> `get_stats()` (existing) -> extend with
`by_project` / `abandoned_streak` / `cost_per_shipped` / `sessions` in
`digest.py` -> either Rich panel (default) or raw JSON (`--json`) to stdout.

## DB impact

None. Read-only over `runs` and existing child tables (`tool_calls`, `errors`,
`shell_commands`) via the existing `get_stats` query. No new tables, no new
columns, no `_ensure_column` calls needed. No `SCHEMA.md` changes (afr has none;
schema stays inline in `db.py` as-is).

## CLI surface

- `afr digest` — defaults to `--week` (last 7 days).
- `afr digest --week` — last 7 days.
- `afr digest --month` — last 30 days.
- `afr digest --json` — same window (respects `--week`/`--month`), machine-
  readable output instead of the Rich panel.

## Output (sketch)

```
Digest: last 7 days (2026-07-16 -> 2026-07-23)

  Sessions:     14        Cost: $8.42        Tokens: 210k in / 340k out
  Outcomes:     shipped 6 | abandoned 4 | blocked 1 | exploratory 3
  Cost/shipped: $1.40

  ⚠ Last 3 sessions in a row were abandoned/blocked — worth a look.

  By project:
    agent-flight-recorder   6 runs   $3.10   shipped 4, abandoned 2
    session-sidekick        5 runs   $2.90   shipped 1, blocked 1, exploratory 3
    locus                   3 runs   $2.42   shipped 1, abandoned 2
```

`--json` shape:

```json
{
  "period": {"days": 7, "since": "2026-07-16T00:00:00Z"},
  "total_runs": 14,
  "total_cost_usd": 8.42,
  "total_tokens_in": 210000,
  "total_tokens_out": 340000,
  "outcomes": {"shipped": 6, "abandoned": 4, "blocked": 1, "exploratory": 3},
  "cost_per_shipped": 1.40,
  "abandoned_streak": 3,
  "by_project": {
    "agent-flight-recorder": {"runs": 6, "cost_usd": 3.10, "outcomes": {"shipped": 4, "abandoned": 2}}
  },
  "sessions": [
    {"id": "6c84c429", "started_at": "2026-07-22T14:00:00Z", "cwd_project": "locus",
     "outcome": "abandoned", "user_goal": "...", "final_summary": "..."}
  ]
}
```

## Error handling

- No runs in range: print "No sessions in the last N days." with the totals
  section omitted; exit 0. `--json` still emits the shape with empty lists /
  zeroed totals (not an error — a wrapping agent shouldn't need to special-case
  a missing key).
- `cost_per_shipped` with zero shipped runs: `None` in JSON, "n/a" in the panel.
- Runs with empty `cwd`: grouped under `"unknown"` in `by_project`.
- Invalid combination (both `--week` and `--month` passed): last flag wins,
  consistent with Typer's default flag behavior; no explicit validation needed.

## Testing (TDD — tests written first)

- `get_digest()` over synthetic run rows: empty set, single run, mixed outcomes,
  runs with empty `cwd`.
- `abandoned_streak`: 0 abandoned, streak at the head, streak broken by a
  shipped run in the middle, all abandoned.
- `cost_per_shipped`: zero shipped (returns `None`), normal division.
- `by_project` grouping: multiple projects, empty cwd bucket.
- CLI: `--week` vs `--month` window boundaries (reuses `get_stats`'s existing
  `days` param, already tested indirectly); `--json` output is valid JSON and
  matches the documented shape.

## Conventions

- Spec: this file (`FEATURE_digest.md`), per the user's global rule.
- Implementation follows TDD (tests before code), consistent with repo history.
- Relationship to other in-flight afr ideas (session diff, error fingerprinting,
  afr↔sidekick integration): independent, no shared surface — each gets its own
  `FEATURE_*.md` spec.
