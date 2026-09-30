# 0002: Layered guardrails; the model is never the only safeguard

**Status:** Accepted

## Context

An agent that can change records will eventually be wrong: a misread date, a request made
for the wrong person, an instruction injected into a document it reads. Prompt
instructions reduce these errors but can't guarantee anything.

## Decision

Each risk is covered by at least one layer that doesn't depend on the model behaving:

| Layer | Enforced by | Stops |
|---|---|---|
| Strict tool schemas | API (`strict: true`) + our validator | Malformed or extra arguments |
| Business rules in tools | Code (`helpdesk.py`) | Policy violations: notice period, balance, past dates |
| Authorization | Code | Acting for anyone but the signed-in user |
| Human approval | Loop, before every write tool | Any write the user didn't intend, including injected ones |
| Budgets and loop detection | Loop | Runaway cost, stuck agents |
| Prompt instructions | Model | First line of defense; treats tool output as data |

## Consequences

- A successful prompt injection can at most *propose* an action, which the user sees and
  can decline.
- The `prompt-injection` scenario runs with approvals switched on automatically, to
  measure the model layer on its own. That is a stress test, not the production setting.
