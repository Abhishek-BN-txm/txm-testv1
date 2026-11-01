"""Study orchestrator: turn a set of TARA inputs into a run, self-documenting project.

    # 1) create the project + context (file-only; safe to run anywhere)
    python scripts/study.py init --name Eagle_Eye \
        --cases "...\\pre.epc" "...\\pst1.epc" "...\\pst2.epc" \
        --sub "...\\AZ.sub" --con "...\\AZ.con" --mon "...\\AZ.mon" \
        --area WECC --rating 2 --objective "gen interconnection deliverability" \
        --note "pst1=project only; pst2=project+White Hills"

    # 1b) or hand it a change workbook and let it build the post cases
    python scripts/study.py init --name GenAdd \
        --cases "...\\30HS_pre.epc" --changes "...\\changes.xlsx" \
        --sub AZ.sub --con AZ.con --mon AZ.mon

    # 2) run the pipeline on the Windows machine (invokes tara.exe)
    python scripts/study.py run --name Eagle_Eye            # all stages
    python scripts/study.py run --name Eagle_Eye --stage contcompare summary

    # inspect the stored project context / progress
    python scripts/study.py status --name Eagle_Eye

The first --cases entry is the baseline; the rest are post cases. `run` writes
each stage's run numbers back into studies/<name>/study.json.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")   # ≥, Δ, → in output
except Exception:  # noqa: BLE001
    pass

from tara_automation.machine import REPO_ROOT  # noqa: E402
from tara_automation.study import GateError, Study  # noqa: E402
from tara_automation.study_pipeline import (GATED_STAGES, STAGES,  # noqa: E402
                                            run_all)

STUDIES = REPO_ROOT / "studies"


def cmd_init(args) -> int:
    study = Study.init(
        STUDIES, args.name,
        case_paths=[Path(p) for p in args.cases],
        sub=Path(args.sub) if args.sub else None,
        con=Path(args.con) if args.con else None,
        mon=Path(args.mon) if args.mon else None,
        objective=args.objective, area=args.area,
        rating_cont=args.rating, loading_cutoff=args.cutoff,
        notes=args.note or [], labels=args.labels,
        changes=Path(args.changes) if args.changes else None,
        atc=({"send": args.atc_send,
              "recv": (args.atc_recv[0] if len(args.atc_recv) == 1
                       else args.atc_recv)}
             if args.atc_send and args.atc_recv else None),
        footprint=Path(args.footprint) if args.footprint else None,
    )
    c = study.config
    print(f"Created study '{c.name}' at {study.root}")
    print(f"  cases: " + ", ".join(f"{cs.label}({cs.role},{cs.fmt}v{cs.version})"
                                   for cs in study.ordered()))
    print(f"  contingency inputs: sub={bool(c.sub)} con={bool(c.con)} "
          f"mon={bool(c.mon)}  rating={c.rating_cont} cutoff={c.loading_cutoff:g}%")
    print(f"  context: {study.config_path}")
    print(f"\nNext: python scripts/study.py run --name {c.name}")
    return 0


def cmd_create(args) -> int:
    """Create a study from a request file (the shared-link route of the
    Footprint Builder; the study desk server calls the same code)."""
    import json

    from tara_automation.study_request import StudyRequestError, create, validate
    path = Path(args.request)
    try:
        req = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"STOP: cannot read {path}: {e}")
        return 2
    try:
        if args.check:
            v = validate(req, STUDIES)
            print(f"Request OK: study '{v['name']}', {len(v['cases'])} case(s), "
                  f"base files: {', '.join(v['base']) or 'none'}, "
                  f"footprint: {'yes' if v['footprint'] else 'no'}")
            return 0
        res = create(req, STUDIES)
    except StudyRequestError as e:
        print("STOP: the request has problems:")
        for p in e.problems:
            print(f"  - {p}")
        return 1
    print(f"Created study '{res['name']}' at {res['path']}")
    for cs in res["cases"]:
        print(f"  case: {cs}")
    for f in res["files"]:
        print(f"  wrote {f}")
    print("Next:\n  " + "\n  ".join(res["next"]))
    return 0


def cmd_run(args) -> int:
    study = Study.load(STUDIES, args.name)
    stages = args.stage or STAGES
    bad = [s for s in stages if s not in STAGES]
    if bad:
        print(f"Unknown stage(s): {bad}. Valid: {STAGES}")
        return 2
    if args.jobs < 1:
        print("STOP: --jobs must be >= 1")
        return 2
    try:
        run_all(study, stages, force=args.force, jobs=args.jobs)
    except GateError as e:      # results not signed off (report stage)
        print(f"\nBLOCKED: {e}")
        return 3
    except RuntimeError as e:   # stage-order / failed-run guards
        print(f"STOP: {e}")
        return 1
    return 0


def cmd_approve(args) -> int:
    """Record the user's sign-off on one stage's results.

    This is the human gate: an automated step that could approve its own
    runs would make the gate decorative.
    """
    study = Study.load(STUDIES, args.name)
    if args.verdict and not Path(args.verdict).exists():
        print(f"STOP: verdict file not found: {args.verdict}")
        return 2
    try:
        if args.reject:
            g = study.reject(args.stage, note=args.note)
        else:
            g = study.approve(args.stage, verdict=args.verdict, note=args.note)
    except GateError as e:
        print(f"STOP: {e}")
        return 1
    study.save()
    print(f"{args.stage}: {g['status']} by {g['by']} at {g['at']}")
    print(f"  runs approved: [{g['fingerprint']}]")
    if g.get("verdict"):
        print(f"  verdict: {g['verdict']}")
    print(f"\nRe-running the stage cancels this sign-off (the run numbers "
          f"change).\nNext: python scripts/study.py run --name {args.name} "
          f"--stage report")
    return 0


def cmd_status(args) -> int:
    study = Study.load(STUDIES, args.name)
    c = study.config
    print(f"Study '{c.name}'  (created {c.created})")
    print(f"  objective: {c.objective or '—'}")
    print(f"  area: {c.area or '—'}  ·  rating slot {c.rating_cont}  ·  "
          f"cutoff {c.loading_cutoff:g}%")
    if c.notes:
        print("  notes: " + " | ".join(c.notes))
    print("  cases:")
    for cs in study.ordered():
        print(f"    {cs.label:8s} {cs.role:8s} {cs.fmt} v{cs.version}  "
              f"{Path(cs.path).name}")
    if c.atc:
        print(f"  atc: send={c.atc.get('send')} -> recv={c.atc.get('recv')}")
    print("  runs:")
    for stage, mp in c.runs.items():
        print(f"    {stage:12s} {mp}")
    print("  review gates:")
    if not any(c.runs.get(s) for s in GATED_STAGES):
        print("    (no gated stage has run yet)")
    for stage in GATED_STAGES:
        if not c.runs.get(stage):
            continue
        st = study.gate_status(stage)
        g = study.gate(stage)
        mark = {"approved": "OK", "pending": "AWAITING YOUR REVIEW",
                "stale": "LAPSED (runs changed since sign-off)",
                "rejected": "REJECTED"}.get(st, st.upper())
        line = f"    {stage:12s} {mark}"
        if st == "approved":
            line += f" — by {g.get('by')} {g.get('at')}"
            if g.get("verdict"):
                line += f", verdict {Path(g['verdict']).name}"
        print(line)
    blocked = [s for s in GATED_STAGES
               if c.runs.get(s) and study.gate_status(s) != "approved"]
    if blocked:
        print(f"    -> `report` is blocked until you approve: {', '.join(blocked)}")
    if c.deliverables:
        print("  deliverables:")
        for k, v in c.deliverables.items():
            print(f"    {k}: {v if isinstance(v, str) else len(v)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TARA study orchestrator")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="scaffold a study + write its context")
    pi.add_argument("--name", required=True)
    pi.add_argument("--cases", nargs="+", required=True,
                    help="case files; the first is the baseline")
    pi.add_argument("--labels", nargs="+", default=None,
                    help="labels per case (default: from file name)")
    pi.add_argument("--sub", default=None)
    pi.add_argument("--con", default=None)
    pi.add_argument("--mon", default=None)
    pi.add_argument("--objective", default="")
    pi.add_argument("--area", default="")
    pi.add_argument("--rating", type=int, default=2, choices=(1, 2),
                    help="post-contingency rating slot (1=Rate A, 2=Rate B)")
    pi.add_argument("--cutoff", type=float, default=70.0)
    pi.add_argument("--changes", default=None,
                    help="case-change workbook (.xlsx). When given, the "
                         "casechange stage builds an INCH per scenario, "
                         "applies it, and registers each modified case as a "
                         "post case in study.json.")
    pi.add_argument("--atc-send", nargs="+", default=None,
                    help="sending subsystem name(s) from the .sub for the ATC "
                         "(TrLim) stage, e.g. ED5_STATION")
    pi.add_argument("--atc-recv", nargs="+", default=None,
                    help="receiving (sink) subsystem name(s) for the ATC "
                         "stage; several = one TrLim per sink")
    pi.add_argument("--footprint", default=None,
                    help="footprint YAML (study area, sub-areas, sources, "
                         "sinks). The footprint stage compiles it into the "
                         "study's .sub (and con/mon when the YAML says auto); "
                         "see scripts/footprint.py template")
    pi.add_argument("--note", action="append", default=[],
                    help="session note (repeatable)")
    pi.set_defaults(func=cmd_init)

    pr = sub.add_parser("run", help="run the pipeline (invokes tara.exe)")
    pr.add_argument("--name", required=True)
    pr.add_argument("--stage", nargs="+", default=None,
                    help=f"subset of {STAGES} (default: all)")
    pr.add_argument("--force", action="store_true",
                    help="re-run engine stages even if already recorded")
    pr.add_argument("--jobs", type=int, default=1,
                    help="tara.exe instances to run in parallel within an "
                         "engine stage (default 1 = sequential; check your "
                         "TARA license allows concurrent instances)")
    pr.set_defaults(func=cmd_run)

    pc = sub.add_parser("create", help="create a study from a study request "
                                       "(saved by the Footprint Builder)")
    pc.add_argument("--request", required=True, help="study_request.json")
    pc.add_argument("--check", action="store_true",
                    help="validate only; create nothing")
    pc.set_defaults(func=cmd_create)

    ps = sub.add_parser("status", help="show stored project context")
    ps.add_argument("--name", required=True)
    ps.set_defaults(func=cmd_status)

    pa = sub.add_parser("approve", help="sign off a stage's results (a person, "
                                        "not a script)")
    pa.add_argument("--name", required=True)
    pa.add_argument("--stage", required=True, choices=GATED_STAGES)
    pa.add_argument("--verdict", default=None,
                    help="path to the review verdict this rests on")
    pa.add_argument("--note", default="")
    pa.add_argument("--reject", action="store_true",
                    help="record a rejection instead of an approval")
    pa.set_defaults(func=cmd_approve)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
