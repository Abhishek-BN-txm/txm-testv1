"""Generic agent runner: definition in, verdict out.

The loop (identical for every agent):
    1. load defs/<name>.json  (recipe path, gatherer, tools, model, budgets)
    2. gather evidence deterministically (GATHERERS[defn.evidence])
    3. reason: Anthropic API, recipe as system prompt, allowed tools only
    4. write-back: verdict under the agent's own output folder + log lines

Guardrails: tools are read-only; all writes happen here; each agent writes
only under runs/output/<name>/. Every invocation is logged with token
usage to runs/agents/AGENT_LOG.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tara_automation.machine import REPO_ROOT
from tara_automation.agents.tools import GATHERERS, TOOLS

RUNS_ROOT = REPO_ROOT / "runs"
DEFS_DIR = Path(__file__).parent / "defs"

HEADLESS_ADDENDUM = (
    "\n\n# Headless-mode addendum\n"
    "You are running as a headless agent. The evidence pack below was "
    "gathered per your recipe's gather step. Use your tools to chase every "
    "outlier to its cause before classifying. Then output ONLY the verdict "
    "markdown, starting with '# Review:'. Do not perform any write-back "
    "steps yourself — the harness owns all file writes."
)


def list_agents() -> list[str]:
    return sorted(p.stem for p in DEFS_DIR.glob("*.json"))


def load_def(name: str) -> dict:
    path = DEFS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No agent definition '{name}'. Available: {list_agents()}")
    return json.loads(path.read_text(encoding="utf-8"))


def _build_prompts(defn: dict, params: dict) -> tuple[str, str]:
    recipe = (REPO_ROOT / defn["recipe"]).read_text(encoding="utf-8")
    system = recipe + HEADLESS_ADDENDUM
    user = GATHERERS[defn["evidence"]](params)
    return system, user


def _derived_fields(params: dict) -> dict:
    d = dict(params)
    if "runs" in params:
        d["runs_joined"] = "_vs_".join(str(n) for n in params["runs"])
    return d


def run_agent(
    name: str,
    params: dict,
    model: str | None = None,
    dry_run: bool = False,
    verbose: bool = True,
) -> dict:
    """Run one agent. Returns {verdict, output_path, usage} (or dry-run stats)."""
    defn = load_def(name)
    model = model or defn["model"]
    system, user = _build_prompts(defn, params)

    if dry_run:
        return {
            "agent": name, "model": model, "params": params,
            "system_chars": len(system), "evidence_chars": len(user),
            "approx_tokens": (len(system) + len(user)) // 4,
        }

    import anthropic  # deferred: gather/tests need no API

    client = anthropic.Anthropic()
    tools = [TOOLS[t]["schema"] for t in defn.get("tools", [])]
    messages = [{"role": "user", "content": user}]
    usage = {"input_tokens": 0, "output_tokens": 0}
    verdict = "(agent exceeded tool-turn budget without producing a verdict)"

    for _turn in range(defn.get("max_tool_turns", 8) + 1):
        resp = client.messages.create(
            model=model, max_tokens=defn.get("max_tokens", 4000),
            system=system, tools=tools, messages=messages,
        )
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        if resp.stop_reason != "tool_use":
            verdict = "".join(b.text for b in resp.content if b.type == "text")
            break
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                out = TOOLS[block.name]["fn"](**block.input)
                if verbose:
                    print(f"  [tool] {block.name}({block.input}) "
                          f"-> {out.count(chr(10)) + 1} lines")
                results.append({"type": "tool_result",
                                "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})

    output_path = _write_back(defn, params, verdict, model, usage)
    return {"verdict": verdict, "output_path": output_path, "usage": usage}


def _write_back(defn: dict, params: dict, verdict: str, model: str,
                usage: dict) -> Path:
    out_dir = RUNS_ROOT / "output" / defn["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = defn["output_file"].format(**_derived_fields(params))
    out = out_dir / fname
    stamp = datetime.now().isoformat(timespec="seconds")
    header = f"<!-- agent={defn['name']} model={model} {stamp} -->\n"
    out.write_text(header + verdict + "\n", encoding="utf-8")

    _append_daily(RUNS_ROOT / "RUN_LOG.md",
                  f"- **Review (headless, {model}):** {defn['name']} "
                  f"{params} — verdict in output/{defn['name']}/{fname}")
    _append_daily(RUNS_ROOT / "agents" / "AGENT_LOG.md",
                  f"- {stamp} {defn['name']} {params} model={model} "
                  f"tokens_in={usage['input_tokens']} "
                  f"tokens_out={usage['output_tokens']}",
                  header_text="# Agent invocation log\n")
    return out


def _append_daily(log: Path, line: str, header_text: str = "# Run log\n") -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    text = log.read_text(encoding="utf-8") if log.exists() else header_text
    text += (f"{line}\n" if f"## {today}" in text
             else f"\n## {today}\n\n{line}\n")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(text, encoding="utf-8")
