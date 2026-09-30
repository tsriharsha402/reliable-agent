.PHONY: install test lint format demo eval

install:
	pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check --fix .

# Scripted run showing tools, approval and the trace. No API key needed.
demo:
	python -m reliable_agent demo

# Live scenario evaluation against Claude. Needs ANTHROPIC_API_KEY.
# Estimated under $2; each scenario is capped at $0.50 by the agent's budget guardrail.
eval:
	python -m reliable_agent eval
