import pytest
from agent_flight_recorder.db import get_connection, init_db, upsert_session, set_outcome
from agent_flight_recorder.analyzers.skill_extractor import cluster_runs, generate_skill_md
from agent_flight_recorder.models import ParsedSession, Run, ToolCall, ShellCommand


@pytest.fixture
def db_with_clusters(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    for i in range(4):
        run = Run(id=f"run-{i}", source="claude", user_goal="Fix Modal deployment error")
        tc = ToolCall(id=f"tc-{i}", run_id=f"run-{i}", tool_name="Bash")
        sc = ShellCommand(id=f"sc-{i}", run_id=f"run-{i}", command="modal run finsight.py")
        upsert_session(conn, ParsedSession(run=run, tool_calls=[tc], shell_commands=[sc]))
    set_outcome(conn, "run-3", "shipped")
    return conn


def test_cluster_finds_similar_runs(db_with_clusters):
    clusters = cluster_runs(db_with_clusters, min_runs=3)
    assert len(clusters) >= 1


def test_cluster_has_success(db_with_clusters):
    clusters = cluster_runs(db_with_clusters, min_runs=3)
    assert any(c["has_success"] for c in clusters)


def test_cluster_run_count(db_with_clusters):
    clusters = cluster_runs(db_with_clusters, min_runs=3)
    assert clusters[0]["run_count"] >= 3


def test_generate_skill_md_contains_category(db_with_clusters):
    clusters = cluster_runs(db_with_clusters, min_runs=3)
    md = generate_skill_md(clusters[0]["category"], clusters[0], db_with_clusters)
    assert clusters[0]["category"] in md


def test_generate_skill_md_contains_shell_command(db_with_clusters):
    clusters = cluster_runs(db_with_clusters, min_runs=3)
    md = generate_skill_md(clusters[0]["category"], clusters[0], db_with_clusters)
    assert "modal run" in md
