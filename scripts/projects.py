"""Project library: projects.xlsx -> selected projects -> change workbook / INCH.

    # 1) a blank, documented library to fill in
    python scripts/projects.py template --out studies/X/projects.xlsx

    # 2) check it (every problem at once; --run also checks POIs in the case)
    python scripts/projects.py validate --library studies/X/projects.xlsx --run 36

    # 3) select projects and build the changes for them
    python scripts/projects.py build --study X --select "{source: queue, cod_by: 2030}"
    python scripts/projects.py build --library L.xlsx --changes C.xlsx --run 36 \
        --base-case case.epc --select "{ids: [Q1234]}" --scenario-id Q23 --inch

`build` writes an ordinary change workbook (open it to audit exactly what will
be added) plus a selection report, and with --inch the INCH file. It never
runs TARA: apply the INCH with `case_change.py prepare/finalize`, or point the
study's `changes` at the generated workbook and run the casechange stage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from tara_automation import projects as pl  # noqa: E402
from tara_automation.casechange import (  # noqa: E402
    WorkbookError, read_workbook, write_inch,
)
from tara_automation.machine import REPO_ROOT  # noqa: E402

STUDIES = REPO_ROOT / "studies"
RUNS = REPO_ROOT / "runs"


def _print_problems(problems) -> None:
    print(f"\n{len(problems)} problem(s):\n")
    for p in problems:
        print(f"  {p}")
    print()


def _study(name):
    from tara_automation.study import Study
    return Study.load(STUDIES, name) if name else None


def _case_index(args, study) -> pl.CaseIndex | None:
    """The basecase run whose tables the POIs are checked against."""
    run = args.run
    if run is None and study is not None:
        base = study.baseline().label
        run = study.config.runs.get("basecase", {}).get(base)
    if run is None:
        if not args.no_case_check:
            raise SystemExit(
                "No case to check POIs against: pass --run N (a basecase run "
                "of the case being modified), or --no-case-check to skip — "
                "then POI buses and machine IDs are NOT verified.")
        print("WARNING: --no-case-check — POI buses and machine IDs are not "
              "verified against the case.")
        return None
    idx = pl.CaseIndex.from_run(RUNS / str(run) / "results")
    print(f"POIs checked against run {run} ({len(idx.buses):,} buses)")
    return idx


def cmd_template(args) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"Refusing to overwrite {out} (use --force).")
        return 1
    pl.write_template(out)
    print(f"Wrote {out}. One row per project; hover a header for its meaning.")
    return 0


def _library_path(args, study) -> Path:
    if args.library:
        return Path(args.library)
    if study is not None:
        return study.root / "projects.xlsx"
    raise SystemExit("Pass --library or --study.")


def cmd_validate(args) -> int:
    study = _study(args.study)
    try:
        lib = pl.read_library(_library_path(args, study))
    except WorkbookError as e:
        print(f"ERROR: {e}")
        return 2
    problems = list(lib.problems)
    if args.run is not None or args.changes:
        changes = read_workbook(Path(args.changes)) if args.changes else None
        idx = (pl.CaseIndex.from_run(RUNS / str(args.run) / "results")
               if args.run is not None else None)
        cs = pl.to_changeset([p for p in lib.projects if p.enabled],
                             changes=changes, case=idx)
        problems += cs.problems
    if problems:
        _print_problems(problems)
    print(f"{len(lib.projects)} project(s), "
          f"{sum(p.enabled for p in lib.projects)} enabled")
    print("OK — library is valid." if not problems else "Fix the problems above.")
    return 0 if not problems else 1


def cmd_build(args) -> int:
    study = _study(args.study)
    lib_path = _library_path(args, study)
    try:
        lib = pl.read_library(lib_path)
    except WorkbookError as e:
        print(f"ERROR: {e}")
        return 2
    if not lib.ok:
        _print_problems(lib.problems)
        return 1

    try:
        chosen = pl.select(lib, pl.parse_selection(args.select))
    except pl.SelectionError as e:
        print(f"ERROR: {e}")
        return 2
    if not chosen:
        print("Selection matched no enabled projects — nothing to build.")
        return 1

    changes_path = args.changes or (study.config.changes if study else None)
    changes = None
    if changes_path:
        changes = read_workbook(Path(changes_path))
        if not changes.ok:
            print(f"The change workbook {Path(changes_path).name} has problems:")
            _print_problems(changes.problems)
            return 1

    base_case = args.base_case or (study.baseline().path if study else "")
    cs = pl.to_changeset(
        chosen, changes=changes, case=_case_index(args, study),
        scenario_id=args.scenario_id, name=args.name or "",
        description=args.description or "", base_case=args.base_case or "",
        new_bus_start=args.new_bus_start, source=lib_path)
    if not cs.ok:
        _print_problems(cs.problems)
        return 1

    out_dir = (Path(args.out_dir) if args.out_dir
               else study.inputs_dir if study else Path.cwd())
    out_dir.mkdir(parents=True, exist_ok=True)
    wb_path = out_dir / f"_projects_{args.scenario_id}_changes.xlsx"
    md_path = out_dir / f"_projects_{args.scenario_id}_selection.md"
    targets = [wb_path, md_path]
    inch_path = out_dir / "inch" / f"{args.scenario_id}.inch"
    if args.inch:
        targets.append(inch_path)
    clash = [t for t in targets if t.exists()]
    if clash and not args.force:
        print("Refusing to overwrite (use --force): "
              + ", ".join(str(t) for t in clash))
        return 1

    pl.write_changes_workbook(cs, wb_path)
    sc = cs.scenarios[0]
    md_path.write_text(
        f"# Project selection {sc.id}\n\n"
        f"Library: `{lib_path}`  \nSelection: `{args.select or '(all enabled)'}`"
        f"  \nChange workbook refs: `{changes_path or '(none)'}`\n\n"
        + pl.summary(chosen) + "\n", encoding="utf-8")
    counts = {k: v for k, v in cs.counts(sc.id).items() if v}
    print(f"{len(chosen)} project(s) -> {sum(counts.values())} change row(s): "
          + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"  {wb_path}\n  {md_path}")

    if args.inch:
        if not base_case:
            print("--inch needs a base case (--base-case or --study).")
            return 1
        base = Path(base_case).resolve()
        case_dir = (Path(args.case_dir) if args.case_dir
                    else study.root / "02_Base_Cases" if study
                    else out_dir / "cases")
        out_case = (case_dir / sc.output_filename(base)).resolve()
        if out_case.exists() and not args.force:
            print(f"Refusing: the INCH would overwrite the case {out_case} "
                  f"when applied (use --case-dir or --force).")
            return 1
        write_inch(cs, sc, base, out_case, inch_path)
        print(f"  {inch_path}  (saves to {out_case})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("template", help="write a blank projects.xlsx")
    t.add_argument("--out", default="projects.xlsx")
    t.add_argument("--force", action="store_true")
    t.set_defaults(func=cmd_template)

    v = sub.add_parser("validate", help="check a filled library")
    v.add_argument("--library")
    v.add_argument("--study")
    v.add_argument("--changes", help="change workbook for changes_ref")
    v.add_argument("--run", type=int, help="basecase run to check POIs against")
    v.set_defaults(func=cmd_validate)

    b = sub.add_parser("build", help="selected projects -> change workbook "
                                     "(+ INCH)")
    b.add_argument("--library")
    b.add_argument("--study", help="study name: library, change workbook, "
                                   "basecase run and folders default from it")
    b.add_argument("--select", help="YAML mapping, e.g. "
                                    "\"{source: queue, status: [FS, GIA]}\"")
    b.add_argument("--changes", help="change workbook for changes_ref")
    b.add_argument("--run", type=int, help="basecase run of the case being "
                                           "modified (POI / ID checks)")
    b.add_argument("--no-case-check", action="store_true")
    b.add_argument("--scenario-id", default=pl.DEFAULT_SCENARIO)
    b.add_argument("--name")
    b.add_argument("--description")
    b.add_argument("--base-case")
    b.add_argument("--new-bus-start", type=int,
                   help="default: the referenced scenarios' NewBusStart")
    b.add_argument("--out-dir")
    b.add_argument("--case-dir", help="where the INCH's #SAVE writes the case")
    b.add_argument("--inch", action="store_true", help="also write the INCH")
    b.add_argument("--force", action="store_true")
    b.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
