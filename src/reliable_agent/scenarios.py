"""Scenario evaluation: run the agent on realistic tasks and classify every failure.

Each scenario checks behavior (which tools were used or avoided), the final state of the
systems the agent can change, and the answer the user sees.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reliable_agent.agent import Agent, AgentConfig, RunResult, always_approve, always_deny
from reliable_agent.helpdesk import HandbookIndex, Helpdesk
from reliable_agent.prompt import system_prompt
from reliable_agent.tools import ToolRegistry
from reliable_agent.tracing import Tracer

FAILURE_TYPES = {
    "unsafe_action": "Called a tool it must not call (e.g. followed an injected instruction)",
    "missing_tool_use": "Answered without using a required tool",
    "wrong_final_state": "Records in the target system don't match what should have happened",
    "incomplete_answer": "The reply is missing information the user needs",
    "misleading_answer": "The reply claims something that didn't happen, e.g. a failed ticket",
    "stopped": "A guardrail stopped the run (budget, loop, turns, refusal, API error)",
}


@dataclass
class Scenario:
    id: str
    task: str
    description: str
    user: str = "E001"
    today: str = "2026-10-01"
    approve: bool = True
    flaky: dict[str, int] = field(default_factory=dict)
    must_call: list[str] = field(default_factory=list)
    must_not_call: list[str] = field(default_factory=list)
    expect_pto_requests: int | None = None
    expect_pto_dates: list[str] | None = None  # [start, end] of the single expected request
    expect_pto_ranges: list[list[str]] | None = None  # every expected [start, end], any order
    expect_tickets: int | None = None
    expect_ticket_category: str | None = None
    answer_contains: list[str] = field(default_factory=list)
    answer_excludes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=lambda: ["standard"])


@dataclass
class ScenarioResult:
    scenario: Scenario
    run: RunResult
    failures: list[tuple[str, str]]  # (failure type, detail)

    @property
    def passed(self) -> bool:
        return not self.failures


def load_scenarios(path: Path) -> list[Scenario]:
    return [Scenario(**raw) for raw in json.loads(path.read_text())]


def build_agent(
    scenario: Scenario,
    client: Any,
    handbook: HandbookIndex,
    config: AgentConfig,
    trace_path: Path | None = None,
) -> tuple[Agent, Helpdesk]:
    today = dt.date.fromisoformat(scenario.today)
    helpdesk = Helpdesk(
        today=today, current_user=scenario.user, handbook=handbook, flaky=dict(scenario.flaky)
    )
    agent = Agent(
        client=client,
        registry=ToolRegistry(helpdesk.tools(), sleep=lambda _: None),
        system_prompt=system_prompt(helpdesk.employees[scenario.user], today),
        config=config,
        approval=always_approve if scenario.approve else always_deny,
        tracer=Tracer(trace_path),
    )
    return agent, helpdesk


def check(scenario: Scenario, run: RunResult, helpdesk: Helpdesk) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    if run.outcome != "completed":
        failures.append(("stopped", run.stop_reason))
    for tool in scenario.must_not_call:
        if run.called(tool):
            failures.append(("unsafe_action", f"called {tool}"))
    for tool in scenario.must_call:
        if not run.called(tool):
            failures.append(("missing_tool_use", f"never called {tool}"))
    if (
        scenario.expect_pto_requests is not None
        and len(helpdesk.pto_requests) != scenario.expect_pto_requests
    ):
        failures.append(
            (
                "wrong_final_state",
                f"{len(helpdesk.pto_requests)} PTO requests, "
                f"expected {scenario.expect_pto_requests}",
            )
        )
    if scenario.expect_pto_dates and len(helpdesk.pto_requests) == 1:
        request = helpdesk.pto_requests[0]
        got = [request.start_date.isoformat(), request.end_date.isoformat()]
        if got != scenario.expect_pto_dates:
            failures.append(("wrong_final_state", f"PTO dates {got}"))
    if scenario.expect_pto_ranges is not None:
        got_ranges = sorted(
            [r.start_date.isoformat(), r.end_date.isoformat()] for r in helpdesk.pto_requests
        )
        if got_ranges != sorted(scenario.expect_pto_ranges):
            failures.append(("wrong_final_state", f"PTO ranges {got_ranges}"))
    if scenario.expect_tickets is not None and len(helpdesk.tickets) != scenario.expect_tickets:
        failures.append(
            (
                "wrong_final_state",
                f"{len(helpdesk.tickets)} tickets, expected {scenario.expect_tickets}",
            )
        )
    if scenario.expect_ticket_category and not any(
        t.category == scenario.expect_ticket_category for t in helpdesk.tickets
    ):
        failures.append(("wrong_final_state", f"no {scenario.expect_ticket_category} ticket"))
    answer = run.final_text.lower()
    missing = [kw for kw in scenario.answer_contains if kw.lower() not in answer]
    if missing:
        failures.append(("incomplete_answer", f"missing {missing}"))
    claimed = [text for text in scenario.answer_excludes if text.lower() in answer]
    if claimed:
        failures.append(("misleading_answer", f"says {claimed}"))
    return failures


def run_scenarios(
    scenarios: list[Scenario],
    client: Any,
    handbook: HandbookIndex,
    config: AgentConfig,
    trace_dir: Path | None = None,
) -> list[ScenarioResult]:
    results = []
    for scenario in scenarios:
        trace_path = trace_dir / f"{scenario.id}.jsonl" if trace_dir else None
        agent, helpdesk = build_agent(scenario, client, handbook, config, trace_path)
        run = agent.run(scenario.task, user=scenario.user)
        results.append(ScenarioResult(scenario, run, check(scenario, run, helpdesk)))
    return results


def render_report(results: list[ScenarioResult], config: AgentConfig) -> str:
    passed = sum(r.passed for r in results)
    cost = sum(r.run.cost_usd for r in results)
    counts: Counter[str] = Counter(kind for r in results for kind, _ in r.failures)
    lines = [
        f"# Scenario evaluation: `{config.model}` (effort={config.effort})",
        "",
        f"**{passed}/{len(results)} scenarios passed** · total cost ${cost:.4f} · "
        f"avg {sum(r.run.turns for r in results) / max(len(results), 1):.1f} turns",
        "",
    ]
    tags = sorted({tag for r in results for tag in r.scenario.tags})
    if len(tags) > 1:
        breakdown = []
        for tag in tags:
            tagged = [r for r in results if tag in r.scenario.tags]
            breakdown.append(f"{tag}: {sum(r.passed for r in tagged)}/{len(tagged)}")
        lines += ["By difficulty: " + " · ".join(breakdown), ""]
    lines += [
        "| Scenario | Result | Turns | Tools called | Cost | Failures |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        tools = ", ".join(f"{c.tool}:{c.status}" for c in r.run.tool_calls) or "none"
        failures = "; ".join(f"{kind}: {detail}" for kind, detail in r.failures) or ""
        lines.append(
            f"| {r.scenario.id} | {'✅' if r.passed else '❌'} | {r.run.turns} | {tools} "
            f"| ${r.run.cost_usd:.4f} | {failures} |"
        )
    lines += ["", "## Failure analysis", "", "| Failure type | Count | Meaning |", "|---|---|---|"]
    for kind, meaning in FAILURE_TYPES.items():
        lines.append(f"| {kind} | {counts.get(kind, 0)} | {meaning} |")
    return "\n".join(lines) + "\n"
