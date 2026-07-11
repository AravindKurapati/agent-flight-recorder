from pydantic import BaseModel
from typing import Literal, Optional


class ToolCall(BaseModel):
    id: str
    run_id: str
    tool_name: str = "Unknown"
    input_summary: str = ""
    output_summary: str = ""
    status: Literal["success", "error"] = "success"
    duration_ms: Optional[int] = None
    timestamp: str = ""
    raw_json: str = ""


class ShellCommand(BaseModel):
    id: str
    run_id: str
    command: str = ""
    exit_code: Optional[int] = None
    duration_ms: Optional[int] = None
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    timestamp: str = ""


class FileEvent(BaseModel):
    id: str
    run_id: str
    path: str = ""
    action: Literal["read", "write", "patch", "delete"]


class Error(BaseModel):
    id: str
    run_id: str
    source: Literal["tool", "shell", "agent"]
    message: str = ""
    raw_json: str = ""
    timestamp: str = ""


class Run(BaseModel):
    id: str
    source: Literal["claude", "codex"]
    project_path: str = ""
    cwd: str = ""
    git_branch: str = ""
    started_at: str = ""
    ended_at: str = ""
    user_goal: str = ""
    final_summary: str = ""
    outcome: str = "untagged"
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0


class ParsedSession(BaseModel):
    run: Run
    tool_calls: list[ToolCall] = []
    shell_commands: list[ShellCommand] = []
    files: list[FileEvent] = []
    errors: list[Error] = []
