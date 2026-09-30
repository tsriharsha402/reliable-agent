# 0001: Own the agent loop

**Status:** Accepted

## Context

The Anthropic SDK offers a tool runner that drives the call-tool-call loop, with hooks for
approvals and result handling. It is the recommended default for most agents. This
project's purpose is reliability: every stop condition, budget and approval must be
explicit, reviewable and covered by deterministic tests.

## Decision

Write the loop directly (`agent.py`, about 230 lines including tracing). The loop enforces, in one place:
turn limit, cost budget, identical-call loop detection, approval before any write,
refusal and `max_tokens` handling, and a trace event for every model and tool call.

## Consequences

- Every guardrail is a few lines of plain code with its own test.
- We own details the tool runner would handle: appending the assistant turn unchanged
  (thinking blocks included), returning all parallel tool results in one message, and
  marking failed tools with `is_error`.
- If the loop logic grows beyond this, re-evaluate the tool runner with hooks.
