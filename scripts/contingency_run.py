"""AC N-1 contingency run: case + sub/con/mon -> accont -> violation summary.

    # RedButte SUT shortcuts (P1 or P2-P7 set on the 30HS2a EPC case)
    python scripts/contingency_run.py --set p1
    python scripts/contingency_run.py --set p2p7

    # explicit files
    python scripts/contingency_run.py --case X.epc --sub s.sub --con c.con --mon m.mon

    # options: --loading-cutoff 70, --opt block.key=value (repeatable),
    #          --dry-run (build comm only), --review (agent verdict after)

Creates runs/N with manifest; results: BranchViol.csv / VoltViol.csv.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tara_automation.commfile.contingency import ContingencyEngine
from tara_automation.machine import REPO_ROOT, load_machine
from tara_automation.run_manager import RunManager
from tara_automation.violations import summarize, summary_line

REDBUTTE = Path(r"C:\Projects\TARA Automation"
                r"\RedButte_DNR_Study")
SETS = {
    "p1": {
        "con": REDBUTTE / "03_TARA_Inputs" / "contingencies" / "RedButte_P1_all.con",
        "mon": REDBUTTE / "03_TARA_Inputs" / "monitored_files" / "PACE_Monitor_P1.MON",
    },
    "p2p7": {
        "con": REDBUTTE / "03_TARA_Inputs" / "contingencies" / "RedButte_P2P7_all.con",
        "mon": REDBUTTE / "03_TARA_Inputs" / "monitored_files" / "PACE_Monitor_P2-7.MON",
    },
}
DEFAULT_CASE = REDBUTTE / "02_Base_Cases" / "30HS2a.epc"
DEFAULT_SUB = (REDBUTTE / "03_TARA_Inputs" / "subsystems"
               / "PACE_Subsystem SK 8.3.2020.SUB")


def parse_overrides(pairs: list[str]) -> dict:
    """--opt lfopt_accont.lockOscShuntACCont=1 -> {block: {key: value}}"""
    out: dict = {}
    for p in pairs:
        try:
            key, val = p.split("=", 1)
            block, opt = key.split(".", 1)
        except ValueError:
            raise SystemExit(f"--opt must be block.key=value, got: {p}")
        try:
            v = float(val) if "." in val or "e" in val.lower() else int(val)
        except ValueError:
            v = val
        out.setdefault(block, {})[opt] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="AC N-1 contingency analysis")
    ap.add_argument("--set", choices=sorted(SETS), default=None,
                    help="RedButte SUT shortcut (con+mon pair)")
    ap.add_argument("--case", default=str(DEFAULT_CASE))
    ap.add_argument("--sub", default=str(DEFAULT_SUB))
    ap.add_argument("--con", default=None)
    ap.add_argument("--mon", default=None)
    ap.add_argument("--case-ver", type=int, default=None)
    ap.add_argument("--loading-cutoff", type=float, default=70.0)
    ap.add_argument("--opt", action="append", default=[],
                    metavar="BLOCK.KEY=VALUE", help="option override (repeatable)")
    ap.add_argument("--label", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--review", action="store_true",
                    help="agent verdict after a successful run (needs API key)")
    args = ap.parse_args()

    if args.set:
        args.con = args.con or str(SETS[args.set]["con"])
        args.mon = args.mon or str(SETS[args.set]["mon"])
    if not (args.con and args.mon):
        print("Provide --set or both --con and --mon")
        return 2

    case = Path(args.case)
    fmt = "EPC" if case.suffix.lower() == ".epc" else "RAW"
    version = args.case_ver or (21 if fmt == "EPC" else 34)
    label = args.label or f"N-1 accont {args.set or Path(args.con).stem}"

    machine = load_machine()
    rm = RunManager(REPO_ROOT / "runs")
    run = rm.create_run(engine="comm", case=case, label=label,
                        options={"format": fmt, "version": version,
                                 "analysis": "contingency"})
    print(f"Run {run.number}: contingency {label} on {case.name} "
          f"({fmt} v{version}, cutoff {args.loading_cutoff:g}%)")

    eng = ContingencyEngine(tara_exe=machine.tara_exe)
    bat = eng.prepare(run, case, Path(args.sub), Path(args.con), Path(args.mon),
                      fmt=fmt, version=version,
                      loading_cutoff=args.loading_cutoff,
                      options_overrides=parse_overrides(args.opt))
    print(f"Prepared: {bat}")
    if args.dry_run:
        run.finalize("prepared", "contingency comm built, not executed (dry run)")
        return 0

    rc = eng.execute(run)
    checks = eng.check_outputs(run)
    if rc != 0 or not all(checks.values()):
        missing = [k for k, ok in checks.items() if not ok]
        run.finalize("failed", f"exit={rc}; missing: {', '.join(missing) or 'none'}")
        print(f"TARA exit {rc}; check {run.logs_dir}")
        return 1

    s = summarize(run.results_dir / "BranchViol.csv",
                  run.results_dir / "VoltViol.csv")
    run.manifest["violations_summary"] = s
    if not s.get("n_contingencies"):
        # Exit 0 with 0 contingencies means the study files never took effect
        # (e.g. sub/con/mon failed to open — TARA WarnID-206). Not a success.
        run.finalize("failed", "0 contingencies analyzed — study files likely "
                     "not read; check logs for WarnID-206 (file not opened)")
        print(f"\nFAILED: 0 contingencies analyzed. The sub/con/mon files "
              f"likely did not load — check {run.logs_dir} for WarnID-206.")
        return 1
    run.finalize("success", summary_line(s))
    print(f"\n{summary_line(s)}")
    print("\nWorst overloads (>=100% AC):")
    for w in s["worst_overloads"]:
        flag = "  [WARN: no emerg rating]" if w.get("no_emergency_rating") else ""
        print(f"  {w['ac_loading']:6.1f}%  {w['facility']}  "
              f"[{w['area']}]  <- {w['contingency']}{flag}")
    if s.get("no_emergency_rating_facilities"):
        print(f"\nWARNING: {len(s['no_emergency_rating_facilities'])} monitored "
              f"facilities have no emergency rating (Rate Cont=9999); loadings "
              f"judged against normal rating.")
    print(f"\nResults: {run.results_dir}")

    if args.review:
        from task02_basecase import maybe_review
        maybe_review(run.number, True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
