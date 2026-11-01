"""Headless review agent CLI — runs the review_runs agent from Python.

    python scripts/review_agent.py compare 3 10
    python scripts/review_agent.py validate 3 4
    python scripts/review_agent.py run 10
    python scripts/review_agent.py compare 3 10 --dry-run   # free preview

Needs ANTHROPIC_API_KEY in the environment (and `pip install anthropic`).
Verdicts land in runs/output/review_runs/; every invocation is
logged to runs/agents/AGENT_LOG.md with token usage.
"""

from __future__ import annotations

import argparse
import os
import sys

from tara_automation.agents import run_agent


def main() -> int:
    ap = argparse.ArgumentParser(description="Headless run-review agent")
    ap.add_argument("mode", choices=["run", "validate", "compare"])
    ap.add_argument("runs", nargs="+", type=int,
                    help="one run number (mode=run) or two (validate/compare)")
    ap.add_argument("--model", default=None,
                    help="override the model in the agent definition")
    ap.add_argument("--dry-run", action="store_true",
                    help="print evidence-pack stats and exit (no API call)")
    args = ap.parse_args()

    need = 1 if args.mode == "run" else 2
    if len(args.runs) != need:
        print(f"mode={args.mode} needs {need} run number(s)")
        return 2

    params = {"mode": args.mode, "runs": args.runs}

    if args.dry_run:
        stats = run_agent("review_runs", params, model=args.model, dry_run=True)
        for k, v in stats.items():
            print(f"{k:15s} {v}")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set. Get a key at console.anthropic.com, "
              "then:  setx ANTHROPIC_API_KEY <key>  (new shell after).")
        return 1

    print(f"Reviewing {params} ...")
    result = run_agent("review_runs", params, model=args.model)
    print("\n" + result["verdict"])
    print(f"\nVerdict saved: {result['output_path']}")
    print(f"Tokens: {result['usage']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
