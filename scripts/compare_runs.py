"""Compare two cases from their comm-engine runs.

    python scripts/compare_runs.py --run-a 3 --run-b 5 [--top 15]

A = baseline, B = change case. Writes runs/compare_<A>_vs_<B>.md (git-tracked)
and prints the digest. Both runs must be comm-engine runs (full report set).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tara_automation.compare import compare_cases
from tara_automation.machine import REPO_ROOT
from tara_automation.run_manager import RunManager


def main() -> int:
    ap = argparse.ArgumentParser(description="Case-vs-case comparison (comm runs)")
    ap.add_argument("--run-a", type=int, required=True, help="baseline run number")
    ap.add_argument("--run-b", type=int, required=True, help="change-case run number")
    ap.add_argument("--top", type=int, default=15, help="top-N movers per section")
    args = ap.parse_args()

    rm = RunManager(REPO_ROOT / "runs")
    ra, rb = rm.load_run(args.run_a), rm.load_run(args.run_b)
    for r in (ra, rb):
        if r.manifest.get("engine") != "comm":
            print(f"Run {r.number} is engine={r.manifest.get('engine')}; "
                  f"comparison needs comm runs (full report set).")
            return 2

    la = Path(str(ra.manifest.get("case", "A"))).name
    lb = Path(str(rb.manifest.get("case", "B"))).name
    cmp = compare_cases(ra.results_dir, rb.results_dir,
                        label_a=f"run {ra.number} ({la})",
                        label_b=f"run {rb.number} ({lb})",
                        top_n=args.top,
                        fmt_a=ra.manifest.get("options", {}).get("format"),
                        fmt_b=rb.manifest.get("options", {}).get("format"))

    md = cmp.to_markdown()
    print(md)
    out_dir = REPO_ROOT / "runs" / "output" / "digests"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"compare_{ra.number}_vs_{rb.number}.md"
    out.write_text(md, encoding="utf-8")
    print(f"Report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
