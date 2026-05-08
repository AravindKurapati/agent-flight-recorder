# tests/conftest.py
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CLAUDE_FIXTURE = FIXTURES_DIR / "claude_sample.jsonl"
CODEX_FIXTURE = FIXTURES_DIR / "codex_sample.jsonl"
