# agent-flight-recorder

> **Every agent session you run disappears the moment it ends. This one doesn't.**

A local-first CLI that records every Claude Code and Codex session — prompts, tool calls, shell commands, file changes, errors, and token costs — into a searchable SQLite database. Find what you worked on, see what failed, and extract reusable workflow patterns.

---

## The problem this solves

AI coding sessions are opaque and ephemeral. When a session ends, all you have is changed files and a vague memory of what the agent tried. There's no way to answer:

- What tools did the agent call, and in what order?
- Which shell commands failed, and what was the error?
- How much did that session actually cost in tokens?
- Why does this same class of problem keep taking 3 sessions to fix?

`afr` records all of that and keeps it queryable — locally, permanently, without sending anything to a server.

---

## Install

```bash
git clone https://github.com/AravindKurapati/agent-flight-recorder
cd agent-flight-recorder
pip install -e .
```

**Requirements:** Python 3.11+ — no API keys, no accounts, everything stays on your machine.

---

## Usage

### Ingest your sessions

```bash
afr ingest claude     # reads ~/.claude/projects/**/*.jsonl
afr ingest codex      # reads ~/.codex/sessions/**/*.jsonl
```

Or wire a Claude Code hook to ingest automatically after every session (add to `~/.claude/settings.json`):

```json
{
  "hooks": {
    "Stop": [{ "matcher": "", "hooks": [{ "type": "command", "command": "afr ingest claude" }] }]
  }
}
```

### Browse recent runs

```bash
afr list --days 7
```

```
 ID       Source  Goal                                    Outcome    Tokens↑  Date
 50c3f2a1 claude  Fix the Modal deployment error          untagged   1,842    2026-05-08
 903b12cd claude  Add React component for dashboard       untagged     311    2026-05-08
 ad7e9f21 claude  Debug the authentication middleware     untagged     250    2026-05-07
```

### Inspect a session

```bash
afr show 50c3f2a1
```

```
┌─────────────────────────────────────────────────────────────────┐
│ Fix the Modal deployment error                                  │
│ claude | 2026-05-08 10:00 → 10:14 | untagged                   │
└─────────────────────────────────────────────────────────────────┘

Tool Calls
  ✓ Read      {"file_path": "finsight.py"}
  ✗ Bash      {"command": "modal run finsight.py"}
  ✓ Edit      {"file_path": "finsight.py"}
  ✓ Bash      {"command": "modal deploy finsight.py"}

Shell Commands
  [1] modal run finsight.py
  [0] modal deploy finsight.py

Errors
  • Error: missing secret huggingface-secret

Tokens: 1,842 in / 4,201 out | Cache read: 920 | Cost: $0.0000
```

### Search across all sessions

```bash
afr search "authentication"
afr search "modal" --days 30
```

### See patterns across sessions

```bash
afr stats --days 30
```

```
Total runs: 74

Outcomes
  shipped: 12
  blocked: 8
  untagged: 54

Tokens  126,450 in / 4,091,200 out
Errors  187 tool | 62 shell

Top Tools
  Bash: 891
  Read: 412
  Edit: 308
  Write: 201
  Glob: 94
  mcp__exa__web_search_exa: 87
```

### Tag a run with its outcome

```bash
afr tag 50c3f2a1 shipped
afr tag 903b12cd blocked
```

Valid outcomes: `shipped`, `blocked`, `abandoned`, `exploratory`

### Extract reusable workflow skills

After a few sessions solving the same class of problem, run:

```bash
afr extract-skills --min-runs 3
```

```
Candidate: deployment-modal-debug
  4 sessions | tools: Bash, Read | errors: 6
  Generate SKILL.md? [y/n]: y
  Written → generated_skills/deployment-modal-debug/SKILL.md
```

It clusters sessions by keyword similarity, finds ones that ended in `shipped`, and drafts a `SKILL.md` from the successful tool sequence. You approve before anything is written.

---

## All commands

| Command | What it does |
|---------|-------------|
| `afr ingest claude` | Parse `~/.claude/` sessions into the database |
| `afr ingest codex` | Parse `~/.codex/` sessions into the database |
| `afr list [--days N]` | Table of recent runs with outcome and token count |
| `afr show <id>` | Full detail: tool calls, shell commands, errors, cost |
| `afr search <query>` | Full-text search across run goals and summaries |
| `afr stats [--days N]` | Outcome distribution, top tools, error counts |
| `afr tag <id> <outcome>` | Label a run: shipped / blocked / abandoned / exploratory |
| `afr extract-skills` | Cluster sessions, propose SKILL.md candidates |

`<id>` accepts the first 8 characters from `afr list` output.

---

## What gets recorded

For each session:

- **Goal** — first user message
- **Tool calls** — every tool fired, input summary, success or error
- **Shell commands** — command, exit code, stdout/stderr excerpt
- **File events** — every read, write, patch, or delete
- **Errors** — failed tool calls and non-zero shell exits
- **Token counts** — input, output, cache read, cache write
- **Outcome** — you tag this: `shipped`, `blocked`, `abandoned`, `exploratory`

Secrets are redacted before anything is written to the database (API keys, bearer tokens, private keys, `.env` contents).

---

## How data is stored

Everything lives at `~/.afr/afr.db` — a single SQLite file on your machine. No data leaves your machine. No telemetry. No accounts.

You can query it directly with any SQLite client:

```bash
sqlite3 ~/.afr/afr.db "SELECT user_goal, outcome, tokens_in FROM runs ORDER BY started_at DESC LIMIT 10"
```

---

## Supported agents

| Agent | Source | Adapter |
|-------|--------|---------|
| Claude Code | `~/.claude/projects/**/*.jsonl` | Full — tool calls, tokens, errors |
| Codex (OpenAI) | `~/.codex/sessions/**/*.jsonl` | Full — tool name mapping, tokens, errors |

---

## License

MIT
