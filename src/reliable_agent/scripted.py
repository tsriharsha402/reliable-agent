"""A scripted stand-in for the Claude client.

Replays predetermined model turns so the harness (tools, approvals, guardrails, tracing)
can be tested deterministically and demonstrated without an API key. It tests the
harness, not the model; model behavior is measured by the live scenario evaluation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def tool_use(block_id: str, name: str, tool_input: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def turn(*blocks: SimpleNamespace, stop_reason: str | None = None, tokens=(900, 150)):
    if stop_reason is None:
        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]),
    )


class ScriptedClient:
    def __init__(self, turns: list[SimpleNamespace]) -> None:
        self._turns = list(turns)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("ScriptedClient ran out of turns")
        return self._turns.pop(0)


def demo_turns() -> list[SimpleNamespace]:
    """A PTO request: parallel lookups, a write needing approval, then a summary."""
    return [
        turn(
            tool_use("t1", "search_handbook", {"query": "time off request notice"}),
            tool_use("t2", "get_pto_balance", {"employee_id": "E001"}),
        ),
        turn(
            tool_use(
                "t3",
                "submit_pto_request",
                {
                    "employee_id": "E001",
                    "start_date": "2026-11-16",
                    "end_date": "2026-11-18",
                    "reason": "Family trip",
                },
            )
        ),
        turn(
            text(
                "Done: PTO-0001 covers Monday 16 to Wednesday 18 November (3 working days) and "
                "is waiting for your manager's approval. You have 9 days left this year."
            )
        ),
    ]
