"""Create the large-load deliverability study: TARA TrLim (DC, by-bus) from the
physical sink-path hubs to each committed large load's serving bus, N-1 on
the AZ footprint.

    python scripts/large_load_deliverability.py init [--case <epc>] [--name N] [--footprint F]

    # SRP 2033 HS discharge case (TS21 in service, TSMC on its load buses):
    python scripts/large_load_deliverability.py init --name APS_LargeLoad_Deliverability_C26 --footprint footprint_C26.yaml --case "...\\SRP_Cluster\\C26_33HS_Disch_Pre.epc"
    python scripts/study.py run --name APS_LargeLoad_Deliverability --stage basecase footprint atc atcsummary --jobs 3

Template: studies/_templates/APS_LargeLoad_Deliverability/
    footprint.yaml   HUBS (sources) and one LL_* sink per load, bus numbers
                     checked against 30HS_22-G7_pre.epc (run 71)
    base/AZ.*        the AZ study area, N-1 contingencies and monitoring

Sinks are read from footprint.yaml (every group under `sinks:`), so adding a
load = adding a group there. Each sink gets its own TrLim run; the sending
side is by-bus, so every hub bus gets a limit to every sink.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

from tara_automation.machine import REPO_ROOT
from tara_automation.study import Study

TEMPLATE = REPO_ROOT / "studies" / "_templates" / "APS_LargeLoad_Deliverability"
DEFAULT_CASE = (r"C:\Projects\tara-inputfls"
                r"\EAGL\basecase\30HS_22-G7_pre.epc")


def init(args) -> int:
    fy = TEMPLATE / args.footprint
    spec = yaml.safe_load(fy.read_text(encoding="utf-8"))
    sends = list(spec["sources"])
    sinks = [n for n in spec["sinks"] if n.startswith("LL_")]
    if args.recv:
        sinks = list(spec["sinks"])
        bad = [r for r in args.recv if r not in sinks]
        if bad:
            print(f"STOP: {bad} not under sinks: in {fy.name} ({sinks})")
            return 2
        sinks = list(args.recv)
    case = Path(args.case)
    if not case.exists() and args.case == DEFAULT_CASE:
        alt = REPO_ROOT / "studies" / "YUMA_Studyv5" / "03_TARA_Inputs" / "cases" / "30HS_22-G7_pre.epc"
        if alt.exists():
            case = alt
    if not case.exists():
        print(f"STOP: case not found: {case}  (pass --case)")
        return 2
    study = Study.init(
        REPO_ROOT / "studies", args.name, case_paths=[case], labels=["pre"],
        objective=("Deliverability from the physical sink-path hubs to "
                   + ", ".join(sinks)),
        area="AZ (areas 14-24)", rating_cont=2, loading_cutoff=70.0,
        atc={"send": sends, "recv": sinks}, footprint=fy,
        notes=["Sinks are single buses at each load's serving substation; "
               "see footprint.yaml comments for which are assumed or nearest-existing."])
    dest = study.root / "03_TARA_Inputs" / "base"
    shutil.copytree(TEMPLATE / "base", dest, dirs_exist_ok=True)
    print(f"Created {study.root}")
    print(f"  send: {sends}   sinks: {sinks}")
    print(f"Next:\n  python scripts/study.py run --name {args.name} "
          f"--stage basecase footprint atc atcsummary --jobs 3")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    pi = sp.add_parser("init")
    pi.add_argument("--case", default=DEFAULT_CASE)
    pi.add_argument("--name", default="APS_LargeLoad_Deliverability")
    pi.add_argument("--recv", nargs="+", default=None,
                    help="run only these sinks (default: every LL_* load sink)")
    pi.add_argument("--footprint", default="footprint.yaml",
                    help="footprint file in the template folder; bus numbers "
                         "are case-specific (footprint_C26.yaml = SRP C26 cases)")
    args = ap.parse_args(argv)
    return init(args)


if __name__ == "__main__":
    sys.exit(main())
