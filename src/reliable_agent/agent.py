"""The agent loop.

Written as an explicit loop rather than an SDK tool runner so that every stop condition,
budget and approval is visible in one place and covered by tests.
See docs/decisions/0001-own-the-agent-loop.md.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic

from reliable_agent.pricing import cost_usd
from reliable_agent.tools import ToolOutcome, ToolRegistry
from reliable_agent.tracing import Tracer

Approval = Callable[[str, dict[str, Any]], bool]

_FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)


def always_approve(tool: str, tool_input: dict[str, Any]) -> bool:
    return True


def always_deny(tool: str, tool_input: dict[str, Any]) -> bool:
    return False


@dataclass(frozen=True)
class AgentConfig:
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_output_tokens: int = 16000
    # Guardrails
    max_turns: int = 8
    max_cost_usd: float = 0.50
    max_identical_calls: int = 2  # the same tool with the same input, per run
    refusal_fallback: bool = True


@dataclass
class ToolCallRecord:
    tool: str
    input: dict[str, Any]
    status: str
    attempts: int


@dataclass
class RunResult:
    outcome: str  # "completed" | "stopped"
    stop_reason: str
    final_text: str
    turns: int
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def called(self, tool: str) -> bool:
        return any(call.tool == tool for call in self.tool_calls)


class Agent:
    def __init__(
        self,
        client: Any,
        registry: ToolRegistry,
        system_prompt: str,
        config: AgentConfig | None = None,
        approval: Approval = always_deny,
        tracer: Tracer | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.client = client
        self.registry = registry
        self.system_prompt = system_prompt
        self.config = config or AgentConfig()
        self.approval = approval
        self.tracer = tracer or Tracer()
        self._clock = clock

    def _request(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": self.system_prompt,
            "tools": self.registry.to_api(),
            "messages": messages,
            "output_config": {"effort": self.config.effort},
        }
        if self.config.refusal_fallback and self.config.model in _FALLBACK_MODELS:
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"
        return request

    def run(self, task: str, user: str = "") -> RunResult:
        cfg = self.config
        self.tracer.emit("run_start", task=task, model=cfg.model, user=user)
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        result = RunResult(outcome="stopped", stop_reason="max_turns", final_text="", turns=0)
        signatures: Counter[str] = Counter()

        for turn in range(1, cfg.max_turns + 1):
            if result.cost_usd >= cfg.max_cost_usd:
                return self._stop(result, "budget_exceeded", f"spent ${result.cost_usd:.4f}")

            started = self._clock()
            try:
                response = self.client.beta.messages.create(**self._request(messages))
            except anthropic.APIError as exc:
                # The SDK has already retried rate limits, 5xx and connection errors.
                return self._stop(result, "api_error", f"{type(exc).__name__}: {exc}")

            usage = response.usage
            call_cost = cost_usd(response.model, usage.input_tokens, usage.output_tokens, cfg.model)
            result.turns = turn
            result.input_tokens += usage.input_tokens
            result.output_tokens += usage.output_tokens
            result.cost_usd += call_cost
            self.tracer.emit(
                "model_call",
                turn=turn,
                model=response.model,
                stop_reason=response.stop_reason,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=round(call_cost, 6),
                latency_ms=round((self._clock() - started) * 1000, 1),
            )

            if response.stop_reason == "refusal":
                result.final_text = "I can't help with that request."
                return self._stop(result, "refusal", "model declined the request")
            if response.stop_reason == "max_tokens":
                return self._stop(result, "max_tokens", "response hit max_tokens")

            # Append the full content (including thinking blocks) unchanged.
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                result.final_text = "".join(
                    block.text for block in response.content if block.type == "text"
                ).strip()
                result.outcome = "completed"
                result.stop_reason = "end_turn"
                return self._finish(result)

            tool_results = []
            for block in tool_uses:
                signature = f"{block.name}:{json.dumps(block.input, sort_keys=True)}"
                signatures[signature] += 1
                if signatures[signature] > cfg.max_identical_calls:
                    return self._stop(
                        result,
                        "loop_detected",
                        f"{block.name} called with the same input {signatures[signature]} times",
                    )
                outcome = self._execute(block.name, block.input)
                result.tool_calls.append(
                    ToolCallRecord(block.name, block.input, outcome.status, outcome.attempts)
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": outcome.content,
                        "is_error": outcome.is_error,
                    }
                )
            # All results for one turn go back in a single user message.
            messages.append({"role": "user", "content": tool_results})

        return self._stop(result, "max_turns", f"no final answer after {cfg.max_turns} turns")

    def _execute(self, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
        started = self._clock()
        tool = self.registry.get(name)
        if tool is not None and tool.writes and not self.approval(name, tool_input):
            outcome = ToolOutcome(
                "denied",
                "The user declined this action. Nothing was submitted. Do not retry; ask the "
                "user what they would like to do instead.",
            )
        else:
            outcome = self.registry.execute(name, tool_input)
        self.tracer.emit(
            "tool_call",
            tool=name,
            input=tool_input,
            status=outcome.status,
            attempts=outcome.attempts,
            latency_ms=round((self._clock() - started) * 1000, 1),
            result_preview=outcome.content[:200],
        )
        return outcome

    def _stop(self, result: RunResult, reason: str, detail: str) -> RunResult:
        result.outcome = "stopped"
        result.stop_reason = reason
        if not result.final_text:
            result.final_text = (
                "I stopped before finishing this request, so nothing further was done. "
                "Please try again or contact the helpdesk."
            )
        self.tracer.emit("guardrail", rule=reason, detail=detail)
        return self._finish(result)

    def _finish(self, result: RunResult) -> RunResult:
        self.tracer.emit(
            "run_end",
            outcome=result.outcome,
            stop_reason=result.stop_reason,
            turns=result.turns,
            tool_calls=len(result.tool_calls),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=round(result.cost_usd, 6),
        )
        return result
