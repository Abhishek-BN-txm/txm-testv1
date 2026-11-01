"""Agent framework: definition-driven agents on one generic runner.

An agent = recipe (.md policy) + definition (defs/<name>.json) + shared tools.
Adding an agent requires no new harness code.

Public API:
    from tara_automation.agents import run_agent, list_agents
    run_agent("review_runs", {"mode": "compare", "runs": [3, 10]})
"""

from tara_automation.agents.runner import list_agents, run_agent  # noqa: F401
