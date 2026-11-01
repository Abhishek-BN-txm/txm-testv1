"""TVA GD (generator deliverability) study of an APS cluster area.

    # build the study (file-only; reads the queue pillar + a basecase run)
    python scripts/gd_cluster_study.py init --area fccho --through 2025 --case-run 87 \
        --case "C:\\...\\tara-inputfls\\SRP_Cluster\\C26_33HS_Disch_Pre.epc"

    # prerequisite: replay the SRP C26 package's 7 Aug 2026 GD run and diff it
    python scripts/gd_cluster_study.py replay-srp --case "C:\\...\\C26_33HS_Ch_Post.epc"

    # case change (if any projects are added) + base case solves, via the pipeline
    python scripts/study.py run --name APS_FCCHO_GD_2025 --stage casechange basecase

    # GI workbook from the solved case that GD will read (post case if one was built)
    python scripts/gd_cluster_study.py workbook --name APS_FCCHO_GD_2025

    # step 1 flowgate screening, then step 2 TVA GD (each is one runs/N via the run manager)
    python scripts/gd_cluster_study.py run --name APS_FCCHO_GD_2025 --step screen
    python scripts/gd_cluster_study.py run --name APS_FCCHO_GD_2025 --step gd

Projects are added only at POI buses confirmed in poi_map.csv (aps_queue.py
accept-poi). After accepting more POIs, re-run `init --force`: the project
table, change workbook, .sub and GI workbook are regenerated. The 2026 cluster
is `--through 2026`. Another area = another AREA_PROFILES entry in
src/tara_automation/gd_cluster.py.

The GD decks are still DRAFT (docs/tasks/30_tva_gd_feasibility.md): run
`replay-srp` first and send back its run number.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path, PureWindowsPath

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from tara_automation import gd_cluster as gc  # noqa: E402
from tara_automation.commfile import gd as gdeck  # noqa: E402
from tara_automation.ingest.aps import AREAS, QueueData, library  # noqa: E402
from tara_automation.machine import REPO_ROOT  # noqa: E402
from tara_automation.study import CaseSpec, Study, StudyConfig  # noqa: E402

STUDIES = REPO_ROOT / "studies"
RUNS = REPO_ROOT / "runs"
TEMPLATE_BASE = STUDIES / "_templates" / "APS_LargeLoad_Deliverability" / "base"
SRP_PACKAGE = (r"C:\Data"
               r"\TARA_2602_005_TVAGD_RC13_SRP\TARA_2602_005_TVAGD_RC13_SRP")
SRP_CASE = r"C:\Projects\tara-inputfls\SRP_Cluster\C26_33HS_Ch_Post.epc"
FOLDERS = ["01_References", "02_Base_Cases", "03_TARA_Inputs", "04_TARA_Outputs",
           "05_Analysis", "06_Reports", "07_Correspondence"]


class PathMap:
    """Writes repo paths as the machine that will run TARA sees them.
    Default: this machine. --repo-root-as maps them to a Windows root when the
    study is generated elsewhere (the Linux sandbox)."""

    def __init__(self, win_root: str | None):
        self.win_root = win_root

    def __call__(self, p: Path) -> str:
        p = Path(p).resolve()
        if not self.win_root:
            return str(p)
        rel = p.relative_to(REPO_ROOT.resolve())
        return str(PureWindowsPath(self.win_root, *rel.parts))


def _fname(p: str) -> str:
    return PureWindowsPath(p).name if "\\" in p else Path(p).name


def _existing(spec: list[str]) -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for s in spec or []:
        q, _, units = s.partition("=")
        out[q.strip().upper()] = [(int(b), i) for b, _, i in
                                  (u.partition(":") for u in units.split(",") if u)]
    return out


# ---------------------------------------------------------------- init -------
def cmd_init(a) -> int:
    from tara_automation.footprint import FootprintError, compile_footprint, load_network
    key, through = a.area, a.through
    if key not in gc.AREA_PROFILES:
        print(f"STOP: no AREA_PROFILES entry for '{key}' (have {list(gc.AREA_PROFILES)})")
        return 2
    prof = gc.AREA_PROFILES[key]
    name = a.name or f"APS_{key.upper()}_GD_{through}"
    root = STUDIES / name
    if (root / "study.json").exists() and not a.force:
        print(f"STOP: {root} exists (use --force to regenerate its inputs)")
        return 2
    res = RUNS / str(a.case_run) / "results"
    if not (res / "GenData.csv").exists():
        print(f"STOP: run {a.case_run} has no GenData.csv")
        return 2
    man = json.loads((RUNS / str(a.case_run) / "manifest.json").read_text(encoding="utf-8"))
    case = a.case or man.get("case")
    if _fname(case).lower() != _fname(man.get("case", "")).lower():
        print(f"STOP: run {a.case_run} solved {man.get('case')}, not {case}")
        return 2
    pm = PathMap(a.repo_root_as)

    qd = QueueData()
    units = gc.read_gens(res)
    net = load_network(res)
    buses = library.buses_from_run(res)
    projects = gc.load_projects(qd, key, through)
    gc.classify(projects, units, net.adjacency, buses, library.read_poi_map(qd),
                prof["model_areas"], existing=_existing(a.existing),
                include=set(a.include or []), exclude=set(a.exclude or []))

    inp = root / "03_TARA_Inputs"
    for d in FOLDERS:
        (root / d).mkdir(parents=True, exist_ok=True)
    tag = f"{key.upper()}{through[-2:]}"            # subsystem / case names
    # TVA GD reads the study year from characters 4-5 of the queue name
    # (SRP26 -> 26; FCCHO25 gave "Study Year = 1000" and no queue units in
    # run 117), so the queue name is 3 letters + 2-digit year.
    # Letters: SRP's working inputs stop recognising queue units when SRP26 is
    # renamed FCC26 or APS26 (runs 135, 139), so the letters are taken to be the
    # utility/area name of the units (APS for area 14). Not yet proven on APS
    # data: see docs/tasks/31_gd_cluster_study.md.
    qname = f"APS{through[-2:]}"
    scen = f"GD{through[-2:]}"
    # TVA GD matches the study queue to the study subsystem by name: SRP's
    # inputs fail when StudyQueue no longer prefixes the subsystem (run 135),
    # so the study subsystem is "<queue>_STUDY".
    study_name = f"{qname}_STUDY"
    export_name = f"{tag}_EXPORT"

    # ---- change workbook (only when something is added)
    adds = gc.new_units(projects, units)
    changes = None
    out_case = f"{Path(PureWindowsPath(case)).stem}_{tag}.epc"
    if adds:
        changes = gc.write_change_workbook(
            inp / "changes.xlsx", adds, scen, f"{AREAS[key][2]} NRIS through {through}",
            f"Adds {len(adds)} NRIS project(s) of the {AREAS[key][2]} area (serial + clusters "
            f"through {through}) at confirmed POI buses, offline at Pmax; already-modelled study "
            f"units taken offline (TVA GD dispatches study units).",
            out_case, unit_changes=gc.offline_existing(projects))

    # ---- study units: modelled + added (keys only; values come from the case read later)
    study_units = []
    for p in projects:
        if p.action == "already modelled":
            poi = p.modelled_poi[0] if p.modelled_poi else (None, "")
            for u in p.modelled:
                study_units.append({"q": p.q, "bus": u.bus, "id": u.id, "nris_mw": p.mw,
                                    "poi_bus": poi[0], "poi_name": poi[1], "source": "case"})
    for r in adds:
        p = next(p for p in projects if p.action == "add" and p.poi_bus == r["BusNum"]
                 and r["Comment"].startswith(p.q + " "))
        study_units.append({"q": p.q, "bus": r["BusNum"], "id": r["GenID"], "nris_mw": p.mw,
                            "poi_bus": p.poi_bus, "poi_name": p.poi_bus_name, "source": "added",
                            "pmax": r["GenMWMax"], "pmin": r["GenMWMin"]})

    # ---- footprint (sink groups, study area, screening pool) compiled on the case
    sink_name, weights, sink_src = gc.sink_groups(qd, key, through)
    region = [b for b, nm in prof["region_hubs"].items()
              if b in net.buses and net.buses[b].name.strip() == nm]
    bad = [b for b in prof["region_hubs"] if b not in region]
    # the area-14 sink also leaves out the study units and every plant radially
    # behind a region hub (collector/gen buses below 69 kV the `near` walk skips)
    plant_side = set().union(*(gc.radial_behind(net.adjacency, b) for b in region)) if region else set()
    spec = gc.footprint_spec(prof, sink_name, weights, region, study_name,
                             exclude_buses=sorted(plant_side | {s["bus"] for s in study_units
                                                                if s["bus"] in net.buses}))
    fy = inp / "gd_footprint.yaml"
    fy.write_text("# Generated by scripts/gd_cluster_study.py: compiled against run "
                  f"{a.case_run} to resolve the GD subsystems (see gd.sub).\n"
                  + __import__("yaml").safe_dump(spec, sort_keys=False), encoding="utf-8")
    try:
        fp = compile_footprint(fy, net)
    except FootprintError as e:
        print(f"STOP: footprint: {e}")
        return 1
    gi_name = f"{name}_GI.xlsx"
    sub_text = gc.render_gd_sub(
        fp, sink_name=sink_name, weights=weights, study_name=study_name,
        study_buses=[s["bus"] for s in study_units], export_name=export_name,
        export_areas=prof["export_areas"], gi_workbook=gi_name,
        exclude_buses=sorted({u.bus for u in units if u.area in prof["export_areas"]
                              and "FUT" in u.name.upper()}),
        header=[f"Generated by scripts/gd_cluster_study.py on {date.today()} for {name}",
                f"case {case} (subsystems resolved on run {a.case_run})",
                f"sink weights: {sink_src} load-ratio table (HS %)",
                "Do not edit: change the inputs and re-run init --force."])
    (inp / "gd.sub").write_text(sub_text, encoding="utf-8")
    for f in ("AZ.con", "AZ.mon"):
        shutil.copyfile(TEMPLATE_BASE / f, inp / f)

    # ---- GI workbook from the base case (+ added units, as the post case will hold them)
    gi_units = list(units) + [gc.Unit(s["bus"], f"{s['q']}_GD", 0.0, prof["export_areas"][0], s["id"],
                                      1, 0.0, s["pmin"], s["pmax"])
                              for s in study_units if s["source"] == "added"]
    gc.write_gi_workbook(inp / gi_name, gc.gi_generator_rows(gi_units, prof["export_areas"],
                                                             study_units, qname))

    # ---- project table
    with open(inp / "gd_projects.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["q", "cycle", "mw", "service_queue", "service_report", "tech", "status",
                    "report_pmax", "report_pmin", "poi_text", "poi_report", "poi_bus_confirmed",
                    "candidates", "modelled_units", "radial_evidence", "action", "reason", "notes"])
        for p in projects:
            w.writerow([p.q, p.cycle, p.mw, p.service, p.service_report, p.tech, p.status,
                        p.rpt.get("pmax", ""), p.rpt.get("pmin", ""), p.poi_text, p.poi_report,
                        p.poi_bus or "", " ".join(f"{b}:{n.strip()}" for b, n, *_ in p.candidates[:3]),
                        " ".join(f"{u.bus}:{u.id}" for u in p.modelled),
                        " ".join(f"{u.bus}:{u.id}@{b}" for u, b in p.radial_evidence),
                        p.action, p.reason, "; ".join(p.notes)])
    table = gc.project_table_md(projects)
    (root / "05_Analysis" / f"{name}_project_table.md").write_text(table, encoding="utf-8")

    # ---- study.json (pipeline) + gd.json (GD settings and runs)
    cfg = StudyConfig(
        name=name, created=date.today().isoformat(),
        objective=(f"TVA GD deliverability of the {AREAS[key][2]} NRIS serial and cluster "
                   f"projects through the {through} cluster, report sink, N-1 AZ"),
        area=f"APS {AREAS[key][2]} (areas {prof['model_areas']})", software="TARA (PowerGEM)",
        cases=[CaseSpec(path=case, label="pre", role="baseline", fmt="EPC", version=21)],
        sub=pm(inp / "gd.sub"), con=pm(inp / "AZ.con"), mon=pm(inp / "AZ.mon"),
        rating_cont=2, loading_cutoff=70.0, changes=pm(changes) if changes else None,
        notes=[f"basecase tables for the build: run {a.case_run}",
               "GD runs are recorded in 03_TARA_Inputs/gd.json, not here"])
    old = root / "study.json"
    if old.exists():   # keep runs/gates of a previous build only when inputs did not change
        prev = json.loads(old.read_text(encoding="utf-8"))
        cfg.notes.append(f"regenerated {date.today()}; previous runs {prev.get('runs', {})}")
    Study(root, cfg).save()
    gdj = {
        "area": key, "through": through, "queue": qname, "scenario_id": scen,
        "case_run": a.case_run, "case": case,
        "screen": {"src": "GD_SCREEN_ALL", "ref": "GD_SCREEN_ALL", "params": gdeck.SCREEN_DEFAULTS},
        "gd": {"export": study_name, "ref": sink_name,   # SRP's working run: wdSubSysName = the study subsystem (taralog), not the export pool
               "params": {**gdeck.GD_DEFAULTS, "study_queue": qname, "study_selection": 1}},
        "sub": pm(inp / "gd.sub"), "con": pm(inp / "AZ.con"), "mon": pm(inp / "AZ.mon"),
        "gi_workbook": pm(inp / gi_name), "gi_built_from": {"label": "pre", "run": a.case_run,
                                                           "includes_added_units": bool(adds)},
        "study_units": study_units, "sink": {"name": sink_name, "weights_pct": weights,
                                             "source": sink_src, "region_hubs": region},
        "runs": {"screen": {}, "gd": {}},
    }
    (inp / "gd.json").write_text(json.dumps(gdj, indent=2), encoding="utf-8")
    _write_memo(root, name, a, projects, adds, study_units, sink_name, weights, sink_src, region, bad, fp)

    s = gc.summary(projects)
    print(f"Study {name} at {root}")
    for k, v in s.items():
        print(f"  {k:<17} {v['n']:>3}  {v['mw']:>8.1f} MW  {' '.join(v['q'])}")
    print(f"  study units: {len(study_units)} generators; added: {len(adds)}; "
          f"change workbook: {'yes' if changes else 'none (nothing to add yet)'}")
    print(f"  sink {sink_name}: " + ", ".join(f"{k}:{v:g}" for k, v in weights.items()))
    if bad:
        print(f"  WARNING region hub buses not found by name: {bad}")
    return 0


def _write_memo(root, name, a, projects, adds, units, sink, w, src, region, bad, fp) -> None:
    s = gc.summary(projects)
    L = [f"# {name}: TVA GD study memo (generated)", "",
         f"Area `{a.area}`, cycles Serial..{a.through}, case run {a.case_run}.", "",
         "## Project set", ""]
    for k, v in s.items():
        L.append(f"- **{k}**: {v['n']} projects, {v['mw']:g} MW ({' '.join(v['q'])})")
    L += ["", "NRIS vs ERIS: active queue *Interconnection Request Type* (current posting); "
          "the latest cluster report's service is shown alongside. Withdrawn requests are not "
          "in the active queue and so not listed.", "",
          f"Full table: `05_Analysis/{name}_project_table.md` and `03_TARA_Inputs/gd_projects.csv`.", "",
          "## Modelling", "",
          f"- Study units: {len(units)} generators (" + ", ".join(sorted({u['q'] for u in units})) + ")",
          f"- Added by change workbook: {len(adds)} unit(s) at confirmed POI buses, Pgen 0, "
          "Pmax = NRIS MW, Pmin = report charging MW for storage.",
          f"- Sink `{sink}` (wdSubSysRefName): PARTICIPATE of per-area subsystems, weights = "
          f"{src} HS load ratio: " + ", ".join(f"area {k} {v:g}%" for k, v in w.items())
          + "; each area scales by Pgen (SCALE ALL GENERATION). Area 14 excludes buses within "
          f"{gc.AREA_PROFILES[a.area]['region_hops']} hops (>= 69 kV) of {region} "
          "(report: AZ share taken outside the study region).",
          "- GD export pool: area 14 SCALE ALL FOR EXPORT INCLUDE OFFLINE, STUDY = study units.",
          "- Screening: source = reference = GD_SCREEN_ALL (all generators of the AZ + sink areas, "
          "incl. offline), as SRP's WORLD_ALL_GEN; wdPumpMode 1.",
          "- Contingencies: AZ.con (single branch in AZ + single tie), monitoring AZ.mon "
          "(50-800 kV in AZ + ties).", ""]
    if bad:
        L.append(f"WARNING: region hubs not found by name in the case: {bad}")
    L += ["## Groups (compiled on the case)", ""]
    L += [f"- {g.name}: {g.role}, {len(g.buses)} buses" for g in fp.groups]
    (root / f"00_{name}_Study_Memo.md").write_text("\n".join(L) + "\n", encoding="utf-8")


# ------------------------------------------------------------- workbook ------
def cmd_workbook(a) -> int:
    study = Study.load(STUDIES, a.name)
    inp = study.inputs_dir
    gdj = json.loads((inp / "gd.json").read_text(encoding="utf-8"))
    label = a.case_label or _default_label(study, gdj)
    run_no = a.run or study.config.runs.get("basecase", {}).get(label)
    if not run_no:
        print(f"STOP: no basecase run for case '{label}'; run study.py --stage basecase first or pass --run")
        return 2
    units = gc.read_gens(RUNS / str(run_no) / "results")
    keys = {u.key for u in units}
    miss = [(s["bus"], s["id"]) for s in gdj["study_units"] if (s["bus"], s["id"]) not in keys]
    if miss:
        print(f"STOP: study units not in run {run_no}'s case: {miss}")
        return 1
    prof = gc.AREA_PROFILES[gdj["area"]]
    path = Path(gdj["gi_workbook"]) if Path(gdj["gi_workbook"]).exists() else \
        inp / Path(PureWindowsPath(gdj["gi_workbook"])).name
    gc.write_gi_workbook(path, gc.gi_generator_rows(units, prof["export_areas"],
                                                    gdj["study_units"], gdj["queue"]))
    gdj["gi_built_from"] = {"label": label, "run": run_no, "includes_added_units": False}
    (inp / "gd.json").write_text(json.dumps(gdj, indent=2), encoding="utf-8")
    print(f"GI workbook {path.name} rebuilt from run {run_no} (case '{label}')")
    return 0


def _default_label(study: Study, gdj: dict) -> str:
    return gdj["scenario_id"] if study.has_case(gdj["scenario_id"]) else study.baseline().label


# ------------------------------------------------------------------ run ------
def cmd_run(a) -> int:
    from tara_automation.machine import load_machine
    from tara_automation.run_manager import RunManager
    study = Study.load(STUDIES, a.name)
    inp = study.inputs_dir
    gdj = json.loads((inp / "gd.json").read_text(encoding="utf-8"))
    label = a.case_label or _default_label(study, gdj)
    cs = next(c for c in study.config.cases if c.label == label)
    if gdj["runs"]["screen"] == {} and a.step == "gd":
        print("STOP: run --step screen first (GD reads its .fgt)")
        return 2
    if a.step == "gd" and gdj["gi_built_from"]["label"] != label:
        print(f"STOP: GI workbook was built from case '{gdj['gi_built_from']['label']}', "
              f"GD would read '{label}'. Run: gd_cluster_study.py workbook --name {a.name}")
        return 2
    for k in ("sub", "con", "mon", "gi_workbook"):
        if not Path(gdj[k]).exists():
            print(f"STOP: {k} not found: {gdj[k]}")
            return 2
    if not Path(cs.path).exists():
        print(f"STOP: case not found: {cs.path}")
        return 2
    machine = load_machine()
    rm = RunManager(RUNS)
    run = rm.create_run(engine="comm", case=cs.path,
                        label=f"{a.name} GD {a.step} {label}",
                        options={"analysis": f"gd_{a.step}", "case_label": label})
    eng = gdeck.GDEngine(machine.tara_exe)
    res = run.results_dir.resolve()
    scen = f"{a.name}_{label}"
    if a.step == "screen":
        fgt = res / "flowgates.fgt"
        deck = gdeck.render_screen_deck(
            results_dir=res, trace_log=(run.logs_dir / gdeck.TRACE_SCREEN).resolve(),
            case=cs.path, sub=gdj["sub"], con=gdj["con"], mon=gdj["mon"], fgt=fgt,
            src=gdj["screen"]["src"], ref=gdj["screen"]["ref"], scenario=scen,
            params=gdj["screen"]["params"])
        meta = {"fgt": str(fgt), "case_label": label}
    else:
        prev = gdj["runs"]["screen"].get(label)
        if not prev:
            print(f"STOP: no screening run for case '{label}'")
            return 2
        fgt = Path(a.fgt) if a.fgt else RUNS / str(prev) / "results" / "flowgates.fgt"
        deck = gdeck.render_gd_deck(
            results_dir=res, trace_log=(run.logs_dir / gdeck.TRACE_GD).resolve(),
            case=cs.path, sub=gdj["sub"], fgt=fgt.resolve(), export=gdj["gd"]["export"],
            ref=gdj["gd"]["ref"], scenario=scen, params=gdj["gd"]["params"])
        meta = {"fgt": str(fgt), "screen_run": prev, "case_label": label,
                "gi_workbook": gdj["gi_workbook"]}
    eng.prepare(run, a.step, deck, meta)
    print(f"run {run.number}: GD {a.step} on '{label}' ({Path(cs.path).name}) ...")
    eng.execute(run)
    checks = eng.check_outputs(run)
    run.manifest["gd_checks"] = checks
    run.finalize("success" if checks["ok"] else "failed",
                 "; ".join(checks["problems"]) or "outputs present")
    gdj["runs"][a.step][label] = run.number
    (inp / "gd.json").write_text(json.dumps(gdj, indent=2), encoding="utf-8")
    print(f"run {run.number}: {'OK' if checks['ok'] else 'FAILED'}")
    for p in checks["problems"]:
        print(f"  problem: {p}")
    for n in checks["notes"]:
        print(f"  note: {n}")
    return 0 if checks["ok"] else 1


# ----------------------------------------------------------- replay-srp ------
def cmd_replay(a) -> int:
    from tara_automation.machine import load_machine
    from tara_automation.run_manager import RunManager
    pkg = Path(a.package)
    sub = pkg / "SRP_TVAGD_TSR_C.sub"
    fgt = pkg / "cases" / (a.fgt or "2033_C26_Post_C.fgt")
    ref = pkg / "temp"
    case = Path(a.case)
    for p in (sub, fgt, pkg / "ras.zip", ref / "TVAGDLoadingSummary.csv", case):
        if not p.exists():
            print(f"STOP: not found: {p}")
            return 2
    gi = gdeck.gi_data_file(sub)
    if not gi or not gi.exists():
        print(f"STOP: GI workbook named in {sub.name} not found: {gi}")
        return 2
    machine = load_machine()
    run = RunManager(RUNS).create_run(engine="comm", case=case, label="TVA GD replay: SRP C26 7 Aug 2026 UI run",
                                      options={"analysis": "gd_replay", "package": str(pkg)})
    # copy inputs into the run (the package stays read-only)
    inputs = run.root / "inputs"
    inputs.mkdir(exist_ok=True)
    for p in (sub, gi, fgt):
        shutil.copyfile(p, inputs / p.name)
    with zipfile.ZipFile(pkg / "ras.zip") as z:
        (inputs / "SRP_RAS.aux").write_bytes(z.read("ras/SRP_RAS.aux"))
    res = run.results_dir.resolve()
    deck = gdeck.render_gd_deck(
        results_dir=res, trace_log=(run.logs_dir / gdeck.TRACE_GD).resolve(), case=case,
        sub=(inputs / sub.name).resolve(), fgt=(inputs / fgt.name).resolve(),
        export=a.export or "SRP26_C", ref="IMPORT_C", scenario="33HS_Ch_Post",
        ras=(inputs / "SRP_RAS.aux").resolve(),
        params={**gdeck.replay_params(), **({"study_queue": a.queue} if a.queue else {})})
    eng = gdeck.GDEngine(machine.tara_exe)
    eng.prepare(run, "gd", deck, {"fgt": str(inputs / fgt.name), "replay_of": str(ref)})
    print(f"run {run.number}: replaying the SRP 7 Aug 2026 TVA GD run ...")
    eng.execute(run)
    checks = eng.check_outputs(run)
    cmp = gdeck.compare_outputs(run.results_dir, ref)
    run.manifest["gd_checks"] = checks
    run.manifest["replay_compare"] = cmp
    ok = checks["ok"] and all(c.get("match") for c in cmp
                              if c["file"] not in gdeck.UNSEEN_KEYWORD_REPORTS)
    run.finalize("success" if checks["ok"] else "failed",
                 f"replay {'MATCHES' if ok else 'DIFFERS'}")
    _print_compare(cmp)
    print(f"run {run.number}: replay {'MATCHES the UI run' if ok else 'does NOT match'}")
    return 0 if ok else 1


def _print_compare(cmp: list[dict]) -> None:
    for c in cmp:
        if c.get("missing"):
            print(f"  {c['file']:<40} MISSING {c['missing']}")
            continue
        print(f"  {c['file']:<40} {'match' if c['match'] else 'DIFF'}  rows {c['rows'][0]} vs "
              f"{c['rows'][1]}  header {'=' if c['header_equal'] else '!='}  cell diffs {c['cell_diffs']}")
        for e in c["examples"]:
            print(f"      {e}")


def cmd_compare(a) -> int:
    ref = Path(a.reference or (Path(a.package) / "temp"))
    cmp = gdeck.compare_outputs(RUNS / str(a.run) / "results", ref)
    _print_compare(cmp)
    return 0 if all(c.get("match") for c in cmp) else 1


# ---------------------------------------------------------------- decks ------
def cmd_decks(a) -> int:
    """Render both decks into a folder without creating a run (dry run)."""
    study = Study.load(STUDIES, a.name)
    gdj = json.loads((study.inputs_dir / "gd.json").read_text(encoding="utf-8"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    label = a.case_label or _default_label(study, gdj)
    cs = next(c for c in study.config.cases if c.label == label)
    r = out / "results"
    s = gdeck.render_screen_deck(results_dir=r, trace_log=out / gdeck.TRACE_SCREEN, case=cs.path,
                                 sub=gdj["sub"], con=gdj["con"], mon=gdj["mon"], fgt=r / "flowgates.fgt",
                                 src=gdj["screen"]["src"], ref=gdj["screen"]["ref"],
                                 scenario=f"{a.name}_{label}", params=gdj["screen"]["params"])
    g = gdeck.render_gd_deck(results_dir=r, trace_log=out / gdeck.TRACE_GD, case=cs.path, sub=gdj["sub"],
                             fgt=r / "flowgates.fgt", export=gdj["gd"]["export"], ref=gdj["gd"]["ref"],
                             scenario=f"{a.name}_{label}", params=gdj["gd"]["params"])
    (out / "gd_screen.txt").write_text(s, encoding="utf-8")
    (out / "gd_tvagd.txt").write_text(g, encoding="utf-8")
    names = gdeck.defined_subsystems(study.inputs_dir / "gd.sub")
    need = {gdj["screen"]["src"], gdj["screen"]["ref"], gdj["gd"]["export"], gdj["gd"]["ref"], "AZ"}
    print(f"decks written to {out}; subsystems missing from gd.sub: {sorted(need - names) or 'none'}")
    return 0 if need <= names else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="build the study from the queue + a basecase run")
    i.add_argument("--area", required=True)
    i.add_argument("--through", required=True, choices=gc.CYCLES[1:])
    i.add_argument("--case-run", type=int, required=True, help="basecase run of the case")
    i.add_argument("--case", default=None, help="case path (default: the run's manifest)")
    i.add_argument("--name", default=None)
    i.add_argument("--existing", action="append", default=[],
                   help="Qnnn=bus:id,bus:id  units already in the case for a project")
    i.add_argument("--include", nargs="*", default=[], help="treat as NRIS anyway")
    i.add_argument("--exclude", nargs="*", default=[], help="leave out")
    i.add_argument("--repo-root-as", default=None,
                   help="write repo paths under this Windows root (when generating off-machine)")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)
    w = sub.add_parser("workbook", help="rebuild the GI workbook from a solved case")
    w.add_argument("--name", required=True)
    w.add_argument("--case-label", default=None)
    w.add_argument("--run", type=int, default=None)
    w.set_defaults(func=cmd_workbook)
    r = sub.add_parser("run", help="run one GD step through the run manager (tara.exe)")
    r.add_argument("--name", required=True)
    r.add_argument("--step", required=True, choices=("screen", "gd"))
    r.add_argument("--case-label", default=None)
    r.add_argument("--fgt", default=None, help="GD step: use this flowgate file (diagnostics)")
    r.set_defaults(func=cmd_run)
    p = sub.add_parser("replay-srp", help="replay the SRP package's 7 Aug 2026 GD run and diff it")
    p.add_argument("--package", default=SRP_PACKAGE)
    p.add_argument("--case", default=SRP_CASE)
    p.add_argument("--queue", default=None, help="override StudyQueue (diagnostics)")
    p.add_argument("--export", default=None, help="override wdSubSysName (diagnostics)")
    p.add_argument("--fgt", default=None, help="flowgate file in the package's cases folder "
                   "(default 2033_C26_Post_C.fgt; use the case year's file for a smoke test)")
    p.set_defaults(func=cmd_replay)
    c = sub.add_parser("compare", help="diff a GD run's CSVs against reference CSVs")
    c.add_argument("--run", type=int, required=True)
    c.add_argument("--reference", default=None)
    c.add_argument("--package", default=SRP_PACKAGE)
    c.set_defaults(func=cmd_compare)
    d = sub.add_parser("decks", help="render both decks to a folder (dry run, no tara.exe)")
    d.add_argument("--name", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--case-label", default=None)
    d.set_defaults(func=cmd_decks)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
