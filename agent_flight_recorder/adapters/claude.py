import json
import uuid
from pathlib import Path
from typing import Iterator
from ..models import ParsedSession, Run, ToolCall, ShellCommand, FileEvent, Error
from ..redactor import redact, redact_json

CLAUDE_DIR = Path.home() / ".claude" / "projects"

_FILE_TOOL_MAP = {
    "Read": "read", "Write": "write", "Edit": "patch",
    "MultiEdit": "patch", "Glob": "read", "Grep": "read", "Delete": "delete",
}


def parse_session_file(path: Path) -> ParsedSession:
    path = Path(path)
    run_id = path.stem
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    project_path = path.parent.name
    user_goal = final_summary = started_at = ended_at = ""
    tokens_in = tokens_out = cache_read = cache_write = 0
    tool_calls: list[ToolCall] = []
    shell_commands: list[ShellCommand] = []
    files: list[FileEvent] = []
    errors: list[Error] = []
    pending: dict[str, dict] = {}   # tool_use_id -> tc_data
    pending_bash: dict[str, ShellCommand] = {}  # tool_use_id -> ShellCommand

    for line in lines:
        ts = line.get("timestamp", "")
        if not started_at and ts:
            started_at = ts
        if ts:
            ended_at = ts

        msg_type = line.get("type", "")
        message = line.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list):
            content = []

        if msg_type == "user" and not line.get("isMeta"):
            # plain string content = actual user prompt
            raw_content = message.get("content", "")
            if isinstance(raw_content, str) and not user_goal:
                text = raw_content.strip()
                _skip = ("<local-command", "<command-name", "<system-reminder",
                         "[Request interrupted", "Base directory for this skill:")
                if text and not any(text.startswith(s) for s in _skip):
                    user_goal = redact(text[:500])

            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and not user_goal:
                    text = block.get("text", "").strip()
                    # skip skill context dumps injected by Claude Code hooks
                    if text and not text.startswith("Base directory for this skill:") \
                            and not text.startswith("<system-reminder") \
                            and not text.startswith("[Request interrupted"):
                        user_goal = redact(text[:500])
                elif block.get("type") == "tool_result":
                    tu_id = block.get("tool_use_id", "")
                    is_error = block.get("is_error", False)
                    raw_output = block.get("content", "")
                    if isinstance(raw_output, list):
                        raw_output = " ".join(b.get("text", "") for b in raw_output if isinstance(b, dict))
                    output = redact(str(raw_output)[:500])

                    if tu_id in pending:
                        tc_data = pending.pop(tu_id)
                        tc_data["output_summary"] = output
                        tc_data["status"] = "error" if is_error else "success"
                        tool_calls.append(ToolCall(**tc_data))

                        if tu_id in pending_bash:
                            sc = pending_bash.pop(tu_id)
                            sc.exit_code = 1 if is_error else 0
                            if is_error:
                                sc.stderr_excerpt = output
                            else:
                                sc.stdout_excerpt = output
                            shell_commands.append(sc)

                        if is_error:
                            errors.append(Error(
                                id=str(uuid.uuid4()), run_id=run_id, source="tool",
                                message=output[:300], raw_json=redact_json(block), timestamp=ts,
                            ))

        elif msg_type == "assistant":
            usage = message.get("usage", {})
            tokens_in += usage.get("input_tokens", 0)
            tokens_out += usage.get("output_tokens", 0)
            cache_read += usage.get("cache_read_input_tokens", 0)
            cache_write += usage.get("cache_creation_input_tokens", 0)

            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    txt = block.get("text", "").strip()
                    if txt:
                        final_summary = redact(txt[:500])
                elif block.get("type") == "tool_use":
                    tu_id = block.get("id", str(uuid.uuid4()))
                    tool_name = block.get("name", "Unknown")
                    inp = block.get("input", {})
                    tc_data = {
                        "id": str(uuid.uuid4()), "run_id": run_id, "tool_name": tool_name,
                        "input_summary": redact(json.dumps(inp)[:300]),
                        "output_summary": "", "status": "success",
                        "duration_ms": None, "timestamp": ts, "raw_json": redact_json(block),
                    }
                    pending[tu_id] = tc_data

                    if tool_name == "Bash":
                        pending_bash[tu_id] = ShellCommand(
                            id=str(uuid.uuid4()), run_id=run_id,
                            command=redact(inp.get("command", "")[:500]), timestamp=ts,
                        )

                    if tool_name in _FILE_TOOL_MAP:
                        fpath = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
                        if fpath:
                            files.append(FileEvent(
                                id=str(uuid.uuid4()), run_id=run_id,
                                path=str(fpath), action=_FILE_TOOL_MAP[tool_name],
                            ))

    for tu_id, tc_data in pending.items():
        tool_calls.append(ToolCall(**tc_data))
        if tu_id in pending_bash:
            shell_commands.append(pending_bash[tu_id])

    # Sonnet pricing: $3/MTok in, $15/MTok out, $0.30/MTok cache_read, $3.75/MTok cache_write
    cost_usd = (
        tokens_in * 3.0 / 1_000_000
        + tokens_out * 15.0 / 1_000_000
        + cache_read * 0.30 / 1_000_000
        + cache_write * 3.75 / 1_000_000
    )

    return ParsedSession(
        run=Run(id=run_id, source="claude", project_path=project_path,
                started_at=started_at, ended_at=ended_at, user_goal=user_goal,
                final_summary=final_summary, tokens_in=tokens_in, tokens_out=tokens_out,
                cache_read=cache_read, cache_write=cache_write, cost_usd=cost_usd),
        tool_calls=tool_calls, shell_commands=shell_commands, files=files, errors=errors,
    )


def iter_sessions(claude_dir: Path = CLAUDE_DIR) -> Iterator[Path]:
    claude_dir = Path(claude_dir)
    if not claude_dir.exists():
        return
    for project_dir in claude_dir.iterdir():
        if project_dir.is_dir():
            yield from project_dir.glob("*.jsonl")
