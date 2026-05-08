import pytest
from pathlib import Path
from tests.conftest import CLAUDE_FIXTURE
from agent_flight_recorder.adapters.claude import parse_session_file


@pytest.fixture
def session():
    return parse_session_file(CLAUDE_FIXTURE)


def test_run_id_is_filename_stem(session):
    assert session.run.id == "claude_sample"


def test_source_is_claude(session):
    assert session.run.source == "claude"


def test_user_goal_extracted(session):
    assert "Modal" in session.run.user_goal


def test_final_summary_extracted(session):
    assert "secret" in session.run.final_summary.lower()


def test_token_counts_aggregated(session):
    assert session.run.tokens_in == 600
    assert session.run.tokens_out == 55
    assert session.run.cache_read == 300


def test_tool_calls_extracted(session):
    names = [tc.tool_name for tc in session.tool_calls]
    assert "Read" in names
    assert "Bash" in names


def test_bash_creates_shell_command(session):
    assert len(session.shell_commands) == 1
    assert "modal run" in session.shell_commands[0].command


def test_file_event_for_read(session):
    paths = [f.path for f in session.files]
    assert "finsight.py" in paths


def test_error_extracted_from_failed_tool_result(session):
    assert len(session.errors) == 1
    assert "secret" in session.errors[0].message.lower()


def test_failed_tool_call_status_is_error(session):
    bash_calls = [tc for tc in session.tool_calls if tc.tool_name == "Bash"]
    assert bash_calls[0].status == "error"
