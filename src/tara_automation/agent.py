"""DEPRECATED shim — the agent framework moved to tara_automation.agents.

Old (task 5 first draft):  tara_automation.agent (single hardcoded agent)
New:                       tara_automation.agents (definition-driven runner)

    from tara_automation.agents import run_agent
    run_agent("review_runs", {"mode": "compare", "runs": [3, 10]})
"""

from tara_automation.agents import list_agents, run_agent  # noqa: F401
