# 0003: Scripted model for CI, live scenarios for model behavior

**Status:** Accepted

## Context

CI must be free, fast and deterministic. The model's behavior is none of those.

## Decision

Two layers of testing:

- **Harness tests (CI, every commit):** a `ScriptedClient` replays predetermined model
  turns to test tools, approvals, guardrails, retries and tracing exactly.
- **Scenario evaluation (on demand, with an API key):** 25 realistic tasks (14 standard, 11 hard) against Claude,
  graded on tools used, final system state and the reply, with every failure classified.

## Consequences

- CI proves the guardrails work; only the live evaluation proves the agent is good.
- The scenario evaluation must be run before any model, prompt or tool change ships.
