"""ATC by TrLim: by-bus DC transfer capability from a sending subsystem to a sink.

Codifies the PowerGEM UI recipe validated by run 54 and used for runs 55-57
(see src/tara_automation/commfile/trlim.py for the method and its pitfalls).
One run per (case, sending subsystem); each goes through the run manager.

    # ED-5 substation -> CAISO on all three EAGL cases, 3 at a time
    python scripts/atc_trlim.py ^
        --case "...\\30HS_22-G7_pre.epc" --case "...\\30HS_22-G7_pst1.epc" ^
        --case "...\\30HS_22-G7_pst2.epc" ^
        --sub studies\\ATC_ED4_ED5\\03_TARA_Inputs\\ED5_ATC.sub ^
        --con "...\\AZ.con" --mon "...\\AZ.mon" ^
        --send ED5_STATION --recv CAISO --jobs 3

    # study parameters (defaults = the validated UI run):
    #   --rating-base 1 --rating-cont 2 --sens-cutoff 0.03 --max-test-mw 2000
    #   --max-viol-per-cons 5 --max-cons-per-path 200 --epc-version 21
    # --dry-run builds the comm files only.

From Python:

    from atc_trlim import run_atc
    results = run_atc(cases=[...], sub=..., con=..., mon=...,
                      sends=["ED5_STATION"], recv="CAISO")

Output per run: results/<SEND>_TrLim.csv (raw TARA, never modified) and a
`trlim_summary` block in manifest.json: per bus path, the first limiting
constraint plus any pre-existing overloads the transfer makes worse, which on
a strict reading make the incremental capability zero.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tara_automation.commfile.trlim import (DEFAULT_PARAMS, TrLimEngine,
                                            check_inputs, summarize_trlim,
                                            summary_line)
from tara_automation.machine import REPO_ROOT, load_machine
from tara_automation.run_manager import RunManager


def run_atc(
    cases: list[Path | str],
    sub: Path | str,
    con: Path | str,
    mon: Path | str,
    sends: list[str],
    recv: str,
    params: dict | None = None,
    label: str | None = None,
    jobs: int = 1,
    dry_run: bool = False,
) -> list[dict]:
    """Prepare, execute, check and finalize one run per (case, send).

    Returns one dict per run: {run, case, send, status, summary, problems,
    notes}. Runs are numbered and finalized in input order; only execution is
    parallel.
    """
    machine = load_machine()
    if not dry_run:
        bad = machine.validate(require_tara=True)
        if bad:
            raise RuntimeError("; ".join(bad))
    rm = RunManager(REPO_ROOT / "runs")
    eng = TrLimEngine(tara_exe=machine.tara_exe)
    full_params = {**DEFAULT_PARAMS, **(params or {})}

    # Phase 0 - validate every input before a single run number is used.
    for case in cases:
        check_inputs(case, sub, con, mon, sends, recv)

    # Phase 1 - prepare every run (sequential numbering).
    prepared = []
    for case in cases:
        case = Path(case)
        for send in sends:
            run_label = (label or "ATC TrLim (UI by-bus recipe)") + \
                f": {send} -> {recv}, {case.stem}"
            run = rm.create_run(engine="comm", case=case, label=run_label,
                                options={"analysis": "trlim", "TrLimMode": 1,
                                         "report": "busDCMaxTransfer",
                                         "send": send, "recv": recv,
                                         **full_params})
            try:
                eng.prepare(run, case, Path(sub), Path(con), Path(mon),
                            send=send, recv=recv, params=params)
            except Exception as exc:
                run.finalize("failed", f"prepare: {exc}")
                raise
            print(f"Run {run.number}: {send} -> {recv} on {case.name}")
            prepared.append((case, send, run))

    if dry_run:
        for _, _, run in prepared:
            run.finalize("prepared", "TrLim comm built, not executed (dry run)")
        return [{"run": r.number, "case": str(c), "send": s,
                 "status": "prepared"} for c, s, r in prepared]

    # Phase 2 - execute.
    if jobs > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            list(ex.map(lambda p: eng.execute(p[2]), prepared))
    else:
        for _, _, run in prepared:
            eng.execute(run)

    # Phase 3 - check and finalize in input order (RUN_LOG stays ordered).
    out = []
    for case, send, run in prepared:
        chk = eng.check_outputs(run)
        rec = {"run": run.number, "case": str(case), "send": send,
               "problems": chk["problems"], "notes": chk["notes"]}
        if not chk["ok"]:
            run.manifest["trlim_check"] = {"problems": chk["problems"],
                                           "notes": chk["notes"]}
            run.finalize("failed", "; ".join(chk["problems"]))
            rec.update(status="failed", summary=None)
        else:
            summary = summarize_trlim(chk["report"])
            run.manifest["trlim_summary"] = summary
            run.manifest["trlim_check"] = {"notes": chk["notes"]}
            line = summary_line(send, recv, summary)
            run.finalize("success", line)
            rec.update(status="success", summary=summary, line=line)
        out.append(rec)
    return out


def _print(results: list[dict]) -> None:
    for r in results:
        print(f"\nRun {r['run']} [{r['status']}] {r['send']} on "
              f"{Path(r['case']).name}")
        for p in r.get("problems") or []:
            print(f"  PROBLEM: {p}")
        for n in r.get("notes") or []:
            print(f"  note: {n}")
        s = r.get("summary")
        if not s:
            continue
        print(f"  contingencies: {s['n_contingencies']}")
        for path, v in s["paths"].items():
            print(f"  path {path.rstrip('_')} -> {v['receiving']} "
                  f"({v['rows']} rows)")
            if v["first_limit"]:
                f = v["first_limit"]
                print(f"    first limit   {v['first_limit_mw']:8.1f} MW  "
                      f"{f['short']}  Dfax {f['dfax']:+.4f}  "
                      f"headroom {f['headroom_mw']:.1f}  <- {f['contingency']}")
            else:
                print(f"    no limit found up to {v['max_test_mw']:.0f} MW")
            for w in v["worsened_initial_overloads"]:
                print(f"    PRE-EXISTING  {w['trlim_mw']:8.1f} MW  {w['short']}  "
                      f"Dfax {w['dfax']:+.4f}  headroom {w['headroom_mw']:.1f}"
                      f"  <- {w['contingency']}")
            if v["relieved_initial_overloads"]:
                print(f"    ({v['relieved_initial_overloads']} initial "
                      f"overload rows relieved by the transfer - informational)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case", action="append", required=True,
                    help="EPC case (repeatable: one run per case)")
    ap.add_argument("--sub", required=True, help=".sub defining send/recv/monitored subsystems")
    ap.add_argument("--con", required=True)
    ap.add_argument("--mon", required=True)
    ap.add_argument("--send", action="append", required=True,
                    help="sending subsystem name in --sub (repeatable)")
    ap.add_argument("--recv", required=True, help="receiving (sink) subsystem name")
    ap.add_argument("--rating-base", type=int, default=DEFAULT_PARAMS["rating_base"])
    ap.add_argument("--rating-cont", type=int, default=DEFAULT_PARAMS["rating_cont"])
    ap.add_argument("--sens-cutoff", type=float, default=DEFAULT_PARAMS["sens_cutoff"])
    ap.add_argument("--max-test-mw", type=float, default=DEFAULT_PARAMS["max_test_mw"])
    ap.add_argument("--max-viol-per-cons", type=int,
                    default=DEFAULT_PARAMS["max_viol_per_cons"])
    ap.add_argument("--max-cons-per-path", type=int,
                    default=DEFAULT_PARAMS["max_cons_per_path"])
    ap.add_argument("--epc-version", type=int, default=DEFAULT_PARAMS["epc_version"])
    ap.add_argument("--label", default=None)
    ap.add_argument("--jobs", type=int, default=1, help="runs to execute in parallel")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    params = {"rating_base": a.rating_base, "rating_cont": a.rating_cont,
              "sens_cutoff": a.sens_cutoff, "max_test_mw": a.max_test_mw,
              "max_viol_per_cons": a.max_viol_per_cons,
              "max_cons_per_path": a.max_cons_per_path,
              "epc_version": a.epc_version}
    results = run_atc(cases=a.case, sub=a.sub, con=a.con, mon=a.mon,
                      sends=a.send, recv=a.recv, params=params, label=a.label,
                      jobs=a.jobs, dry_run=a.dry_run)
    _print(results)
    return 0 if all(r["status"] in ("success", "prepared") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
