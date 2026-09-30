from pathlib import Path

from reliable_agent.__main__ import main
from reliable_agent.agent import AgentConfig, RunResult, ToolCallRecord
from reliable_agent.helpdesk import TEAMS
from reliable_agent.scenarios import (
    Scenario,
    ScenarioResult,
    build_agent,
    check,
    load_scenarios,
    render_report,
    run_scenarios,
)
from reliable_agent.scripted import ScriptedClient, demo_turns, text, turn

ROOT = Path(__file__).resolve().parent.parent


def test_scenario_file_is_valid(helpdesk):
    scenarios = load_scenarios(ROOT / "scenarios" / "scenarios.json")
    tool_names = {t.name for t in helpdesk.tools()}
    assert len({s.id for s in scenarios}) == len(scenarios) >= 12
    for s in scenarios:
        assert set(s.must_call + s.must_not_call + list(s.flaky)) <= tool_names, s.id
        assert s.user in helpdesk.employees
    assert TEAMS


def _run(**kwargs) -> RunResult:
    defaults = dict(outcome="completed", stop_reason="end_turn", final_text="", turns=1)
    return RunResult(**{**defaults, **kwargs})


def test_failure_classification(helpdesk):
    scenario = Scenario(
        id="x",
        task="t",
        description="d",
        must_call=["search_handbook"],
        must_not_call=["submit_pto_request"],
        expect_pto_requests=0,
        answer_contains=["November 12"],
    )
    injected = _run(
        tool_calls=[ToolCallRecord("submit_pto_request", {}, "ok", 1)], final_text="done"
    )
    helpdesk.submit_pto_request("E001", "2026-11-16", "2026-11-16", "x")
    kinds = {kind for kind, _ in check(scenario, injected, helpdesk)}
    assert kinds == {"unsafe_action", "missing_tool_use", "wrong_final_state", "incomplete_answer"}

    stopped = _run(
        outcome="stopped",
        stop_reason="loop_detected",
        final_text="November 12",
        tool_calls=[ToolCallRecord("search_handbook", {}, "ok", 1)],
    )
    helpdesk.pto_requests.clear()
    assert check(scenario, stopped, helpdesk) == [("stopped", "loop_detected")]


def test_scenario_runner_and_report(handbook, tmp_path):
    scenario = Scenario(
        id="demo",
        task="Book Nov 16-18 off",
        description="d",
        must_call=["submit_pto_request"],
        expect_pto_requests=1,
        expect_pto_dates=["2026-11-16", "2026-11-18"],
        answer_contains=["PTO-0001"],
    )
    client = ScriptedClient(demo_turns())
    results = run_scenarios([scenario], client, handbook, AgentConfig(), trace_dir=tmp_path)
    assert results[0].passed, results[0].failures
    assert (tmp_path / "demo.jsonl").exists()
    report = render_report(results, AgentConfig())
    assert "1/1 scenarios passed" in report and "unsafe_action" in report


def test_flaky_scenario_retries_inside_the_harness(handbook):
    scenario = Scenario(id="f", task="balance?", description="d", flaky={"get_pto_balance": 1})
    from reliable_agent.scripted import tool_use

    turns = [turn(tool_use("t1", "get_pto_balance", {"employee_id": "E001"})), turn(text("12"))]
    agent, _ = build_agent(scenario, ScriptedClient(turns), handbook, AgentConfig())
    result = agent.run(scenario.task)
    assert result.tool_calls[0].status == "ok"
    assert result.tool_calls[0].attempts == 2


def test_failed_result_property():
    result = ScenarioResult(Scenario(id="x", task="t", description="d"), _run(), [("stopped", "x")])
    assert not result.passed


def test_cli_demo(monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "PTO-0001" in out and "completed" in out
