from agent_flight_recorder.analyzers.outcome_suggester import suggest_outcome


def _sc(command, exit_code=0):
    return {"command": command, "exit_code": exit_code}


def _tc(tool_name, status="success"):
    return {"tool_name": tool_name, "status": status}


def _err(msg="boom"):
    return {"message": msg}


def test_suggest_shipped_on_git_commit():
    events = {"shell_commands": [_sc("git commit -m 'fix bug'")], "tool_calls": [], "errors": []}
    out, reason = suggest_outcome(events)
    assert out == "shipped"
    assert "git commit" in reason


def test_suggest_shipped_on_gh_pr_create():
    events = {"shell_commands": [_sc("gh pr create --title foo")], "tool_calls": [], "errors": []}
    out, _ = suggest_outcome(events)
    assert out == "shipped"


def test_suggest_shipped_on_git_push():
    events = {"shell_commands": [_sc("git push origin main")], "tool_calls": [], "errors": []}
    assert suggest_outcome(events)[0] == "shipped"


def test_ignores_failed_ship_command():
    events = {"shell_commands": [_sc("git commit -m foo", exit_code=1)], "tool_calls": [], "errors": []}
    # Failed commit should not trigger shipped suggestion.
    res = suggest_outcome(events)
    assert res is None or res[0] != "shipped"


def test_suggest_blocked_on_many_errors():
    events = {"shell_commands": [], "tool_calls": [], "errors": [_err()] * 4}
    out, reason = suggest_outcome(events)
    assert out == "blocked"
    assert "4 errors" in reason


def test_suggest_blocked_on_mostly_failed_shell():
    events = {"shell_commands": [_sc("foo", 1), _sc("bar", 1), _sc("baz", 0)], "tool_calls": [], "errors": []}
    out, _ = suggest_outcome(events)
    assert out == "blocked"


def test_suggest_exploratory_on_read_only():
    events = {
        "shell_commands": [],
        "tool_calls": [_tc("Read"), _tc("Grep"), _tc("Glob"), _tc("Read")],
        "errors": [],
    }
    out, reason = suggest_outcome(events)
    assert out == "exploratory"
    assert "no file writes" in reason


def test_no_suggestion_when_writes_present():
    events = {
        "shell_commands": [],
        "tool_calls": [_tc("Read"), _tc("Edit"), _tc("Read")],
        "errors": [],
    }
    assert suggest_outcome(events) is None


def test_no_suggestion_when_too_few_signals():
    events = {"shell_commands": [], "tool_calls": [_tc("Read")], "errors": []}
    assert suggest_outcome(events) is None


def test_no_suggestion_on_empty_session():
    assert suggest_outcome({"shell_commands": [], "tool_calls": [], "errors": []}) is None


def test_shipped_takes_priority_over_blocked():
    # A session that had errors but ended in a successful commit → shipped wins.
    events = {
        "shell_commands": [_sc("npm test", 1), _sc("npm test", 1), _sc("git commit -m fix", 0)],
        "tool_calls": [_tc("Edit")],
        "errors": [_err(), _err(), _err()],
    }
    out, _ = suggest_outcome(events)
    assert out == "shipped"
