"""Task 2: load a powerflow case, solve, extract key loadflow data.

comm files + tara.exe (builds bat, runs it, checks outputs):

    python scripts/task02_basecase.py --case "<path>.epc" --epc-ver 21

    # Prepare only (write comm files/bat but don't execute)
    python scripts/task02_basecase.py --dry-run ...

Each invocation creates runs/N/ with a manifest and appends to runs/RUN_LOG.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tara_automation.machine import REPO_ROOT, load_machine
from tara_automation.run_manager import RunManager

DEFAULT_CASE = (
    r"C:\Projects\TARA Automation"
    r"\RedButte_DNR_Study\02_Base_Cases\30HS2a.epc"
)


def maybe_review(run_number: int, enabled: bool) -> None:
    """Optional inline agent review after a successful run (--review)."""
    if not enabled:
        return
    try:
        from tara_automation.agents import run_agent

        print("\nRunning review_runs agent ...")
        result = run_agent("review_runs", {"mode": "run", "runs": [run_number]})
        print("\n" + result["verdict"])
        print(f"\nVerdict: {result['output_path']}  tokens={result['usage']}")
    except Exception as e:  # noqa: BLE001 — a review failure must not fail the run
        print(f"Review agent failed (run itself unaffected): {e}")


def detect_raw_version(case: Path, default: int = 34) -> int:
    """PSSE RAW header line: 'IC, SBASE, REV, ...' — REV is field 3."""
    try:
        with open(case, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("@!") or not line.strip():
                    continue
                rev = int(float(line.split(",")[2].split("/")[0]))
                if 24 <= rev <= 36:
                    return rev
                break
    except (OSError, ValueError, IndexError):
        pass
    return default


def main() -> int:
    ap = argparse.ArgumentParser(description="Load-solve-extract base case via TARA")
    ap.add_argument("--engine", choices=["comm"], default="comm",
                    help=argparse.SUPPRESS)  # comm only; kept for old commands
    ap.add_argument("--case", default=DEFAULT_CASE)
    ap.add_argument("--case-ver", type=int, default=None,
                    help="EPC (18-21) or PSSE RAW (24-36) version; "
                         "default: 21 for .epc, auto-detected from header for .raw")
    ap.add_argument("--epc-ver", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--label", default="task02 basecase load-solve-extract")
    ap.add_argument("--dry-run", action="store_true", help="comm engine: prepare only")
    ap.add_argument("--review", action="store_true",
                    help="after a successful run, have the review_runs agent "
                         "write a verdict (needs ANTHROPIC_API_KEY; costs a "
                         "few cents)")
    args = ap.parse_args()

    # Absolute: TARA runs inside runs/N, so a relative path would not resolve
    # there. TARA 26.02 then waits for input forever instead of failing (runs 80-83).
    case = Path(args.case).resolve()
    if not case.exists():
        print(f"Case not found: {case}")
        return 1
    if case.stat().st_size < 10_000:
        print(f"Case file is empty or implausibly small "
              f"({case.stat().st_size} bytes): {case}")
        return 1
    fmt = "EPC" if case.suffix.lower() == ".epc" else "RAW"
    version = args.case_ver or args.epc_ver
    if version is None:
        version = 21 if fmt == "EPC" else detect_raw_version(case)
        if fmt == "RAW":
            print(f"Detected PSSE RAW version {version} from header")

    machine = load_machine()
    rm = RunManager(REPO_ROOT / "runs")
    run = rm.create_run(engine=args.engine, case=case, label=args.label,
                        options={"format": fmt, "version": version})
    print(f"Run {run.number}: engine={args.engine} case={case.name} "
          f"({fmt} v{version})")

    if args.engine == "comm":
        from tara_automation.commfile import CommEngine

        eng = CommEngine(tara_exe=machine.tara_exe)
        bat = eng.prepare(run, case, fmt=fmt, version=version)
        print(f"Prepared: {bat}")
        if args.dry_run:
            run.finalize("prepared", "comm files built, not executed (dry run)")
            return 0
        rc = eng.execute(run)
        checks = eng.check_outputs(run)
        missing = [t for t, ok in checks.items() if not ok]
        for t, ok in checks.items():
            print(f"  [{'OK' if ok else '--'}] {t}.csv")
        if rc == 0 and not missing:
            run.finalize("success", f"all {len(checks)} result tables written")
            print(f"\nResults: {run.results_dir}")
            maybe_review(run.number, args.review)
            return 0
        run.finalize("failed", f"exit={rc}; missing: {', '.join(missing) or 'none'}")
        print(f"\nTARA exit code {rc}; check {run.logs_dir}")
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
