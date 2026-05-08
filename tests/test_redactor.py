from agent_flight_recorder.redactor import redact, redact_json


def test_redact_api_key():
    assert "[REDACTED]" in redact("api_key: sk-abc123def456ghi789jkl012")


def test_redact_bearer_token():
    assert "[REDACTED]" in redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.xyz")


def test_redact_github_token():
    assert "[REDACTED]" in redact("token=ghp_abcdefghijklmnopqrstuvwxyz123456")


def test_redact_leaves_safe_text():
    safe = "modal run finsight.py --env prod"
    assert redact(safe) == safe


def test_redact_json_returns_string():
    result = redact_json({"command": "echo hello", "token": "sk-secret123456789012"})
    assert isinstance(result, str)
    assert "[REDACTED]" in result
