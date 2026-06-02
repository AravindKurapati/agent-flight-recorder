# FEATURE: Session windows (`afr windows`)

Status: design approved 2026-06-02. Approach A (Python analyzer + dedicated command).

## Problem

afr records every Claude Code / Codex run with real `started_at` timestamps and
token costs, but offers no view of how that activity maps onto Anthropic's
**5-hour rolling usage windows**. The user wants to answer, from ground-truth
recorded data:

- How many distinct 5-hour windows have I actually used today / this week?
- How many fresh 5-hour windows can still fit before my weekly cap resets?

This is the **time/window** side of the question. It deliberately does NOT model
the weekly token-budget ceiling; that is `claude-burnrate`'s job and requires
pasting `/usage` output. afr's unique contribution is reconstructing *actual*
windows from real run timestamps.

## Goals

- Reconstruct 5-hour windows from recorded runs, matching Anthropic's model
  ("a window opens on first activity and lasts 5 hours").
- Report windows used today and this week.
- Report windows still available before the weekly reset (time-bounded,
  back-to-back).
- Read-only over `runs`. Configuration set once and stored locally.

## Non-goals

- No modeling of the weekly token/usage cap percentage (opaque to afr; belongs
  to burnrate + `/usage`).
- No live polling of Anthropic. Everything derives from the local DB + config.
- No artificial "max windows per week" cap in v1 (can be added later if wanted).

## Approach (A)

New analyzer module `analyzers/windows.py` mirroring `analyzers/stats.py`; thin
CLI command `afr windows`; config in a new key-value `config` table. Window logic
lives in one testable unit.

### Data flow

`afr windows` -> load runs with non-empty `started_at` -> `analyzers/windows.py`
reconstructs windows + computes counts -> `render/terminal.py` prints a panel.
Writes happen only via `afr config set`.

### Window reconstruction (core algorithm)

1. Load runs with non-empty `started_at`, sort ascending. Parse ISO8601:
   trailing `Z` -> `+00:00`, store as timezone-aware UTC `datetime`.
2. Greedy sessionization:
   - The first run, or any run whose start is `>= current_anchor + 5h`, opens a
     new window anchored at that run's start.
   - All other runs attach to the currently open window.
3. Each reconstructed window carries: `anchor` (UTC), `end = anchor + 5h`,
   `run_count`, summed `cost_usd`, summed tokens.

This reproduces the real model exactly: idle gaps do not create windows; the
clock is set by the first activity after the previous window expires.

### The two numbers

- **Used today / this week**: count windows whose `anchor`, converted to the
  configured display timezone, falls in the current calendar day / on or after
  the most recent weekly-reset instant.
- **Available before reset**:
  - `next_reset` = next configured weekly-reset occurrence after `now`.
  - If the latest window is still open (`now < anchor + 5h`), report its
    remaining time separately as the active window.
  - `effective_start` = `latest_window.end` if a window is currently open, else
    `now`.
  - `available_full = max(0, floor((next_reset - effective_start) / 5h))`.
  - Any non-zero remainder is reported as a partial trailing window of `Yh`.
  - Headline: "N more 5-hour windows fit before reset (<reset, display tz>),
    plus a partial Yh tail." If a window is active: also "Active window: Xh left."

## DB impact

New additive table, created in `db.py::init_db` alongside existing tables, never
dropped (consistent with afr's additive-migration philosophy):

```sql
CREATE TABLE IF NOT EXISTS config (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
```

User-facing keys (what `afr config set <key> <value>` accepts):
- `weekly-reset` — value `"<Weekday> HH:MM"`, e.g. `"Wed 00:00"`, interpreted in
  the display timezone. Parsed and stored internally as `weekly_reset_weekday`
  (`0`-`6`) + `weekly_reset_time` (`HH:MM`).
- `timezone` — IANA name (e.g. `America/New_York`), resolved via stdlib
  `zoneinfo` for correct DST handling.

`afr config show` displays the stored values; unknown keys are rejected.

No changes to `runs` or any other table. afr has no `SCHEMA.md` (schema is inline
in `db.py`); the new table is documented here and via a comment in `init_db`.

## CLI surface

- `afr windows` — print the windows panel (used today, used this week, active
  window remaining, available before reset).
- `afr config set <key> <value>` — set a config key (validates known keys).
- `afr config show` — print current config.

Command named `windows` (precise; avoids collision with afr's existing "run"
terminology and the ambiguous word "session").

## Output (sketch)

```
5-hour windows
  Today:        2 used
  This week:    7 used (since Wed 12:00am America/New_York)
  Active:       1.8h left in current window
  Available:    ~3 more full windows before reset (Wed Jun 3, 12:00am ET) + 0.4h tail
```

## Error handling

- No weekly-reset config: still print used-today / used-this-week; print a hint
  to run `afr config set weekly-reset ...`; exit 0 (no crash).
- No `timezone` config: default to UTC with a note; suggest setting it.
- Unparseable `started_at`: skip that row, continue.
- No runs at all: used = 0; still show availability if config present.

## Testing (TDD — tests written first)

- Reconstruction over synthetic run lists: empty, single, back-to-back,
  gapped, exactly-on-5h-boundary.
- Available-math with injected `now` and `next_reset` for determinism.
- Day/week boundary correctness across timezone (run at 11:30pm local vs 12:30am).
- Weekly-reset instant computation (most-recent-occurrence and next-occurrence)
  including across a DST transition.
- Config get/set roundtrip and unknown-key rejection.

All window/availability functions take `now` (and parsed runs) as parameters so
tests are deterministic; the CLI injects real `now`.

## Conventions

- Spec: this file (`FEATURE_session_windows.md`), per the user's global rule.
- Implementation will follow TDD (tests before code).
- Relationship to burnrate: complementary. afr = real windows used + time-bounded
  availability; burnrate = budget-bounded forecast from `/usage`.
```
