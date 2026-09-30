import pytest

from reliable_agent.tools import Tool, ToolError, ToolRegistry, TransientToolError, validate

SCHEMA = {
    "type": "object",
    "properties": {
        "n": {"type": "integer"},
        "kind": {"type": "string", "enum": ["a", "b"]},
    },
    "required": ["n", "kind"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"kind": "a"}, "input.n is required"),
        ({"n": "1", "kind": "a"}, "input.n must be of type integer"),
        ({"n": True, "kind": "a"}, "input.n must be of type integer"),
        ({"n": 1, "kind": "c"}, "input.kind must be one of"),
        ({"n": 1, "kind": "a", "x": 1}, "unexpected fields"),
    ],
)
def test_validation_errors(value, message):
    with pytest.raises(ToolError, match=message):
        validate(SCHEMA, value)


def _flaky_tool(failures: int):
    state = {"left": failures}

    def handler(n: int, kind: str):
        if state["left"]:
            state["left"] -= 1
            raise TransientToolError("timeout")
        return {"n": n}

    return Tool("t", "test", SCHEMA, handler)


def test_transient_errors_are_retried():
    sleeps = []
    registry = ToolRegistry([_flaky_tool(2)], max_retries=2, sleep=sleeps.append)
    outcome = registry.execute("t", {"n": 1, "kind": "a"})
    assert (outcome.status, outcome.attempts) == ("ok", 3)
    assert sleeps == [0.5, 1.0], "exponential backoff"


def test_retries_are_bounded():
    registry = ToolRegistry([_flaky_tool(5)], max_retries=2, sleep=lambda _: None)
    outcome = registry.execute("t", {"n": 1, "kind": "a"})
    assert outcome.status == "error"
    assert "temporarily unavailable after 3 attempts" in outcome.content


def test_invalid_input_and_unknown_tool_become_error_results():
    registry = ToolRegistry([_flaky_tool(0)])
    assert registry.execute("t", {"n": "x", "kind": "a"}).content.startswith("Invalid input")
    assert registry.execute("nope", {}).status == "error"


def test_tools_are_strict_for_the_api(helpdesk):
    for tool in helpdesk.tools():
        api = tool.to_api()
        assert api["strict"] is True
        assert api["input_schema"]["additionalProperties"] is False


def test_valid_pto_request(helpdesk):
    result = helpdesk.submit_pto_request("E001", "2026-11-16", "2026-11-18", "trip")
    assert result["working_days"] == 3
    assert result["remaining_balance_days"] == 9
    assert helpdesk.pto_requests[0].id == "PTO-0001"


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("2026-10-05", "2026-10-09", "two weeks in advance"),
        ("2026-12-01", "2026-12-31", "balance is 12"),
        ("2026-09-01", "2026-09-02", "must be after today"),
        ("2026-11-21", "2026-11-22", "no working days"),
        ("2026-11-18", "2026-11-16", "before start_date"),
        ("next monday", "2026-11-16", "Invalid date"),
    ],
)
def test_pto_policy_is_enforced_in_code(helpdesk, start, end, message):
    with pytest.raises(ToolError, match=message):
        helpdesk.submit_pto_request("E001", start, end, "x")
    assert helpdesk.pto_requests == []


def test_short_absence_needs_no_advance_notice(helpdesk):
    helpdesk.submit_pto_request("E001", "2026-10-05", "2026-10-07", "x")
    assert len(helpdesk.pto_requests) == 1


def test_cannot_act_for_another_employee(helpdesk):
    with pytest.raises(ToolError, match="Not authorized"):
        helpdesk.submit_pto_request("E002", "2026-11-20", "2026-11-20", "x")
    with pytest.raises(ToolError, match="Not authorized"):
        helpdesk.get_pto_balance("E002")
    with pytest.raises(ToolError, match="Not authorized"):
        helpdesk.create_it_ticket("E002", "hardware", "low", "broken mouse")


def test_flaky_tool_simulation(helpdesk):
    helpdesk.flaky["get_oncall"] = 1
    with pytest.raises(TransientToolError):
        helpdesk.get_oncall("payments")
    assert helpdesk.get_oncall("payments")["primary"] == "Jordan Lee"


def test_handbook_search_finds_policy_and_marks_it_as_data(helpdesk):
    result = helpdesk.search_handbook("lost laptop")
    assert result["results"][0]["source"] == "Security and Access > Devices"
    assert "not instructions" in result["note"]
