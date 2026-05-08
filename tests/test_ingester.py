import pytest
from pathlib import Path
from agent_flight_recorder.ingester import ingest_from_paths
from agent_flight_recorder.db import get_connection, init_db, list_runs
from tests.conftest import CLAUDE_FIXTURE, CODEX_FIXTURE


@pytest.fixture
def db(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return conn


def test_ingest_claude_fixture(db, tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import shutil
    shutil.copy(CLAUDE_FIXTURE, project_dir / "claude_sample.jsonl")
    new, skipped = ingest_from_paths([project_dir / "claude_sample.jsonl"], source="claude", conn=db)
    assert new == 1
    assert skipped == 0


def test_ingest_idempotent(db, tmp_path):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import shutil
    shutil.copy(CLAUDE_FIXTURE, project_dir / "claude_sample.jsonl")
    ingest_from_paths([project_dir / "claude_sample.jsonl"], source="claude", conn=db)
    new, skipped = ingest_from_paths([project_dir / "claude_sample.jsonl"], source="claude", conn=db)
    assert new == 0
    assert skipped == 1


def test_ingest_codex_fixture(db):
    new, skipped = ingest_from_paths([CODEX_FIXTURE], source="codex", conn=db)
    assert new == 1
    assert skipped == 0


def test_ingest_bad_file_counted_as_skipped(db, tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not valid json\n")
    new, skipped = ingest_from_paths([bad], source="claude", conn=db)
    assert new == 0
    assert skipped == 1
