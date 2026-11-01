"""Compare AC N-1 contingency results across two or more runs.

    # baseline first, then the change case(s)
    python scripts/compare_contingency.py --runs 18 19 21
    python scripts/compare_contingency.py --runs 18 19 21 --labels pre pst1 pst2

Writes, to runs/output/digests/ (override with --out-dir):
  - contingency_compare_<runs>_<rating>.xlsx   (the standard deliverable)
  - contingency_compare_<runs>_<rating>.md     (short digest, also printed)

Rating basis (Rate 1 vs Rate 2) is auto-detected from each run's option
overrides. All runs must be contingency runs (BranchViol/VoltViol present).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Digest text uses non-ASCII glyphs (≥, Δ, →); the default Windows console is
# cp1252 and would raise UnicodeEncodeError on print. Prefer real UTF-8 output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 — cosmetic; never fail the run over stdout
    pass

from tara_automation.compare_contingency import compare  # noqa: E402
from tara_automation.machine import REPO_ROOT  # noqa: E402
from tara_automation.run_manager import RunManager  # noqa: E402
from tara_automation.xlsx_reports import contingency_workbook  # noqa: E402
from tara_automation.xlsx_reports import contingency_workbook


def default_label(run) -> str:
    """Short label from the case file stem, e.g. 30HS_22-G7_pst1 -> pst1."""
    stem = Path(str(run.manifest.get("case", f"run{run.number}"))).stem
    return stem.split("_")[-1] if "_" in stem else stem


def main() -> int:
    ap = argparse.ArgumentParser(description="N-1 contingency comparison (xlsx)")
    ap.add_argument("--runs", type=int, nargs="+", required=True,
                    help="run numbers; the first is the baseline")
    ap.add_argument("--labels", nargs="+", default=None,
                    help="short labels per run (default: from case name)")
    ap.add_argument("--out-dir", default=None,
                    help="output directory (default: runs/output/digests)")
    args = ap.parse_args()

    if len(args.runs) < 2:
        print("Need at least two runs (baseline + one change case).")
        return 2
    if args.labels and len(args.labels) != len(args.runs):
        print("--labels must have one label per run.")
        return 2

    rm = RunManager(REPO_ROOT / "runs")
    runs = []
    for n in args.runs:
        run = rm.load_run(n)
        analysis = run.manifest.get("options", {}).get("analysis")
        if analysis != "contingency":
            print(f"Run {n} is not a contingency run (analysis={analysis!r}); "
                  f"need BranchViol/VoltViol outputs.")
            return 2
        for name in ("BranchViol.csv", "VoltViol.csv"):
            if not (run.results_dir / name).exists():
                print(f"Run {n} is missing {name}.")
                return 2
        runs.append(run)

    labels = args.labels or [default_label(r) for r in runs]
    cmp = compare(runs, labels)

    rating_tag = cmp.cases[0].rating.split()[0] + cmp.cases[0].rating.split()[1]
    rating_tag = rating_tag.replace("(", "").replace(")", "")  # Rate1 / Rate2
    stem = "contingency_compare_" + "-".join(str(n) for n in args.runs) \
        + f"_{rating_tag}"
    out_dir = Path(args.out_dir) if args.out_dir else \
        REPO_ROOT / "runs" / "output" / "digests"
    out_dir.mkdir(parents=True, exist_ok=True)

    xlsx = contingency_workbook(cmp, out_dir / f"{stem}.xlsx")
    md = cmp.to_markdown()
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(md, encoding="utf-8")

    print(md)
    print(f"\nWorkbook: {xlsx}")
    print(f"Digest:   {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
