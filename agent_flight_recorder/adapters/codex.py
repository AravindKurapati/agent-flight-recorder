import json
import uuid
from pathlib import Path
from typing import Iterator
from ..models import ParsedSession, Run, ToolCall, ShellCommand, FileEvent, Error
from ..redactor import redact, redact_json

CODEX_DIR = Path.home() / ".codex" / "sessions"

_TOOL_NAME_MAP = {
    "exec_command": "Bash", "apply_patch": "Edit", "read_file": "Read",
    "write_file": "Write", "delete_file": "Delete", "list_directory": "Glob",
}
_FILE_TOOL_ACTIONS = {
    "Read": "read", "Write": "write", "Edit": "patch", "Delete": "delete", "Glob": "read",
}


def parse_session_file(path: Path) -> ParsedSession:
    path = Path(path)
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    session_id = path.stem
    project_path = cwd = user_goal = final_summary = started_at = ended_at = ""
    tokens_in = tokens_out = 0
    tool_calls: list[ToolCall] = []
    shell_commands: list[ShellCommand] = []
    files: list[FileEvent] = []
    errors: list[Error] = []
    pending: dict[str, dict] = {}

    for line in lines:
        ts = line.get("timestamp", "")
        if not started_at and ts:
            started_at = ts
        if ts:
            ended_at = ts

        event_type = line.get("type", "")

        if event_type == "session_meta":
            session_id = line.get("session_id", session_id)
            project_path = line.get("project_path", "")
            cwd = line.get("cwd", cwd)

        elif event_type == "message":
            role = line.get("role", "")
            content = line.get("content", "")
            if role == "user" and not user_goal:
                user_goal = redact(str(content)[:500])
            elif role == "assistant":
                final_summary = redact(str(content)[:500])

        elif event_type == "response_item:function_call":
            call_id = line.get("call_id", str(uuid.uuid4()))
            raw_name = line.get("name", "")
            tool_name = _TOOL_NAME_MAP.get(raw_name, raw_name.capitalize() or "Unknown")
            args = line.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"raw": args}
            inp_summary = redact(json.dumps(args)[:300])
            pending[call_id] = {
                "id": str(uuid.uuid4()), "run_id": session_id, "tool_name": tool_name,
                "input_summary": inp_summary, "output_summary": "", "status": "success",
                "duration_ms": None, "timestamp": ts, "raw_json": redact_json(line),
                "_raw_name": raw_name, "_args": args,
            }
            if raw_name == "exec_command":
                pending[call_id]["_shell"] = ShellCommand(
                    id=str(uuid.uuid4()), run_id=session_id,
                    command=redact(args.get("command", "")[:500]), timestamp=ts,
                )

        elif event_type == "function_call_output":
            call_id = line.get("call_id", "")
            output = redact(str(line.get("output", ""))[:500])
            exit_code = line.get("exit_code")
            is_error = exit_code is not None and exit_code != 0

            if call_id in pending:
                data = pending.pop(call_id)
                data["output_summary"] = output
                data["status"] = "error" if is_error else "success"
                tool_name = data["tool_name"]
                args = data.pop("_args", {})
                sc = data.pop("_shell", None)
                data.pop("_raw_name", None)

                tool_calls.append(ToolCall(**data))

                if sc is not None:
                    sc.exit_code = exit_code
                    if is_error:
                        sc.stderr_excerpt = output
                    else:
                        sc.stdout_excerpt = output
                    shell_commands.append(sc)

                if is_error:
                    errors.append(Error(
                        id=str(uuid.uuid4()), run_id=session_id, source="tool",
                        message=output[:300], raw_json=redact_json(line), timestamp=ts,
                    ))

                if tool_name in _FILE_TOOL_ACTIONS:
                    fpath = args.get("path") or args.get("file_path") or ""
                    if fpath:
                        files.append(FileEvent(
                            id=str(uuid.uuid4()), run_id=session_id,
                            path=str(fpath), action=_FILE_TOOL_ACTIONS[tool_name],
                        ))

        elif event_type == "token_count":
            tokens_in += line.get("input_tokens", 0)
            tokens_out += line.get("output_tokens", 0)

    for data in pending.values():
        sc = data.pop("_shell", None)
        data.pop("_raw_name", None)
        data.pop("_args", None)
        tool_calls.append(ToolCall(**data))
        if sc is not None:
            shell_commands.append(sc)

    return ParsedSession(
        run=Run(id=session_id, source="codex", project_path=project_path, cwd=cwd,
                started_at=started_at, ended_at=ended_at, user_goal=user_goal,
                final_summary=final_summary, tokens_in=tokens_in, tokens_out=tokens_out),
        tool_calls=tool_calls, shell_commands=shell_commands, files=files, errors=errors,
    )


def iter_sessions(codex_dir: Path = CODEX_DIR) -> Iterator[Path]:
    codex_dir = Path(codex_dir)
    if not codex_dir.exists():
        return
    yield from codex_dir.rglob("*.jsonl")
