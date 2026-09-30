"""Structured, append-only traces: one JSON event per model call and tool call.

A trace answers "what did the agent do, in what order, what did it cost, and why did it
stop?" without re-running anything.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.run_id = uuid.uuid4().hex[:12]
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._clock = clock
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: Any) -> None:
        record = {"run_id": self.run_id, "ts": round(self._clock(), 3), "event": event, **fields}
        self.events.append(record)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")


def load_trace(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def render(events: list[dict[str, Any]]) -> str:
    """Human-readable view of a trace."""
    lines = []
    for e in events:
        kind = e["event"]
        if kind == "run_start":
            lines.append(f"▶ task: {e['task']}  (model {e['model']}, user {e['user']})")
        elif kind == "model_call":
            lines.append(
                f"  turn {e['turn']}: model → {e['stop_reason']}  "
                f"[{e['input_tokens']} in / {e['output_tokens']} out, "
                f"${e['cost_usd']:.4f}, {e['latency_ms']:.0f} ms]"
            )
        elif kind == "tool_call":
            args = json.dumps(e["input"], ensure_ascii=False)
            if len(args) > 90:
                args = args[:87] + "..."
            retries = f", {e['attempts']} attempts" if e.get("attempts", 1) > 1 else ""
            lines.append(f"    ↳ {e['tool']}({args}) → {e['status']}{retries}")
        elif kind == "guardrail":
            lines.append(f"    ⛔ guardrail: {e['rule']}: {e['detail']}")
        elif kind == "run_end":
            lines.append(
                f"■ {e['outcome']} ({e['stop_reason']}): {e['turns']} turns, "
                f"{e['tool_calls']} tool calls, ${e['cost_usd']:.4f}"
            )
    return "\n".join(lines)
