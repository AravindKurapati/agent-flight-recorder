import pytest
from tests.conftest import CODEX_FIXTURE
from agent_flight_recorder.adapters.codex import parse_session_file


@pytest.fixture
def session():
    return parse_session_file(CODEX_FIXTURE)


def test_run_id_from_session_meta(session):
    assert session.run.id == "test-codex-001"


def test_source_is_codex(session):
    assert session.run.source == "codex"


def test_project_path_extracted(session):
    assert session.run.project_path == "/home/user/project"


def test_user_goal_extracted(session):
    assert "React" in session.run.user_goal


def test_token_counts(session):
    assert session.run.tokens_in == 500
    assert session.run.tokens_out == 100


def test_tool_name_mapping(session):
    names = [tc.tool_name for tc in session.tool_calls]
    assert "Read" in names
    assert "Bash" in names


def test_shell_command_extracted(session):
    assert len(session.shell_commands) == 1
    assert "npm test" in session.shell_commands[0].command


def test_file_event_for_read(session):
    paths = [f.path for f in session.files]
    assert "src/App.tsx" in paths


def test_successful_shell_exit_code(session):
    assert session.shell_commands[0].exit_code == 0


def test_no_errors_on_success(session):
    assert len(session.errors) == 0
