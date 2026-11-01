"""Case modification: change workbook -> TARA INCH files -> modified cases.

    # 1) get a blank, documented workbook to fill in
    python scripts/case_change.py template --out studies/Eagle_Eye/03_TARA_Inputs/changes.xlsx

    # 2) check it before TARA ever sees it (reports every problem at once)
    python scripts/case_change.py validate --workbook <path>

    # 3) render one INCH file per enabled scenario
    python scripts/case_change.py build --workbook <path> --study Eagle_Eye
    python scripts/case_change.py build --workbook <path> --base-case <case.epc> --out-dir <dir>

`build` is file-only: it writes the INCH files and the target case names, and
does not invoke tara.exe. Applying them is the next step (see docs/tasks/
11_case_modification.md) — the INCH file can be loaded by hand in TARAViewer
(CaseUtils -> CaseCreator -> Apply all INCH files below) in the meantime.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from tara_automation.casechange import (  # noqa: E402
    WorkbookError, read_workbook, write_inch, write_template,
)
from tara_automation.casechange.workbook import sheet_summary  # noqa: E402
from tara_automation.machine import REPO_ROOT  # noqa: E402

STUDIES = REPO_ROOT / "studies"


def _report(cs) -> bool:
    """Print problems. Returns True when the workbook is usable."""
    if cs.ok:
        return True
    print(f"\n{len(cs.problems)} problem(s) found:\n")
    by_sheet: dict[str, list] = {}
    for p in cs.problems:
        by_sheet.setdefault(p.sheet, []).append(p)
    for sheet, items in by_sheet.items():
        print(f"  {sheet}")
        for p in items:
            where = f"row {p.row}" if p.row else "sheet"
            col = f" [{p.column}]" if p.column else ""
            print(f"    {where}{col}: {p.message}")
        print()
    return False


def cmd_template(args) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"Refusing to overwrite {out} (use --force).")
        return 1
    write_template(out)
    print(f"Wrote {out}\n\nSheets:\n{sheet_summary()}\n")
    print("Start on the Scenarios sheet. Hover any column header for its "
          "meaning, default and allowed values.\nGrey italic rows are "
          "examples and are ignored — delete them or edit their Comment.")
    return 0


def cmd_example(args) -> int:
    from tara_automation.casechange.example import build_example_workbook
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"Refusing to overwrite {out} (use --force).")
        return 1
    build_example_workbook(out)
    print(f"Wrote {out}\n\nFour scenarios, each isolating one INCH command "
          f"family so a failed load points at one family:\n"
          f"  S1  ADD_*     buses, GSU, POI tie, 300 MW plant, new load\n"
          f"  S2  MODIFY_*  S1 + unit redispatch + line re-rating\n"
          f"  S3  SCALE_*   S1 + area 14 load and generation scaling\n"
          f"  S4  TAP       tap DUGAS-MORGAN 500 and hang the plant off it\n\n"
          f"All existing elements referenced are real buses/branches in "
          f"30HS_22-G7_pre.epc (area 14, APS).")
    return 0


def cmd_validate(args) -> int:
    try:
        cs = read_workbook(Path(args.workbook))
    except WorkbookError as e:
        print(f"ERROR: {e}")
        return 2
    ok = _report(cs)
    scenarios = cs.enabled_scenarios()
    print(f"{len(cs.scenarios)} scenario(s), {len(scenarios)} enabled:")
    for sc in scenarios:
        counts = {k: v for k, v in cs.counts(sc.id).items() if v}
        total = sum(counts.values())
        print(f"  {sc.id:<6} {sc.name}")
        if sc.description:
            print(f"         {sc.description}")
        print(f"         {total} change(s): "
              + (", ".join(f"{k} {v}" for k, v in counts.items())
                 or "none — this scenario would reproduce the base case"))
        if sc.bus_names:
            print(f"         new buses: " + ", ".join(
                f"{n}={num}" for n, num in sc.bus_names.items()))
    print("\nOK — workbook is valid." if ok else "\nFix the problems above.")
    return 0 if ok else 1


def _resolve_base_case(sc, args) -> Path:
    """Scenario BaseCase -> a real path (explicit path, study label, default)."""
    if sc.base_case:
        p = Path(sc.base_case)
        if p.exists():
            return p
        if args.study:
            from tara_automation.study import Study
            study = Study.load(STUDIES, args.study)
            for c in study.config.cases:
                if c.label == sc.base_case:
                    return Path(c.path)
            raise FileNotFoundError(
                f"scenario {sc.id}: BaseCase {sc.base_case!r} is neither an "
                f"existing path nor a case label in study {args.study}")
        raise FileNotFoundError(
            f"scenario {sc.id}: BaseCase {sc.base_case!r} does not exist")
    if args.base_case:
        return Path(args.base_case)
    if args.study:
        from tara_automation.study import Study
        return Path(Study.load(STUDIES, args.study).baseline().path)
    raise SystemExit(
        "No base case: set BaseCase on the Scenarios sheet, or pass "
        "--base-case / --study.")


def cmd_build(args) -> int:
    try:
        cs = read_workbook(Path(args.workbook))
    except WorkbookError as e:
        print(f"ERROR: {e}")
        return 2
    if not _report(cs) and not args.force:
        print("Refusing to build from a workbook with problems (--force to "
              "override).")
        return 1

    if args.out_dir:
        out_dir = Path(args.out_dir)
    elif args.study:
        out_dir = STUDIES / args.study / "03_TARA_Inputs" / "inch"
    else:
        out_dir = Path.cwd() / "inch"
    if args.case_dir:
        case_dir = Path(args.case_dir)
    elif args.study:
        # A modified case IS a base case for every downstream stage, so it
        # belongs with the study's cases rather than with its inputs.
        case_dir = STUDIES / args.study / "02_Base_Cases"
    else:
        case_dir = out_dir.parent / "cases"
    out_dir.mkdir(parents=True, exist_ok=True)
    case_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for sc in cs.enabled_scenarios():
        base = _resolve_base_case(sc, args).resolve()
        if not base.exists():
            print(f"  {sc.id}: base case not found: {base}")
            return 1
        out_case = (case_dir / sc.output_filename(base)).resolve()
        inch_path = out_dir / f"{sc.id}.inch"
        write_inch(cs, sc, base, out_case, inch_path)
        counts = {k: v for k, v in cs.counts(sc.id).items() if v}
        print(f"  {sc.id:<6} {inch_path.name:<14} "
              f"{sum(counts.values())} change(s) -> {out_case.name}")
        written.append(inch_path)

    if not written:
        print("No enabled scenarios — nothing written.")
        return 1
    print(f"\nWrote {len(written)} INCH file(s) to {out_dir}")
    print("Modified cases will be written to " + str(case_dir))
    return 0


def cmd_prepare(args) -> int:
    """Create a run folder with the comm + bat that applies an INCH file."""
    from tara_automation.casechange.inch import read_save_target
    from tara_automation.commfile.casechange import CaseChangeEngine
    from tara_automation.machine import load_machine
    from tara_automation.run_manager import RunManager

    inch = Path(args.inch).resolve()
    target = read_save_target(inch)
    if target is None:
        print(f"ERROR: {inch.name} has no #SAVE record, so there is no output "
              f"case to produce. Add one, or pass --out-case.")
        return 2
    # The INCH's own #SAVE writes the case, not the comm — so the INCH is the
    # only place the output path can be set. Change it in the workbook
    # (Scenarios.OutputName) or in the INCH file itself.
    fmt, version, out_case = target

    case = Path(args.case)
    if not case.exists():
        print(f"ERROR: base case not found: {case}")
        return 2

    machine = load_machine()
    rm = RunManager(REPO_ROOT / "runs")
    run = rm.create_run(engine="comm", case=case,
                        label=args.label or f"case change {inch.stem}",
                        options={"analysis": "casechange", "inch": str(inch)})
    eng = CaseChangeEngine(tara_exe=machine.tara_exe)
    bat = eng.prepare(run, case, inch, out_case,
                      fmt=args.fmt or "EPC", version=args.version or 21)
    run.manifest["status"] = "prepared"
    run.save_manifest()

    print(f"Run {run.number} prepared.\n")
    print(f"  base case : {case}")
    print(f"  inch      : {inch}")
    print(f"  writes    : {out_case}  ({fmt} v{version})")
    print(f"\nRun it:\n  \"{bat}\"\n")
    print("Then finalize (parses Warn_Sum.csv and closes the run):\n"
          f"  python scripts/case_change.py finalize --run {run.number}")
    return 0


def cmd_finalize(args) -> int:
    from tara_automation.commfile.casechange import (
        CaseChangeEngine, describe_inch_errors, parse_warn_sum,
    )
    from tara_automation.run_manager import RunManager

    rm = RunManager(REPO_ROOT / "runs")
    run = rm.load_run(args.run)
    if "casechange" not in run.manifest:
        print(f"Run {args.run} is not a case-change run.")
        return 2
    if run.manifest.get("tara_exit_code") is None:
        run.manifest["tara_exit_code"] = args.exit_code
    checks = CaseChangeEngine.check_outputs(run)
    run.manifest["casechange_checks"] = checks
    warnings = parse_warn_sum(run.results_dir / "Warn_Sum.csv")

    print(f"Run {args.run}: {'OK' if checks['ok'] else 'FAILED'}")
    print(f"  case written  : {checks['case_written']} "
          f"({checks['case_bytes']:,} bytes)")
    print(f"  warnings read : {checks['warnings_parsed']}")
    print(describe_inch_errors(warnings))
    other = [w for w in warnings
             if not w["case_noise"] and w["id"] not in
             {e["id"] for e in checks["inch_errors"]}]
    if other:
        print("\n  Other warnings (not known case noise):")
        for w in other:
            print(f"    WarnID-{w['id']} x{w['count']} {w['severity']}: "
                  f"{w['message'][:80]}")
    run.finalize("success" if checks["ok"] else "failed",
                 CaseChangeEngine.summary_line(checks))
    return 0 if checks["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Build TARA INCH case modifications from a workbook.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", help="write a blank change workbook")
    t.add_argument("--out", default="changes.xlsx")
    t.add_argument("--force", action="store_true",
                   help="overwrite an existing file")
    t.set_defaults(func=cmd_template)

    e = sub.add_parser("example", help="write a worked Eagle Eye workbook")
    e.add_argument("--out", default="changes_example.xlsx")
    e.add_argument("--force", action="store_true")
    e.set_defaults(func=cmd_example)

    v = sub.add_parser("validate", help="check a filled workbook")
    v.add_argument("--workbook", required=True)
    v.set_defaults(func=cmd_validate)

    b = sub.add_parser("build", help="render INCH files, one per scenario")
    b.add_argument("--workbook", required=True)
    b.add_argument("--study", help="study name, to resolve base cases and "
                                   "choose the output folder")
    b.add_argument("--base-case", help="base case for scenarios that name none")
    b.add_argument("--out-dir", help="where to write the .inch files")
    b.add_argument("--case-dir", help="where TARA should write modified cases")
    b.add_argument("--force", action="store_true",
                   help="build even if the workbook has problems")
    b.set_defaults(func=cmd_build)

    pr = sub.add_parser("prepare",
                        help="create a run with the comm+bat that applies an "
                             "INCH file (you run the bat)")
    pr.add_argument("--inch", required=True)
    pr.add_argument("--case", required=True, help="base case to modify")
    pr.add_argument("--fmt", help="base case format (EPC/RAW), default EPC")
    pr.add_argument("--version", type=int, help="read keyword, default 21")
    pr.add_argument("--label", help="run label for RUN_LOG")
    pr.set_defaults(func=cmd_prepare)

    fi = sub.add_parser("finalize",
                        help="check a completed case-change run and close it")
    fi.add_argument("--run", type=int, required=True)
    fi.add_argument("--exit-code", type=int, default=0,
                    help="tara.exe exit code, if the bat was run by hand")
    fi.set_defaults(func=cmd_finalize)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
