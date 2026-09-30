"""Tool definitions, input validation and execution with retries.

Tools are the agent's only way to affect the world, so every call passes the same
checks: schema validation, then the tool's own business rules. Transient failures are
retried here, so the model never has to reason about flaky infrastructure.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class ToolError(Exception):
    """A permanent failure the model should see and explain (bad input, policy violation)."""


class TransientToolError(Exception):
    """A temporary failure worth retrying (timeout, upstream unavailable)."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    writes: bool = False  # changes state in another system; requires human approval

    def to_api(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


@dataclass
class ToolOutcome:
    status: str  # "ok" | "error" | "denied" | "blocked"
    content: str
    attempts: int = 0

    @property
    def is_error(self) -> bool:
        return self.status != "ok"


_JSON_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate(schema: dict[str, Any], value: Any, path: str = "input") -> None:
    """Validate the JSON Schema subset our tools use. Raises ToolError with a clear message."""
    expected = schema.get("type")
    if expected:
        python_type = _JSON_TYPES[expected]
        if not isinstance(value, python_type) or (
            expected == "integer" and isinstance(value, bool)
        ):
            raise ToolError(f"{path} must be of type {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"{path} must be one of {schema['enum']}")
    if expected == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ToolError(f"{path} must be at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ToolError(f"{path} must be at most {schema['maxLength']} characters")
    if expected == "object":
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ToolError(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise ToolError(f"{path} has unexpected fields: {sorted(extra)}")
        for key, sub_schema in properties.items():
            if key in value:
                validate(sub_schema, value[key], f"{path}.{key}")


class ToolRegistry:
    def __init__(
        self,
        tools: list[Tool],
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def to_api(self) -> list[dict[str, Any]]:
        return [tool.to_api() for tool in self._tools.values()]

    def execute(self, name: str, tool_input: dict[str, Any]) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome("error", f"Unknown tool: {name}")
        try:
            validate(tool.input_schema, tool_input)
        except ToolError as exc:
            return ToolOutcome("error", f"Invalid input: {exc}")

        attempts = 0
        while True:
            attempts += 1
            try:
                result = tool.handler(**tool_input)
                return ToolOutcome("ok", json.dumps(result, default=str), attempts)
            except ToolError as exc:
                return ToolOutcome("error", str(exc), attempts)
            except TransientToolError as exc:
                if attempts > self.max_retries:
                    return ToolOutcome(
                        "error",
                        f"{name} is temporarily unavailable after {attempts} attempts: {exc}. "
                        "Tell the user to try again later.",
                        attempts,
                    )
                self._sleep(self.backoff_seconds * 2 ** (attempts - 1))
