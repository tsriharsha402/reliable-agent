import json

import anthropic
import httpx2

from reliable_agent.agent import Agent, AgentConfig, always_approve, always_deny
from reliable_agent.scripted import ScriptedClient, demo_turns, text, tool_use, turn
from reliable_agent.tools import ToolRegistry
from reliable_agent.tracing import Tracer, load_trace, render


def make_agent(helpdesk, turns, approval=always_approve, config=None, tracer=None):
    client = ScriptedClient(turns)
    agent = Agent(
        client,
        ToolRegistry(helpdesk.tools(), sleep=lambda _: None),
        "system",
        config or AgentConfig(),
        approval=approval,
        tracer=tracer or Tracer(),
    )
    return agent, client


def test_demo_run_completes_and_writes_state(helpdesk):
    agent, client = make_agent(helpdesk, demo_turns())
    result = agent.run("Book Nov 16-18 off")
    assert (result.outcome, result.stop_reason, result.turns) == ("completed", "end_turn", 3)
    assert [c.tool for c in result.tool_calls] == [
        "search_handbook",
        "get_pto_balance",
        "submit_pto_request",
    ]
    assert len(helpdesk.pto_requests) == 1
    assert result.cost_usd > 0


def test_parallel_results_return_in_one_message_and_history_is_append_only(helpdesk):
    agent, client = make_agent(helpdesk, demo_turns())
    agent.run("Book Nov 16-18 off")
    second = client.requests[1]["messages"]
    assert second[1]["role"] == "assistant"
    assert second[1]["content"] is not None
    results = second[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert all(r["type"] == "tool_result" and r["is_error"] is False for r in results)
    # The assistant turn is passed back exactly as received (thinking blocks included).
    first_response_content = client.requests[2]["messages"][1]["content"]
    assert first_response_content is second[1]["content"]


def test_request_shape(helpdesk):
    agent, client = make_agent(helpdesk, [turn(text("hi"))])
    agent.run("hello")
    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"] == {"effort": "medium"}
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert all(t["strict"] for t in request["tools"])
    assert "tool_choice" not in request


def test_no_fallback_for_haiku(helpdesk):
    agent, client = make_agent(
        helpdesk, [turn(text("hi"))], config=AgentConfig(model="claude-haiku-4-5")
    )
    agent.run("hello")
    assert "fallbacks" not in client.requests[0]


def test_declined_write_is_not_executed(helpdesk):
    turns = [
        turn(
            tool_use(
                "t1",
                "submit_pto_request",
                {
                    "employee_id": "E001",
                    "start_date": "2026-11-16",
                    "end_date": "2026-11-18",
                    "reason": "trip",
                },
            )
        ),
        turn(text("OK, nothing was submitted.")),
    ]
    agent, client = make_agent(helpdesk, turns, approval=always_deny)
    result = agent.run("book it")
    assert result.tool_calls[0].status == "denied"
    assert helpdesk.pto_requests == []
    denied = client.requests[1]["messages"][2]["content"][0]
    assert denied["is_error"] is True and "declined" in denied["content"]


def test_reads_never_ask_for_approval(helpdesk):
    asked = []

    def approval(tool, tool_input):
        asked.append(tool)
        return True

    turns = [turn(tool_use("t1", "get_oncall", {"team": "payments"})), turn(text("Jordan"))]
    agent, _ = make_agent(helpdesk, turns, approval=approval)
    agent.run("who is on call")
    assert asked == []


def test_tool_errors_reach_the_model_as_error_results(helpdesk):
    turns = [
        turn(
            tool_use(
                "t1",
                "submit_pto_request",
                {
                    "employee_id": "E002",
                    "start_date": "2026-11-20",
                    "end_date": "2026-11-20",
                    "reason": "x",
                },
            )
        ),
        turn(text("I can only book time off for you.")),
    ]
    agent, client = make_agent(helpdesk, turns)
    result = agent.run("book for Priya")
    assert result.tool_calls[0].status == "error"
    error = client.requests[1]["messages"][2]["content"][0]
    assert error["is_error"] is True and "Not authorized" in error["content"]
    assert result.outcome == "completed"


def test_loop_detection(helpdesk):
    same = {"query": "pto"}
    turns = [turn(tool_use(f"t{i}", "search_handbook", same)) for i in range(5)]
    agent, _ = make_agent(helpdesk, turns)
    result = agent.run("loop")
    assert (result.outcome, result.stop_reason) == ("stopped", "loop_detected")
    assert len(result.tool_calls) == 2


def test_turn_limit(helpdesk):
    turns = [turn(tool_use(f"t{i}", "search_handbook", {"query": f"q{i} pto"})) for i in range(5)]
    agent, _ = make_agent(helpdesk, turns, config=AgentConfig(max_turns=3))
    result = agent.run("wander")
    assert result.stop_reason == "max_turns"
    assert result.turns == 3


def test_cost_budget(helpdesk):
    expensive = [
        turn(tool_use(f"t{i}", "search_handbook", {"query": f"q{i} pto"}), tokens=(50_000, 5_000))
        for i in range(5)
    ]
    agent, _ = make_agent(helpdesk, expensive, config=AgentConfig(max_cost_usd=0.5))
    result = agent.run("spend")
    assert result.stop_reason == "budget_exceeded"
    assert result.turns == 2  # $0.30 per turn: the third call is never made


def test_refusal_and_max_tokens_stop_cleanly(helpdesk):
    agent, _ = make_agent(helpdesk, [turn(stop_reason="refusal")])
    assert agent.run("x").stop_reason == "refusal"
    agent, _ = make_agent(helpdesk, [turn(text("partial"), stop_reason="max_tokens")])
    assert agent.run("x").stop_reason == "max_tokens"


def test_api_errors_stop_the_run(helpdesk):
    class Down:
        def __init__(self):
            request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            error = anthropic.InternalServerError(
                "overloaded", response=httpx2.Response(529, request=request), body=None
            )

            def create(**_):
                raise error

            self.beta = type("B", (), {"messages": type("M", (), {"create": staticmethod(create)})})

    agent = Agent(Down(), ToolRegistry(helpdesk.tools()), "system")
    result = agent.run("x")
    assert (result.outcome, result.stop_reason) == ("stopped", "api_error")
    assert "nothing further was done" in result.final_text


def test_trace_is_written_and_rendered(helpdesk, tmp_path):
    path = tmp_path / "trace.jsonl"
    agent, _ = make_agent(helpdesk, demo_turns(), tracer=Tracer(path))
    agent.run("Book Nov 16-18 off", user="E001")
    events = load_trace(path)
    assert [e["event"] for e in events][:2] == ["run_start", "model_call"]
    assert events[-1]["event"] == "run_end"
    assert json.loads(json.dumps(events))  # serializable
    text_view = render(events)
    assert "submit_pto_request" in text_view and "completed" in text_view
