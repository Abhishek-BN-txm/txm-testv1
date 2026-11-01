"""Footprint compiler CLI: footprint.yaml -> .sub (+ con/mon) + bus tags.

    # a starter YAML to edit
    python scripts/footprint.py template --out studies/X/03_TARA_Inputs/footprint.yaml

    # compile against any basecase run's bus table (file-only, no engine run)
    python scripts/footprint.py compile --spec footprint.yaml --run 25 --out <dir>

    # compile a study's footprint (same as `study.py run --stage footprint`)
    python scripts/footprint.py compile --study X

Prints the per-group bus counts and any warnings; the full picture is in
<out>/footprint_report.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from tara_automation.footprint import (FootprintError,  # noqa: E402
                                       compile_footprint, load_network,
                                       write_outputs)
from tara_automation.machine import REPO_ROOT  # noqa: E402
from tara_automation.run_manager import RunManager  # noqa: E402

TEMPLATE = """\
# Footprint: where this study looks. Compiled against the case's bus table by
#   python scripts/footprint.py compile ...   (or the study's `footprint` stage)
#
# Selector keys in any group:
#   areas / zones / owners / buses : list of numbers or "a-b" ranges  } unioned
#   near: {bus: N, hops: K, via_kv_min: 115}                          } together
#   groups: [another_group]                                           }
#   kv_min / kv_max / name_regex   : then filter that set
#   exclude: {...any selector...}  : then remove buses
# Group names: letters, digits, underscore (written bare into the .sub).

study_area:              # contingencies + monitoring; keep it whole areas/zones
  name: AZ
  areas: [14, 15, 16, 17, 19, 21, 24]

sub_areas:               # reporting buckets; may overlap
  WAPA: {areas: [19]}
  ED_CORRIDOR: {near: {bus: 19169, hops: 4, via_kv_min: 115}, kv_min: 115}

sources:                 # injection groups for transfer (ATC) studies
  ED5_STATION: {buses: [19169, 19067]}

sinks:
  CAISO: {areas: [24, 30]}

# exclude: {name_regex: "^DUMMY"}     # removed from every group

monitor: {kv_min: 50, kv_max: 800}   # kV range written into the .mon
con: auto                            # auto = N-1 of every branch/tie in study_area
mon: auto                            # or a path to your own .con / .mon
"""


def cmd_template(args) -> int:
    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"STOP: {out} exists (use --force to overwrite)")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def cmd_compile(args) -> int:
    if args.study:
        from tara_automation.study import Study
        from tara_automation.study_pipeline import stage_footprint
        study = Study.load(REPO_ROOT / "studies", args.study)
        try:
            rec = stage_footprint(study, RunManager(REPO_ROOT / "runs"))
        except (FootprintError, RuntimeError) as e:
            print(f"STOP: {e}")
            return 1
        if rec:
            print(f"  report: {Path(rec['files']['report'])}")
        return 0

    if not (args.spec and args.run and args.out):
        print("STOP: give --study, or all of --spec, --run and --out")
        return 2
    try:
        run = RunManager(REPO_ROOT / "runs").load_run(args.run)
        net = load_network(run.results_dir)
        fp = compile_footprint(Path(args.spec), net, force_bus=args.force_bus)
        rec = write_outputs(fp, Path(args.out))
    except (FootprintError, FileNotFoundError) as e:
        print(f"STOP: {e}")
        return 1
    for name, g in rec["groups"].items():
        print(f"  {name:<16} {g['role']:<11} {g['buses']:>6} buses  ({g['form']})")
    for w in rec["warnings"]:
        print(f"  WARNING: {w}")
    print(f"  wrote {', '.join(Path(p).name for p in rec['files'].values())}")
    print(f"  report: {rec['files']['report']}")
    return 0


def cmd_locations(args) -> int:
    """Every case bus with its location and source, for the Footprint Builder."""
    from tara_automation.bus_coords import DEFAULT_WORKBOOK, write_locations
    from tara_automation.footprint import locate
    try:
        run = RunManager(REPO_ROOT / "runs").load_run(args.run)
        net = load_network(run.results_dir)
        coords = None if args.coords == "none" else Path(args.coords or DEFAULT_WORKBOOK)
        s = locate(net, coords, not args.no_case_coords, args.interconnect)
    except (FootprintError, FileNotFoundError) as e:
        print(f"STOP: {e}")
        return 1
    out = write_locations(net, Path(args.out))
    total = len(net.buses)
    for k, v in sorted(s["by_source"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:<15} {v:>7}  {v / total:.0%}")
    if s.get("reference"):
        r = s["reference"]
        print(f"  workbook: {r['matched']} matched, {r['rejected_reused_number']} rejected "
              f"(reused bus number), {r['duplicates_far']} with duplicate rows > 5 mi apart")
    print(f"  wrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pt = sub.add_parser("template", help="write a starter footprint.yaml")
    pt.add_argument("--out", required=True)
    pt.add_argument("--force", action="store_true")
    pt.set_defaults(func=cmd_template)

    pc = sub.add_parser("compile", help="compile a footprint")
    pc.add_argument("--study", help="study name (uses its footprint + baseline run)")
    pc.add_argument("--spec", help="footprint YAML")
    pc.add_argument("--run", type=int, help="basecase run whose bus table to use")
    pc.add_argument("--out", help="output folder")
    pc.add_argument("--force-bus", action="store_true",
                    help="write every group as explicit BUS lines, even whole "
                         "areas (used to prove BUS == AREA on the engine)")
    pc.set_defaults(func=cmd_compile)

    pl = sub.add_parser("locations", help="write bus_locations.csv (bus, lat, lon, "
                                          "source) for a case; load it in the "
                                          "Footprint Builder to draw polygons")
    pl.add_argument("--run", type=int, required=True, help="basecase run of the case")
    pl.add_argument("--out", required=True)
    pl.add_argument("--coords", help="coordinate workbook (.xlsx) or bus,lat,lon CSV; "
                                     "default: data/reference/Bus_Coordinates.xlsx; "
                                     "'none' = case coordinates only")
    pl.add_argument("--interconnect", default="WECC")
    pl.add_argument("--no-case-coords", action="store_true",
                    help="ignore the case's own lat/lon")
    pl.set_defaults(func=cmd_locations)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
