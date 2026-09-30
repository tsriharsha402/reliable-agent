# Failure analysis

How to read a scenario evaluation and turn failures into fixes.

## Failure types

Every failed check is classified, so a report shows *where* the agent fails, not only
how often.

| Type | What it means | Usual fix |
|---|---|---|
| `unsafe_action` | Called a tool it must not call, e.g. followed an injected instruction | Treat as a severity-1 finding. Strengthen the prompt, and confirm a non-model layer (approval, authorization) would have stopped it |
| `missing_tool_use` | Answered without a tool it needed, e.g. a policy answer from memory | Tool descriptions and system prompt ("search the handbook first") |
| `wrong_final_state` | Records don't match what should have happened | Check the trace: wrong arguments (prompt, schema descriptions) or wrong rules (tool code) |
| `incomplete_answer` | The reply misses information the user needs | Prompt; check the keyword isn't too strict before changing anything |
| `stopped` | A guardrail ended the run: budget, loop, turn limit, refusal, API error | Read the trace. A loop usually means a tool error the model can't act on |

## Workflow

1. Run `make eval` and open `results/<run-id>/report.md`.
2. For each failure, open its trace: `python -m reliable_agent trace results/<run-id>/traces/<id>.jsonl`.
3. Decide which layer failed: model, prompt, tool description, tool rules, or the scenario
   itself (an overly strict check).
4. Fix one thing, re-run the affected scenarios with `--only`, then the whole suite.
5. Add a scenario for any new failure found in real use.

## Results

No live evaluation has been run yet. The first report will be committed under `results/`
and summarized here.
