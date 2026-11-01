"""Study pipeline: drive a study end-to-end and record results in study.json.

Stages (each is re-runnable; already-completed engine stages are skipped unless
``force=True``):

  casechange      change workbook -> INCH per scenario -> modified case,
                  each registered in study.json as a post case (skipped when
                  the study has no change workbook)
  basecase        load/solve/extract each case (comm engine)
  basecompare     baseline vs each post case (loadflow deltas)
  contingency     AC N-1 per case (sub/con/mon, rating basis from config)
  contcompare     contingency Excel workbook + digest across the N-1 runs
  atc             transfer capability (TrLim, by-bus) per case x sending
                  subsystem (skipped when the study has no `atc` block)
  atcsummary      ATC limits table across cases (markdown + json)
  summary         deliverability summary markdown
  review          optional AI review verdict (needs ANTHROPIC_API_KEY)
  report          GATED: results pack for decks and external use — refuses to
                  run until the user has approved every analysis stage

Engine stages invoke tara.exe, so the driving script (`scripts/study.py run`)
is executed on the user's Windows machine.

Gates: an analysis stage that produces numbers calls `study.mark_for_review()`
when it succeeds, which sets that stage's gate to `pending` and cancels any
earlier approval. Only `scripts/study.py approve` (a person) sets `approved`,
and only `report` demands it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tara_automation.compare import compare_cases
from tara_automation.compare_contingency import compare as compare_cont
from tara_automation.machine import REPO_ROOT, load_machine
from tara_automation.run_manager import RunManager
from tara_automation.study import CaseSpec, GateError, Study
from tara_automation.violations import summarize, summary_line
from tara_automation.xlsx_reports import contingency_workbook

DIGESTS = REPO_ROOT / "runs" / "output" / "digests"


def _runs_exist(rm: RunManager, numbers) -> bool:
    return all((rm.runs_root / str(n) / "manifest.json").exists()
               for n in numbers)


def _execute_batch(eng, prepared: list, jobs: int) -> dict[int, int]:
    """Execute prepared runs, ``jobs`` at a time. Returns {run_number: rc}.

    Safe to parallelize because every run is fully prepared first (sequential
    run numbering), each tara.exe works only in its own runs/N/ tree, and all
    finalize()/RUN_LOG writes happen after this returns, in case order.
    """
    if jobs <= 1:
        return {run.number: eng.execute(run) for _, run in prepared}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {run.number: ex.submit(eng.execute, run)
                for _, run in prepared}
        return {n: f.result() for n, f in futs.items()}


# -------------------------------------------------------------- casechange ----
def stage_casechange(study: Study, rm: RunManager, machine,
                     force=False, jobs=1) -> dict:
    """Workbook -> one INCH per scenario -> one modified case per scenario.

    Each modified case is appended to study.json as a post case, so every
    later stage treats it as an ordinary base case and needs no knowledge of
    where it came from. No-ops when the study has no change workbook.

    A scenario whose INCH applied with errors is NOT registered as a case:
    TARA exits 0 and writes a full-size case even when records were dropped
    (runs 31-33), so letting a silently-wrong case into the pipeline would
    quietly corrupt every downstream comparison.
    """
    from tara_automation.casechange import read_workbook, write_inch
    from tara_automation.commfile.casechange import (
        CaseChangeEngine, describe_inch_errors,
    )
    from tara_automation.study import CaseSpec, detect_format_version

    cfg = study.config
    if not cfg.changes:
        return {}
    workbook = Path(cfg.changes)
    if not workbook.exists():
        raise FileNotFoundError(f"change workbook missing: {workbook}")

    done = cfg.runs.get("casechange", {})
    if done and _runs_exist(rm, done.values()) and not force:
        print(f"  casechange: already done {done} (use --force to re-run)")
        return done

    cs = read_workbook(workbook)
    if not cs.ok:
        raise RuntimeError(
            f"{workbook.name} has {len(cs.problems)} problem(s); fix them "
            f"first:\n  " + "\n  ".join(str(p) for p in cs.problems[:20]))

    inch_dir = study.inputs_dir / "inch"
    case_dir = study.root / "02_Base_Cases"
    inch_dir.mkdir(parents=True, exist_ok=True)
    case_dir.mkdir(parents=True, exist_ok=True)

    by_label = {c.label: c for c in cfg.cases}
    eng = CaseChangeEngine(tara_exe=machine.tara_exe)

    # Phase 1 — build every INCH and prepare every run (race-free numbering).
    prepared = []
    for sc in cs.enabled_scenarios():
        base = (Path(by_label[sc.base_case].path) if sc.base_case in by_label
                else Path(sc.base_case) if sc.base_case
                else Path(study.baseline().path))
        if not base.exists():
            raise FileNotFoundError(
                f"scenario {sc.id}: base case not found: {base}")
        out_case = (case_dir / sc.output_filename(base)).resolve()
        inch = write_inch(cs, sc, base, out_case, inch_dir / f"{sc.id}.inch")
        run = rm.create_run(
            engine="comm", case=base,
            label=f"{cfg.name} casechange {sc.id} ({sc.name})",
            options={"analysis": "casechange", "scenario": sc.id,
                     "inch": str(inch), "changes": str(workbook)})
        fmt, ver = detect_format_version(base)
        eng.prepare(run, base, inch, out_case, fmt=fmt, version=ver)
        prepared.append((sc, run, out_case))

    # Phase 2 — execute.
    if jobs > 1:
        print(f"  casechange: executing {len(prepared)} runs, "
              f"{jobs} in parallel")
    rcs = _execute_batch(eng, [(sc, run) for sc, run, _ in prepared], jobs)

    # Phase 3 — check + finalize sequentially, in scenario order.
    out: dict = {}
    failed = []
    for sc, run, out_case in prepared:
        run.manifest["tara_exit_code"] = rcs[run.number]
        checks = eng.check_outputs(run)
        run.manifest["casechange_checks"] = checks
        run.finalize("success" if checks["ok"] else "failed",
                     eng.summary_line(checks))
        if not checks["ok"]:
            print(f"  casechange {sc.id}: run {run.number} FAILED — "
                  f"{eng.summary_line(checks)}")
            if checks["inch_errors"]:
                print(describe_inch_errors(
                    [dict(e) for e in checks["inch_errors"]]))
            failed.append(f"run {run.number} ({sc.id})")
            continue
        fmt, ver = detect_format_version(out_case)
        study.add_case(CaseSpec(path=str(out_case), label=sc.id, role="post",
                                fmt=fmt, version=ver))
        counts = {k: v for k, v in cs.counts(sc.id).items() if v}
        print(f"  casechange {sc.id}: run {run.number} OK — "
              f"{sum(counts.values())} change(s) -> {out_case.name}")
        out[sc.id] = run.number

    cfg.runs["casechange"] = out
    study.save()
    if failed:
        raise RuntimeError("case-change run(s) failed: " + ", ".join(failed))
    return out


# ---------------------------------------------------------------- basecase ----
def stage_basecase(study: Study, rm: RunManager, machine,
                   force=False, jobs=1) -> dict:
    from tara_automation.commfile import CommEngine
    done = study.config.runs.get("basecase", {})
    if done and _runs_exist(rm, done.values()) and not force:
        print(f"  basecase: already done {done} (use --force to re-run)")
        return done
    eng = CommEngine(tara_exe=machine.tara_exe)
    # Phase 1 — prepare every run sequentially (race-free numbering).
    prepared = []
    for cs in study.ordered():
        run = rm.create_run(engine="comm", case=Path(cs.path),
                            label=f"{study.config.name} basecase {cs.label}",
                            options={"format": cs.fmt, "version": cs.version})
        eng.prepare(run, Path(cs.path), fmt=cs.fmt, version=cs.version)
        prepared.append((cs, run))
    # Phase 2 — execute (parallel if jobs > 1).
    if jobs > 1:
        print(f"  basecase: executing {len(prepared)} runs, {jobs} in parallel")
    rcs = _execute_batch(eng, prepared, jobs)
    # Phase 3 — check + finalize sequentially, in case order.
    out: dict = {}
    failed = []
    for cs, run in prepared:
        rc = rcs[run.number]
        checks = eng.check_outputs(run)
        missing = [t for t, ok in checks.items() if not ok]
        ok = rc == 0 and not missing
        run.finalize("success" if ok else "failed",
                     f"all {len(checks)} tables" if ok
                     else f"exit={rc}; missing {len(missing)}")
        print(f"  basecase {cs.label}: run {run.number} "
              f"{'OK' if ok else 'FAILED'}")
        if ok:
            out[cs.label] = run.number
        else:
            failed.append(f"run {run.number} ({cs.label})")
    if failed:
        raise RuntimeError("base-case run(s) failed: " + ", ".join(failed))
    study.config.runs["basecase"] = out
    study.save()
    return out


# ---------------------------------------------------------------- caseinfo ----
def stage_caseinfo(study: Study, rm: RunManager) -> Path | None:
    """Per-case information from the basecase runs (solution, totals, swing
    buses vs limits, areas, voltages, loadings, warnings) into
    05_Analysis/case_info/<label>.json, plus one markdown table across cases.
    File-only; voltages and loadings are limited to the footprint's study area
    once the footprint is compiled."""
    from tara_automation.case_info import (case_info, render_md,
                                           study_buses_from_tags, write)
    c = study.config
    done = c.runs.get("basecase", {})
    if not done:
        raise RuntimeError("caseinfo needs the basecase runs; run --stage basecase first")
    buses = None
    fp_json = c.deliverables.get("footprint")
    if fp_json and Path(fp_json).exists():
        rec = json.loads(Path(fp_json).read_text(encoding="utf-8"))
        buses = study_buses_from_tags(Path(rec["files"]["bus_tags"]), rec["study_area"])
    out_dir = study.analysis_dir / "case_info"
    infos = []
    for cs in study.ordered():
        n = done.get(cs.label)
        if n is None:
            continue
        info = case_info(rm.load_run(n).results_dir, buses, cs.label, n, cs.path)
        write(info, out_dir)
        infos.append(info)
        flag = f", {len(info['flags'])} flag(s)" if info["flags"] else ""
        print(f"  caseinfo {cs.label}: run {n}, "
              f"{'solved' if info['solution']['converged'] else 'NOT SOLVED'} in "
              f"{info['solution']['iterations']} iterations{flag}")
    md = study.analysis_dir / f"{c.name}_case_info.md"
    md.write_text(render_md(infos, c.name), encoding="utf-8")
    c.deliverables["case_info"] = str(out_dir)
    study.save()
    return md


# ------------------------------------------------------------- basecompare ----
def stage_basecompare(study: Study, rm: RunManager) -> list[str]:
    base_runs = study.config.runs.get("basecase")
    if not base_runs:
        raise RuntimeError("run the basecase stage first")
    DIGESTS.mkdir(parents=True, exist_ok=True)
    base_lbl = study.baseline().label
    a = rm.load_run(base_runs[base_lbl])
    made = []
    for cs in study.posts():
        b = rm.load_run(base_runs[cs.label])
        cmp = compare_cases(
            a.results_dir, b.results_dir,
            label_a=f"run {a.number} ({base_lbl})",
            label_b=f"run {b.number} ({cs.label})",
            fmt_a=a.manifest.get("options", {}).get("format"),
            fmt_b=b.manifest.get("options", {}).get("format"))
        out = DIGESTS / f"compare_{a.number}_vs_{b.number}.md"
        out.write_text(cmp.to_markdown(), encoding="utf-8")
        (study.analysis_dir / out.name).write_text(cmp.to_markdown(),
                                                   encoding="utf-8")
        made.append(str(out))
        print(f"  basecompare {base_lbl} vs {cs.label}: {out.name}")
    study.config.deliverables["basecompare"] = made
    study.save()
    return made


# ------------------------------------------------------------- contingency ----
def stage_contingency(study: Study, rm: RunManager, machine,
                      force=False, jobs=1) -> dict:
    from tara_automation.commfile.contingency import ContingencyEngine
    c = study.config
    if not (c.sub and c.con and c.mon):
        raise RuntimeError("study has no sub/con/mon; cannot run contingency")
    done = c.runs.get("contingency", {})
    if done and _runs_exist(rm, done.values()) and not force:
        print(f"  contingency: already done {done} (use --force to re-run)")
        return done
    eng = ContingencyEngine(tara_exe=machine.tara_exe)
    overrides = {"opt_cont": {"monBranRatingCont": c.rating_cont}}
    # Phase 1 — prepare every run sequentially (race-free numbering).
    prepared = []
    for cs in study.ordered():
        run = rm.create_run(
            engine="comm", case=Path(cs.path),
            label=f"{c.name} N-1 {cs.label} Rate{c.rating_cont}",
            options={"format": cs.fmt, "version": cs.version,
                     "analysis": "contingency"})
        eng.prepare(run, Path(cs.path), Path(c.sub), Path(c.con), Path(c.mon),
                    fmt=cs.fmt, version=cs.version,
                    loading_cutoff=c.loading_cutoff, options_overrides=overrides)
        prepared.append((cs, run))
    # Phase 2 — execute (parallel if jobs > 1).
    if jobs > 1:
        print(f"  contingency: executing {len(prepared)} runs, "
              f"{jobs} in parallel")
    rcs = _execute_batch(eng, prepared, jobs)
    # Phase 3 — check + finalize sequentially, in case order.
    out: dict = {}
    failed = []
    for cs, run in prepared:
        rc = rcs[run.number]
        checks = eng.check_outputs(run)
        if rc != 0 or not all(checks.values()):
            run.finalize("failed", f"exit={rc}")
            failed.append(f"run {run.number} ({cs.label}): exit={rc}")
            continue
        s = summarize(run.results_dir / "BranchViol.csv",
                      run.results_dir / "VoltViol.csv")
        run.manifest["violations_summary"] = s
        if not s.get("n_contingencies"):
            run.finalize("failed", "0 contingencies analyzed (study files?)")
            failed.append(f"run {run.number} ({cs.label}): 0 contingencies "
                          f"— check study-file reads")
            continue
        run.finalize("success", summary_line(s))
        print(f"  contingency {cs.label}: run {run.number} — {summary_line(s)}")
        out[cs.label] = run.number
    if failed:
        raise RuntimeError("contingency run(s) failed: " + "; ".join(failed))
    c.runs["contingency"] = out
    study.mark_for_review("contingency")
    study.save()
    return out


# --------------------------------------------------------------- footprint ----
def stage_footprint(study: Study, rm: RunManager) -> dict | None:
    """footprint.yaml -> compiled .sub (+ con/mon when 'auto') + bus tags.

    Compiled against the BASELINE's basecase run, because the selectors read
    the case's bus table. Points the study's sub (and con/mon) at the result.
    File-only: no engine run, so it is not gated — but changing the footprint
    changes what contingency / atc analyse, so it says when those are stale.
    """
    from tara_automation.footprint import (compile_footprint, load_network,
                                           write_outputs)
    c = study.config
    if not c.footprint:
        print("  footprint: none configured (study uses its own sub/con/mon)")
        return None
    base = study.baseline()
    run_no = c.runs.get("basecase", {}).get(base.label)
    if not run_no:
        raise RuntimeError("footprint needs the baseline's basecase run "
                           "(its bus table); run --stage basecase first")
    net = load_network(rm.load_run(run_no).results_dir)
    fp = compile_footprint(Path(c.footprint), net)
    atc = c.atc or {}
    names = list(atc.get("send") or []) + _recv_list(atc)
    previous = c.deliverables.get("footprint")
    old_sha = None
    if previous and Path(previous).exists():
        old_sha = json.loads(Path(previous).read_text(encoding="utf-8"))             .get("sha256", {})
    rec = write_outputs(fp, study.inputs_dir / "footprint", required_names=names,
                        fallback={"con": c.con, "mon": c.mon})
    files = rec["files"]
    c.sub = files["sub"]
    c.con = files.get("con", c.con)
    c.mon = files.get("mon", c.mon)
    c.deliverables["footprint"] = str(Path(files["json"]))
    groups = ", ".join(f"{n}={g['buses']}" for n, g in rec["groups"].items())
    print(f"  footprint: {groups} (network: basecase run {run_no})")
    for w in rec["warnings"]:
        print(f"  footprint WARNING: {w}")
    if old_sha is not None and old_sha != rec["sha256"]:
        stale = [s for s in ("contingency", "atc") if c.runs.get(s)]
        if stale:
            print(f"  footprint changed since {stale} ran: those results used "
                  f"the old study files. Re-run them with --force (their "
                  f"sign-offs lapse when the runs change).")
    study.save()
    return rec


# --------------------------------------------------------------------- atc ----
def _recv_list(atc: dict | None) -> list[str]:
    """`atc.recv` is one subsystem name or a list of them (one TrLim per
    (case, send, recv)). A single name keeps the old run keys `case|send`;
    a list keys runs `case|send>recv`."""
    r = (atc or {}).get("recv")
    if not r:
        return []
    return [r] if isinstance(r, str) else list(r)


def _atc_key(label: str, send: str, recv: str, multi: bool) -> str:
    return f"{label}|{send}>{recv}" if multi else f"{label}|{send}"


def _split_atc_key(key: str, default_recv: str | None) -> tuple[str, str, str | None]:
    label, rest = key.split("|", 1)
    send, _, recv = rest.partition(">")
    return label, send, (recv or default_recv)


def stage_atc(study: Study, rm: RunManager, machine, force=False, jobs=1) -> dict:
    """Transfer capability: one TrLim run per (case, sending subsystem).

    Needs an `atc` block in study.json:
        "atc": {"send": ["ED5_STATION"], "recv": "CAISO", "params": {...}}
    `recv` may also be a list (e.g. one sink per large load). All names
    must exist in the study's .sub file. No-ops without the block,
    so a study that is not an ATC study simply skips this stage.
    """
    from tara_automation.commfile.trlim import (TrLimEngine, check_inputs,
                                                summarize_trlim, summary_line)
    c = study.config
    if not c.atc:
        return {}
    if not (c.sub and c.con and c.mon):
        raise RuntimeError("study has no sub/con/mon; cannot run ATC")
    sends = list(c.atc.get("send") or [])
    recvs = _recv_list(c.atc)
    multi = not isinstance(c.atc.get("recv"), str)
    if not sends or not recvs:
        raise RuntimeError("study.atc needs both 'send' (list) and 'recv'")
    params = c.atc.get("params") or None

    done = c.runs.get("atc", {})
    if done and _runs_exist(rm, done.values()) and not force:
        print(f"  atc: already done {done} (use --force to re-run)")
        return done

    # Validate every case/subsystem before a single run number is used.
    for cs in study.ordered():
        for recv in recvs:
            check_inputs(Path(cs.path), Path(c.sub), Path(c.con), Path(c.mon),
                         sends, recv, fmt=cs.fmt)

    eng = TrLimEngine(tara_exe=machine.tara_exe)
    # Phase 1 — prepare every run sequentially (race-free numbering).
    prepared = []
    for cs in study.ordered():
        for send, recv in [(s_, r_) for s_ in sends for r_ in recvs]:
            run = rm.create_run(
                engine="comm", case=Path(cs.path),
                label=f"{c.name} ATC TrLim {send} -> {recv} {cs.label}",
                options={"analysis": "trlim", "TrLimMode": 1,
                         "report": "busDCMaxTransfer",
                         "send": send, "recv": recv, "case_label": cs.label})
            eng.prepare(run, Path(cs.path), Path(c.sub), Path(c.con),
                        Path(c.mon), send=send, recv=recv, fmt=cs.fmt,
                        params=params)
            prepared.append((_atc_key(cs.label, send, recv, multi), run))
    # Phase 2 — execute (parallel if jobs > 1).
    if jobs > 1:
        print(f"  atc: executing {len(prepared)} runs, {jobs} in parallel")
    rcs = _execute_batch(eng, prepared, jobs)
    # Phase 3 — check + finalize sequentially, in case order.
    out: dict = {}
    failed = []
    for key, run in prepared:
        run.manifest["tara_exit_code"] = rcs[run.number]
        chk = eng.check_outputs(run)
        if not chk["ok"]:
            run.manifest["trlim_check"] = {"problems": chk["problems"]}
            run.finalize("failed", "; ".join(chk["problems"]))
            failed.append(f"run {run.number} ({key}): {'; '.join(chk['problems'])}")
            continue
        summary = summarize_trlim(chk["report"])
        run.manifest["trlim_summary"] = summary
        run.manifest["trlim_check"] = {"notes": chk["notes"]}
        _, send, recv = _split_atc_key(key, recvs[0] if not multi else None)
        line = summary_line(send, recv, summary)
        run.finalize("success", line)
        print(f"  atc {key}: run {run.number} — {line}")
        for n in chk["notes"]:
            print(f"    note: {n}")
        out[key] = run.number
    if failed:
        raise RuntimeError("ATC run(s) failed: " + "; ".join(failed))
    c.runs["atc"] = out
    study.mark_for_review("atc")
    study.save()
    return out


# -------------------------------------------------------------- atcsummary ----
def stage_atcsummary(study: Study, rm: RunManager) -> Path | None:
    """One table of transfer limits across cases, from the ATC manifests.

    Reads only what the engine wrote (`trlim_summary`); the raw TrLim CSVs are
    never rewritten. Each row keeps the pre-existing-overload count next to the
    limit, because a limit quoted without it is misleading.
    """
    c = study.config
    atc_runs = c.runs.get("atc")
    if not atc_runs:
        return None
    rows, data = [], {}
    for key, n in atc_runs.items():
        run = rm.load_run(n)
        summary = run.manifest.get("trlim_summary", {})
        default_recv = c.atc.get("recv") if c.atc and isinstance(c.atc.get("recv"), str) else None
        case_label, send, recv = _split_atc_key(key, default_recv)
        data[key] = {"run": n, "case": case_label, "send": send, "recv": recv,
                     "summary": summary}
        for path, v in summary.get("paths", {}).items():
            rows.append({
                "case": case_label, "send": send, "recv": recv, "run": n,
                "path": path.rstrip("_"), "receiving": v["receiving"],
                "limit_mw": v["first_limit_mw"],
                "constraint": (v["first_limit"] or {}).get("short", ""),
                "contingency": (v["first_limit"] or {}).get("contingency", ""),
                "worsened": len(v["worsened_initial_overloads"]),
                "rows": v["rows"],
            })

    L = [f"# {c.name} — transfer capability (ATC) summary", "",
         f"**Objective:** {c.objective or 'TBD'}  ·  **Area:** {c.area or 'TBD'}"
         f"  ·  **Sink:** {', '.join(_recv_list(c.atc)) or '—'}", "",
         "Method: TARA TrLim, by-bus parallel mode (`TrLimMode 1`, report "
         "`busDCMaxTransfer`) — the PowerGEM UI recipe validated by run 54.", "",
         "| Case | Sending system | Bus path | First limit (MW) | Binding constraint |"
         " Contingency | Pre-existing overloads |",
         "|---|---|---|---:|---|---|---:|"]
    for r in rows:
        lim = "—" if r["limit_mw"] is None else f"{r['limit_mw']:.1f}"
        L.append(f"| {r['case']} | {r['send']} | {r['path']} | {lim} | "
                 f"{r['constraint']} | {r['contingency'][:40]} | {r['worsened']} |")
    L += ["", "**Reading this table.** *First limit* is the smallest transfer "
          "limit among constraints that still have headroom before any "
          "transfer. *Pre-existing overloads* counts constraints already over "
          "their rating that this transfer makes worse — on a strict reading "
          "those make the incremental capability zero, which is a judgment for "
          "the study engineer, not the script.", ""]

    study.analysis_dir.mkdir(parents=True, exist_ok=True)
    out = study.analysis_dir / f"{c.name}_atc_summary.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    DIGESTS.mkdir(parents=True, exist_ok=True)
    (DIGESTS / out.name).write_text("\n".join(L) + "\n", encoding="utf-8")
    js = study.analysis_dir / f"{c.name}_atc_summary.json"
    js.write_text(json.dumps({"rows": rows, "runs": data}, indent=2),
                  encoding="utf-8")
    c.deliverables["atc_summary"] = str(out)
    c.deliverables["atc_summary_json"] = str(js)
    study.save()
    print(f"  atcsummary: {out.name} ({len(rows)} path rows)")
    return out


# ------------------------------------------------------------- contcompare ----
def stage_contcompare(study: Study, rm: RunManager):
    cont = study.config.runs.get("contingency")
    if not cont:
        raise RuntimeError("run the contingency stage first")
    ordered = study.ordered()
    runs = [rm.load_run(cont[cs.label]) for cs in ordered]
    labels = [cs.label for cs in ordered]
    cmp = compare_cont(runs, labels)

    tag = f"Rate{study.config.rating_cont}"
    stem = (f"contingency_compare_"
            + "-".join(str(r.number) for r in runs) + f"_{tag}")
    DIGESTS.mkdir(parents=True, exist_ok=True)
    xlsx = contingency_workbook(cmp, DIGESTS / f"{stem}.xlsx")
    (DIGESTS / f"{stem}.md").write_text(cmp.to_markdown(), encoding="utf-8")
    # study-folder copies (the tracked digest travels; xlsx is git-ignored)
    study.analysis_dir.mkdir(parents=True, exist_ok=True)
    contingency_workbook(cmp, study.analysis_dir / f"{stem}.xlsx")
    (study.analysis_dir / f"{stem}.md").write_text(cmp.to_markdown(),
                                                   encoding="utf-8")
    study.config.deliverables["contcompare_xlsx"] = str(xlsx)
    study.save()
    print(f"  contcompare: {xlsx.name} (+ digest)")
    return cmp


# ----------------------------------------------------------------- summary ----
def stage_summary(study: Study, rm: RunManager, cmp=None) -> Path:
    c = study.config
    if cmp is None:
        if not c.runs.get("contingency"):
            raise RuntimeError("run the contingency stage first")
        cmp = compare_cont(
            [rm.load_run(n) for n in c.runs["contingency"].values()],
            list(c.runs["contingency"].keys()))
    base = cmp.cases[0]
    new_worse = [r for r in cmp.overload_rows
                 if "NEW" in r["status"] or "worse" in r["status"]]

    L = [f"# {c.name} — deliverability summary", "",
         f"**Objective:** {c.objective or 'TBD'}  ·  **Area:** {c.area or 'TBD'}"
         f"  ·  **Rating basis:** {base.rating}", ""]
    if c.notes:
        L += ["_Notes: " + "; ".join(c.notes) + "_", ""]
    L += ["## Cases & runs", "",
          "| Label | Role | Base-case run | N-1 run | Overloads ≥100% | NConv |",
          "|---|---|---|---|---|---|"]
    for cs in study.ordered():
        bn = c.runs.get("basecase", {}).get(cs.label, "—")
        cn = c.runs.get("contingency", {}).get(cs.label, "—")
        cd = next((cc for cc in cmp.cases if cc.label == cs.label), None)
        ov = cd.n_overloads if cd else "—"
        nc = sum(cd.nconv.values()) if cd else "—"
        L.append(f"| {cs.label} | {cs.role} | {bn} | {cn} | {ov} | {nc} |")

    L += ["", f"## New / worsened N-1 overloads vs {base.label} "
          f"({len(new_worse)})", ""]
    if new_worse:
        L += ["| Facility | kV | Contingency | "
              + " | ".join(cmp.labels) + " | Status |",
              "|---" * (4 + len(cmp.labels)) + "|"]
        for r in new_worse[:25]:
            loads = ["" if v is None else f"{v:.1f}" for v in r["loadings"]]
            L.append("| " + " | ".join(
                [r["facility"][:30], r["kv"], r["contingency"][:28]]
                + loads + [r["status"]]) + " |")
    else:
        L.append("_None — no case introduces or worsens an N-1 overload._")

    nconv_changes = {cc.label: len(cc.nconv) for cc in cmp.cases}
    L += ["", "## Stability", "",
          f"Distinct non-convergent contingencies per case: {nconv_changes}. "
          + ("Set is stable across cases — no new instability introduced."
             if len(set(nconv_changes.values())) == 1
             else "Set differs between cases — review the Non-Convergent tab."),
          "", "## Deliverables", ""]
    for k, v in c.deliverables.items():
        if isinstance(v, list):
            L += [f"- {k}: " + ", ".join(Path(p).name for p in v)]
        else:
            L.append(f"- {k}: `{Path(v).name}`")
    out = study.reports_dir / f"{c.name}_deliverability_summary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    study.config.deliverables["summary"] = str(out)
    study.save()
    print(f"  summary: {out}")
    return out


# ------------------------------------------------------------------ review ----
def stage_review(study: Study) -> None:
    """Best-effort headless verdict; the computable summary is the fallback."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  review: skipped (no ANTHROPIC_API_KEY; the AI review is "
              "optional). The computable summary is already written.")
        return
    cont = study.config.runs.get("contingency")
    if not cont:
        return
    try:
        from tara_automation.agents import run_agent
        runs = list(cont.values())
        result = run_agent("review_runs",
                           {"mode": "contingency_compare", "runs": runs})
        print("  review:\n" + result.get("verdict", ""))
    except Exception as e:  # noqa: BLE001 — review must never fail the pipeline
        print(f"  review: AI review unavailable ({e}). "
              f"Computable summary already written.")


# ------------------------------------------------------------------ report ----
def stage_report(study: Study, rm: RunManager) -> Path:
    """The gate. Packs the approved numbers for decks and external use.

    Refuses while any analysis stage that produced runs is unapproved, so a
    figure nobody reviewed cannot reach a slide (precedent: run 50's 37 MW,
    which was invalid and was already in a draft deck).
    """
    c = study.config
    needed = [s for s in GATED_STAGES if c.runs.get(s)]
    if not needed:
        raise RuntimeError("nothing to report: no analysis stage has runs")
    blocked = [s for s in needed if study.gate_status(s) != "approved"]
    if blocked:
        # Raise on the first, but name them all — one message, one fix list.
        statuses = ", ".join(f"{s}={study.gate_status(s)}" for s in blocked)
        try:
            study.require_approved(blocked[0])
        except GateError as e:
            raise GateError(f"report blocked — unapproved stage(s): {statuses}"
                            f"\n\n{e}") from None

    pack = {
        "study": c.name, "objective": c.objective, "area": c.area,
        "software": c.software, "generated_at": datetime.now().isoformat(timespec="seconds"),
        "approved": {s: study.gate(s) for s in needed},
        "cases": [{"label": cs.label, "role": cs.role,
                   "file": Path(cs.path).name} for cs in study.ordered()],
        "runs": {s: c.runs.get(s, {}) for s in needed},
        "deliverables": c.deliverables,
    }
    atc_json = c.deliverables.get("atc_summary_json")
    if atc_json and Path(atc_json).exists():
        pack["atc"] = json.loads(Path(atc_json).read_text(encoding="utf-8"))["rows"]

    out = study.reports_dir / f"{c.name}_results_pack.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pack, indent=2), encoding="utf-8")
    c.deliverables["results_pack"] = str(out)
    study.save()
    approvals = "; ".join(f"{s} by {study.gate(s).get('by')} "
                          f"({study.gate(s).get('at')})" for s in needed)
    print(f"  report: {out.name} — cleared for external use [{approvals}]")
    return out


# -------------------------------------------------------------------- all -----
STAGES = ["casechange", "basecase", "footprint", "caseinfo", "basecompare", "contingency",
          "contcompare", "atc", "atcsummary", "summary", "review", "report"]

# `report` is opt-in: it is the gated, outward-facing stage, so a bare
# `study.py run` does analysis and stops at the sign-off, rather than failing.
DEFAULT_STAGES = [s for s in STAGES if s != "report"]


# these invoke tara.exe
ENGINE_STAGES = {"casechange", "basecase", "contingency", "atc"}

# these produce numbers that get quoted, so they need a human sign-off before
# `report` will pack them (Study.mark_for_review / approve)
GATED_STAGES = ["contingency", "atc"]


def run_all(study: Study, stages: list[str] | None = None,
            force: bool = False, jobs: int = 1) -> None:
    stages = stages or DEFAULT_STAGES
    # machine.json (tara.exe path) is only needed for engine stages, so the
    # file-only stages (basecompare/contcompare/summary/review) run anywhere.
    machine = load_machine() if ENGINE_STAGES & set(stages) else None
    rm = RunManager(REPO_ROOT / "runs")
    cmp = None
    print(f"Study '{study.config.name}': stages {stages}"
          + (f", jobs={jobs}" if jobs > 1 else ""))
    if "casechange" in stages:
        stage_casechange(study, rm, machine, force, jobs)
    if "basecase" in stages:
        stage_basecase(study, rm, machine, force, jobs)
    if "footprint" in stages:
        stage_footprint(study, rm)
    if "caseinfo" in stages:
        stage_caseinfo(study, rm)
    if "basecompare" in stages:
        stage_basecompare(study, rm)
    if "contingency" in stages:
        stage_contingency(study, rm, machine, force, jobs)
    if "contcompare" in stages:
        cmp = stage_contcompare(study, rm)
    if "atc" in stages:
        stage_atc(study, rm, machine, force, jobs)
    if "atcsummary" in stages:
        stage_atcsummary(study, rm)
    if "summary" in stages:
        stage_summary(study, rm, cmp)
    if "review" in stages:
        stage_review(study)
    if "report" in stages:
        stage_report(study, rm)
    print(f"Done. Context: {study.config_path}")
