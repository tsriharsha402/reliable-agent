"""Command line.

python -m reliable_agent demo                         # scripted run, no API key
python -m reliable_agent ask "Book Nov 16-18 off"     # live, asks before any write
python -m reliable_agent eval                         # live scenario evaluation
python -m reliable_agent trace results/<run>/<scenario>.jsonl
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from reliable_agent.agent import Agent, AgentConfig
from reliable_agent.helpdesk import HandbookIndex, Helpdesk
from reliable_agent.prompt import system_prompt
from reliable_agent.tools import ToolRegistry
from reliable_agent.tracing import Tracer, load_trace, render

ROOT = Path.cwd()


def console_approval(tool: str, tool_input: dict) -> bool:
    print(f"\nThe assistant wants to run {tool}:")
    print(json.dumps(tool_input, indent=2))
    try:
        return input("Approve? [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reliable_agent")
    parser.add_argument("--handbook", type=Path, default=ROOT / "data" / "handbook")
    parser.add_argument("--model", default=AgentConfig.model)
    parser.add_argument("--effort", default=AgentConfig.effort)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="Scripted run showing the harness; no API key needed")

    ask = sub.add_parser("ask", help="Run one task against Claude")
    ask.add_argument("task")
    ask.add_argument("--user", default="E001")
    ask.add_argument("--today", default=dt.date.today().isoformat())
    ask.add_argument("--trace", type=Path)

    ev = sub.add_parser("eval", help="Run the scenario evaluation against Claude")
    ev.add_argument("--scenarios", type=Path, default=ROOT / "scenarios" / "scenarios.json")
    ev.add_argument("--run-id", default=dt.date.today().isoformat())
    ev.add_argument("--only", nargs="*", help="Scenario IDs to run")

    tr = sub.add_parser("trace", help="Pretty-print a trace file")
    tr.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    config = AgentConfig(model=args.model, effort=args.effort)

    if args.command == "trace":
        print(render(load_trace(args.path)))
        return 0

    handbook = HandbookIndex(args.handbook)

    if args.command == "demo":
        from reliable_agent.scripted import ScriptedClient, demo_turns

        today = dt.date(2026, 10, 1)
        helpdesk = Helpdesk(today=today, current_user="E001", handbook=handbook)
        tracer = Tracer()
        agent = Agent(
            ScriptedClient(demo_turns()),
            ToolRegistry(helpdesk.tools()),
            system_prompt(helpdesk.employees["E001"], today),
            config,
            approval=lambda tool, tool_input: (
                print(f"    (approval requested for {tool}: yes)") or True
            ),
            tracer=tracer,
        )
        result = agent.run("Book November 16 to 18 off for a family trip.", user="E001")
        print(render(tracer.events))
        print(f"\nAssistant: {result.final_text}")
        return 0

    import anthropic

    client = anthropic.Anthropic(max_retries=3, timeout=120.0)

    if args.command == "ask":
        today = dt.date.fromisoformat(args.today)
        helpdesk = Helpdesk(today=today, current_user=args.user, handbook=handbook)
        tracer = Tracer(args.trace)
        agent = Agent(
            client,
            ToolRegistry(helpdesk.tools()),
            system_prompt(helpdesk.employees[args.user], today),
            config,
            approval=console_approval,
            tracer=tracer,
        )
        result = agent.run(args.task, user=args.user)
        print("\n" + render(tracer.events))
        print(f"\nAssistant: {result.final_text}")
        return 0 if result.outcome == "completed" else 1

    from reliable_agent.scenarios import load_scenarios, render_report, run_scenarios

    scenarios = load_scenarios(args.scenarios)
    if args.only:
        scenarios = [s for s in scenarios if s.id in args.only]
    run_dir = ROOT / "results" / args.run_id
    results = run_scenarios(scenarios, client, handbook, config, trace_dir=run_dir / "traces")
    report = render_report(results, config)
    (run_dir / "report.md").write_text(report)
    print(report)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
