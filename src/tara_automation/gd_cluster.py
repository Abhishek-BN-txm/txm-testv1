"""Cluster-area TVA GD study builder (queue -> project table -> study inputs).

    queue_by_study_area.csv + active queue + export/generators.csv + reports
        -> project table: every active request of one APS study area through a
           cluster cycle, NRIS/ERIS, already in the case?, POI bus, action
    + a basecase run of the chosen case (BusData/GenData/BranchData)
        -> change workbook rows (New_Generators) for projects to add
        -> gd.sub   (AZ study area, screening pool, GD export with STUDY block,
                     report sink as PARTICIPATE of per-area subsystems, TVA_GI_DATA)
        -> GI data workbook (Generator / Load / TSR + cost tables)

Hard rules carried from the queue pillar (ingest/aps/library.py):
- A POI bus is never guessed. A project is added only at a bus that is in
  poi_map.csv (written by a person with `scripts/aps_queue.py accept-poi`).
  Everything else is listed as blocked, with candidate buses as evidence.
- Line-tap POIs need a new bus built on the change workbook (Line_Taps);
  they stay blocked here.
- A project counts as already modelled only when case generators carry its
  queue number in their bus name (e.g. Q230_SPV1), or the user names the units
  (`existing` override). Units that merely sit radially behind a candidate POI
  are reported as evidence, never used silently.

NRIS vs ERIS: the active queue's "Interconnection Request Type" (current
posting) decides; "Network Resource" in it = NRIS. The latest report's service
is shown alongside and every disagreement is listed.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tara_automation.ingest.aps import AREAS, QueueData
from tara_automation.ingest.aps import library

CYCLES = ["Serial", "Transition", "2024", "2025", "2026"]
PROCESS_TO_CYCLE = {"Pre-2024 (Transition or serial)": "Serial", "Transition Cluster": "Transition",
                    "2024 Cluster": "2024", "2025 Cluster": "2025", "2026 Cluster": "2026"}

# Per-area settings that are judgement, kept as data so another area is a new entry.
# region_hubs: substations whose neighbourhood is the cluster's "study region"; the
# report takes the AZ share of the sink "outside the study region", so the area-14
# sink excludes buses within `region_hops` of these (walking >= 69 kV).
# Bus numbers are checked against the case by name before use.
AREA_PROFILES: dict[str, dict] = {
    "fccho": {
        "model_areas": [14],
        "export_areas": [14],
        "region_hubs": {14000: "CHOLLA", 14100: "CHOLLA W", 14115: "CHOLLA E", 14204: "CHOLLA",
                        14001: "FOURCORN", 14101: "FOURCORN", 14211: "FOURCORN",
                        14116: "SITGREVS", 14114: "CHEVELON", 14002: "MOENKOPI"},
        "region_hops": 2,
        "screen_areas": [10, 14, 15, 16, 17, 18, 19, 21, 22, 24, 26, 30, 65, 73],
        "study_area": {"name": "AZ", "areas": [14, 15, 19, 17, 16, 21, 24]},
    },
}


# ----------------------------------------------------------------- case data --
@dataclass
class Unit:
    bus: int
    name: str
    kv: float
    area: int
    id: str
    status: int
    pgen: float
    pmin: float
    pmax: float

    @property
    def key(self) -> tuple[int, str]:
        return (self.bus, self.id)


def read_gens(results_dir: Path) -> list[Unit]:
    """GenData.csv of a basecase run -> units (bus kV from BusData)."""
    from tara_automation.footprint import _table_rows
    results_dir = Path(results_dir)
    _, brows = _table_rows(results_dir / "BusData.csv", "Bus#")
    kv = {int(r[0]): float(r[2]) for r in brows}
    h, rows = _table_rows(results_dir / "GenData.csv", "Bus#")
    ix = {k: h.index(k) for k in ("Bus Name", "Area", "ID", "Sta", "PGen", "Pmin", "Pmax")}
    out = []
    for r in rows:
        b = int(r[0])
        out.append(Unit(b, r[ix["Bus Name"]], kv.get(b, 0.0), int(r[ix["Area"]]), r[ix["ID"]].strip(),
                        int(float(r[ix["Sta"]] or 0)), float(r[ix["PGen"]] or 0),
                        float(r[ix["Pmin"]] or 0), float(r[ix["Pmax"]] or 0)))
    return out


def radial_behind(adjacency: dict[int, set[int]], poi: int, max_size: int = 120) -> set[int]:
    """Buses that lose their connection to the grid when `poi` is removed
    (the radial plant side of a POI): neighbour components of <= max_size buses."""
    out: set[int] = set()
    for start in adjacency.get(poi, ()):
        comp, stack = {start}, [start]
        while stack and len(comp) <= max_size:
            n = stack.pop()
            for m in adjacency.get(n, ()):
                if m != poi and m not in comp:
                    comp.add(m)
                    stack.append(m)
        if len(comp) <= max_size:
            out |= comp
    return out


def qnum_units(units: list[Unit], q: str, areas: list[int]) -> list[Unit]:
    """Units whose bus name starts with this queue number (Q230_SPV1, Q311 WND)."""
    n = int(q[1:])
    pat = re.compile(rf"^Q0*{n}(?!\d)", re.I)
    return [u for u in units if u.area in areas and pat.match(u.name)]


# ------------------------------------------------------------------- reports --
_MW = r"([+\-/\d,.\s]*(?:MW\s*/\s*[+\-]?[\d,.]+)?)"
_RPT = re.compile(r"APS\s+(Q\d+)(?:/SP\d+)?\s*:\s*" + _MW + r"\s*MW\s+of\s+([A-Za-z ]+?)\s+at\s+([^|(\[\n]+)"
                  r"([^|\n]*)", re.I)


def parse_mw(expr: str) -> tuple[float, float] | None:
    s = expr.replace("MW", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    if s.startswith("+/-"):
        v = float(s[3:])
        return v, -v
    parts = s.split("/")
    try:
        a = float(parts[0].lstrip("+"))
        b = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
    except ValueError:
        return None
    return a, b


def parse_report_dispatch(markdown: str) -> dict[str, dict]:
    """Queue-ahead lines of an APS cluster report -> {Q: {pmax, pmin, tech, text, service}}.
    e.g. 'APS Q275: +1,000/-500 MW of Hybrid at Cholla 500 kV (1000 MW SPV, 500 MW BESS)'."""
    out: dict[str, dict] = {}
    for m in _RPT.finditer(markdown or ""):
        q, mw, tech, at, tail = m.groups()
        pm = parse_mw(mw)
        if pm is None or q in out:
            continue
        svc = re.search(r"as\s+(NRIS|ERIS)", at + tail, re.I)
        at = re.sub(r"\s+as\s+(NRIS|ERIS).*$", "", at, flags=re.I)
        out[q] = {"pmax": pm[0], "pmin": pm[1], "tech": tech.strip(), "at": at.strip(),
                  "text": re.sub(r"\s+", " ", m.group(0)).strip(),
                  "service": svc.group(1).upper() if svc else "",
                  "no_grid_charge": bool(re.search(r"no\s+grid[- ]charg", tail, re.I))}
    return out


def _report_markdown(qd: QueueData, report_file: str) -> str:
    p = qd.raw / f"{report_file}.json"
    if not p.exists():
        return ""
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("markdown", "")
    except (OSError, json.JSONDecodeError):
        return ""


# ------------------------------------------------------------------ projects --
@dataclass
class Project:
    q: str
    cycle: str
    mw: float                      # active queue Electrical Output MW
    service: str                   # NRIS | ERIS (active queue)
    service_report: str            # latest report's service, if any
    tech: str
    status: str
    poi_text: str
    poi_report: str
    report_file: str = ""
    rpt: dict = field(default_factory=dict)        # parse_report_dispatch row
    poi_bus: int | None = None                     # confirmed (poi_map)
    poi_bus_name: str = ""
    line_tap: bool = False
    candidates: list = field(default_factory=list)  # [(bus, name, kv, score)]
    modelled: list = field(default_factory=list)    # [Unit]
    modelled_poi: list = field(default_factory=list)  # [(bus, name)] the units sit radially behind
    radial_evidence: list = field(default_factory=list)  # [(Unit, poi bus)] non-Q-named units
    action: str = ""
    reason: str = ""
    notes: list = field(default_factory=list)

    @property
    def storage(self) -> bool:
        return "storage" in self.tech.lower() or "bess" in self.tech.lower()

    @property
    def nris(self) -> bool:
        return self.service == "NRIS"


def load_projects(qd: QueueData, area_key: str, through: str) -> list[Project]:
    """Active requests of one study area, cycles Serial..through (no surplus,
    no in-service). Withdrawn requests are not in queue_by_study_area.csv."""
    if through not in CYCLES:
        raise ValueError(f"through must be one of {CYCLES}")
    area_name = AREAS[area_key][0]
    keep = CYCLES[: CYCLES.index(through) + 1]
    gens = {}
    for r in _csv(qd.export / "generators.csv"):
        if r["area_key"] == area_key and r["is_latest_for_cycle"] == "True":
            gens[r["q"]] = r          # later rows (later cycles) win
    rpt: dict[str, dict] = {}
    for f in sorted({g["report_file"] for g in _csv(qd.export / "generators.csv")
                     if g["area_key"] == area_key and g["stage"] == "cluster"
                     and g["is_latest_for_cycle"] == "True" and g["cycle"] in keep},
                    key=lambda f: _report_date(qd, f)):
        rpt.update(parse_report_dispatch(_report_markdown(qd, f)))   # newest last wins
    out = []
    for r in _csv(qd.queue_by_area):
        if r["Study Area"] != area_name:
            continue
        cyc = PROCESS_TO_CYCLE.get(r["Process"])
        if cyc is None or cyc not in keep:
            continue
        q = r["FERC Q Number"]
        g = gens.get(q, {})
        typ = r["Interconnection Request Type"]
        out.append(Project(
            q=q, cycle=cyc, mw=_f(r["Electrical Output MW"]),
            service="NRIS" if "network resource" in typ.lower() else "ERIS",
            service_report=g.get("service", ""), tech=r["Energy Source Type"],
            status=r["Current Status"], poi_text=r["Requested POI"],
            poi_report=g.get("poi", ""), report_file=g.get("report_file", ""),
            rpt=rpt.get(q, {})))
    out.sort(key=lambda p: (CYCLES.index(p.cycle), int(p.q[1:])))
    return out


def _report_date(qd: QueueData, f: str) -> str:
    for r in _csv(qd.export / "generators.csv"):
        if r["report_file"] == f:
            return r["report_date"]
    return ""


def classify(projects: list[Project], units: list[Unit], adjacency: dict[int, set[int]],
             buses: dict, poi_map: dict[str, dict], model_areas: list[int],
             existing: dict[str, list[tuple[int, str]]] | None = None,
             include: set[str] | None = None, exclude: set[str] | None = None) -> list[Project]:
    """Fill POI / modelled / action for each project (in place, also returned).

    buses: {bus: library.Bus} from the chosen case (for candidates)."""
    existing, include, exclude = existing or {}, include or set(), exclude or set()
    by_key = {u.key: u for u in units}
    claimed = {u.key for p in projects for u in qnum_units(units, p.q, model_areas)}
    for p in projects:
        p.line_tap = library.is_line_tap(p.poi_text) or library.is_line_tap(p.poi_report)
        hit = poi_map.get(library.poi_key(p.poi_text)) or (
            poi_map.get(library.poi_key(p.poi_report)) if p.poi_report else None)
        if hit and hit.get("bus"):
            p.poi_bus, p.poi_bus_name = int(hit["bus"]), hit.get("bus_name", "")
        for text in filter(None, (p.poi_report, p.poi_text)):
            for b, s in library.candidates(text, library.poi_kv(text), buses):
                if b.number not in [c[0] for c in p.candidates]:
                    p.candidates.append((b.number, b.name, b.kv, s))
        p.modelled = qnum_units(units, p.q, model_areas)
        if p.q in existing:
            p.modelled = [by_key[k] for k in existing[p.q] if k in by_key]
            missing = [k for k in existing[p.q] if k not in by_key]
            if missing:
                p.notes.append(f"--existing units not in the case: {missing}")
        for cbus, cname, *_ in p.candidates[:2]:
            behind = radial_behind(adjacency, cbus)
            if p.modelled and any(u.bus in behind for u in p.modelled):
                p.modelled_poi.append((cbus, cname))
            for u in units:
                if u.bus in behind and u.key not in claimed and u.area in model_areas:
                    p.radial_evidence.append((u, cbus))
        # ---- action
        if p.q in exclude:
            p.action, p.reason = "excluded", "excluded by --exclude"
        elif not p.nris and p.q not in include:
            p.action, p.reason = "excluded", f"ERIS ({_svc_note(p)})"
        elif p.modelled:
            p.action = "already modelled"
            p.reason = ("units named by --existing" if p.q in existing
                        else "case units carry the queue number in their bus name")
        elif p.line_tap and p.poi_bus:
            p.action, p.reason = "add", ("line-tap POI connected at the bus mapped in poi_map.csv "
                                         "(approximation: no new tap bus)")
        elif p.line_tap:
            p.action = "blocked"
            p.reason = "line-tap POI: needs a new bus on the change workbook (Line_Taps) from the report one-line"
        elif p.poi_bus:
            p.action, p.reason = "add", "POI bus confirmed in poi_map.csv"
        else:
            p.action = "blocked"
            p.reason = "POI bus not confirmed: fill use_bus in library/poi_review.csv, then aps_queue.py accept-poi"
        if p.service_report and p.service_report != p.service:
            p.notes.append(f"service: active queue {p.service}, latest report {p.service_report}")
        if p.rpt.get("no_grid_charge"):
            p.notes.append("report: no grid charging")
    return projects


def _svc_note(p: Project) -> str:
    return "active queue" + (f"; report says {p.service_report}" if p.service_report else "")


def _f(s) -> float:
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return 0.0


def _csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------- new generators -----
def gen_id(q: str, used: set[str]) -> str:
    """Two-character machine ID from the queue number, unique at its bus."""
    cand = [q[-2:], "Q" + q[-1], *[a + b for a in "ABCDEFGHJKLMNPRSTUVWXYZ" for b in "123456789"]]
    for c in cand:
        if c not in used:
            used.add(c)
            return c
    raise RuntimeError("no free machine ID")


def new_units(projects: list[Project], units: list[Unit]) -> list[dict]:
    """New_Generators rows for every 'add' project: one unit at the confirmed
    POI bus, Pmax = NRIS MW, Pmin = the report's charging MW for storage
    (0 otherwise), Pgen 0 so the case solves unchanged; TVA GD dispatches
    study units itself."""
    used: dict[int, set[str]] = {}
    for u in units:
        used.setdefault(u.bus, set()).add(u.id)
    rows = []
    for p in projects:
        if p.action != "add":
            continue
        pmax = p.mw
        pmin = 0.0
        if p.storage and p.rpt and p.rpt.get("pmin", 0) < 0 and not p.rpt.get("no_grid_charge"):
            pmin = p.rpt["pmin"]
        gid = gen_id(p.q, used.setdefault(p.poi_bus, set()))
        # Offline at Pgen = Pmax, as SRP's working TVA GD cases hold their
        # queue units (runs 117-120: online study units at Pgen 0 were never
        # treated as queue projects).
        rows.append({"Scenarios": "ALL", "BusNum": p.poi_bus, "GenID": gid, "Status": 0,
                     "GenMW": pmax, "GenMWMax": pmax, "GenMWMin": pmin,
                     "Comment": f"{p.q} {p.cycle} NRIS {p.mw:g} MW {p.tech}; at POI bus "
                                f"{p.poi_bus} (no gen-tie modelled); Pmin "
                                f"{'from report charging' if pmin else '0'}"})
    return rows


def write_change_workbook(path: Path, rows: list[dict], scenario_id: str, name: str,
                          description: str, output_name: str,
                          unit_changes: list[dict] | None = None) -> Path:
    from tara_automation.casechange.workbook import append_rows, write_template
    path = Path(path)
    write_template(path)
    # remove the template's grey example rows (row 2 of each sheet)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    for ws in wb.worksheets:
        if ws.title != "README" and ws.max_row >= 2:
            ws.delete_rows(2, ws.max_row - 1)
    wb.save(path)
    append_rows(path, "Scenarios", [{
        "ScenarioID": scenario_id, "Name": name, "Description": description,
        "OutputFormat": "EPC", "OutputVersion": 21, "OutputName": output_name,
        "Solve": "Y", "Enabled": "Y",
        "Comment": "generated by scripts/gd_cluster_study.py; edit the project table, not this sheet"}])
    if rows:
        append_rows(path, "New_Generators", rows)
    if unit_changes:
        append_rows(path, "Unit_Changes", unit_changes)
    return path


def offline_existing(projects: list[Project]) -> list[dict]:
    """Unit_Changes rows taking the already-modelled study units offline, so
    every study unit enters TVA GD the same way (offline queue unit)."""
    out = []
    for p in projects:
        if p.action == "already modelled":
            for u in p.modelled:
                out.append({"Scenarios": "ALL", "Element": "GEN", "BusNum": u.bus, "ID": u.id,
                            "Status": 0, "Comment": f"{p.q}: study unit, offline for TVA GD"})
    return out


# ----------------------------------------------------------------- the .sub ---
def sink_groups(qd: QueueData, area_key: str, through: str) -> tuple[str, dict[int, float], str]:
    """(group name, {area: HS weight %}, source note) for the newest cycle <= through."""
    spec = yaml.safe_load((qd.root / "sinks" / f"{area_key}.yaml").read_text(encoding="utf-8"))
    groups = spec.get("sinks") or {}
    part = _csv(qd.root / "sinks" / "sink_participation.csv")
    tag = {"Transition": "TC"}
    for cyc in reversed(CYCLES[: CYCLES.index(through) + 1]):
        name = f"{area_key.upper()}_{tag.get(cyc, cyc)}_SINK"
        if name in groups:
            w = {int(r["area"]): _f(r["HS_pct"]) for r in part if r["group"] == name and r["HS_pct"]}
            src = next((r["report_file"] for r in part if r["group"] == name), "")
            areas = [int(a) for a in groups[name].get("areas", [])]
            if set(w) != set(areas):
                raise ValueError(f"{name}: weights for {sorted(w)} but areas {sorted(areas)}")
            return name, w, src
    raise ValueError(f"no sink group for {area_key} through {through}")


def footprint_spec(profile: dict, sink_name: str, weights: dict[int, float],
                   region_buses: list[int], study_name: str,
                   exclude_buses: list[int] | None = None) -> dict:
    """Footprint YAML (compiled by tara_automation.footprint against the case):
    the AZ study area, one sink group per report area (area 14 minus the study
    region), the study units and the screening pool."""
    sinks = {}
    for a in sorted(weights):
        sel: dict = {"areas": [a]}
        if a in profile["export_areas"] and region_buses:
            sel["exclude"] = {"near": [{"bus": b, "hops": profile["region_hops"], "via_kv_min": 69}
                                       for b in region_buses]}
            if exclude_buses:
                sel["exclude"]["buses"] = sorted(set(exclude_buses))
        sinks[f"SNK{a}"] = sel
    return {"study_area": dict(profile["study_area"]),
            "sub_areas": {"GD_SCREEN_ALL": {"areas": list(profile["screen_areas"])}},
            "sinks": sinks}


def render_gd_sub(fp, *, sink_name: str, weights: dict[int, float], study_name: str,
                  study_buses: list[int], export_name: str, export_areas: list[int],
                  gi_workbook: str, header: list[str],
                  exclude_buses: list[int] | None = None) -> str:
    """The GD .sub. Groups come from a compiled Footprint (exact bus sets).

    exclude_buses: generator buses kept out of the export scaling (generic
    placeholder units such as PV FUT / BESFUT / WNDFUT that would double count
    the queue projects). Written as SCALE ... EXCEPT, the form SRP's working
    decks use; run 117-119 (no EXCEPT block) found no study units."""
    L = [f"// {h}" for h in header] + [""]

    def block(name: str, body: list[str]) -> None:
        L.extend([f"SUBSYSTEM '{name}'", *body, "END", ""])

    def members(g) -> list[str]:
        return ([f"   AREA {a}" for a in g.areas_exact] if g.areas_exact
                else [f"   BUS {n}" for n in sorted(g.buses)])

    sa = fp.study_area
    L.append(f"// study area (contingencies + monitoring: AZ.con / AZ.mon): {len(sa.buses)} buses")
    block(sa.name, members(sa))
    scr = fp.get("GD_SCREEN_ALL")
    L.append("// flowgate screening pool (source = reference), as SRP's WORLD_ALL_GEN")
    block("GD_SCREEN_ALL", members(scr) + ["   ADD ALL GENERATORS INCLUDE OFFLINE"])
    L.append(f"// study units: {len(study_buses)} generator buses")
    block(study_name, [f"   BUS {b}" for b in sorted(set(study_buses))] or ["   // (none)"])
    L.append("// GD export pool: harmful-unit search space, STUDY block = the study units")
    excl = sorted(set(exclude_buses or []) - set(study_buses))
    if excl:
        L.append(f"// kept out of export scaling: {len(excl)} placeholder generator buses")
        block(f"{export_name}_EXCL", [f"   BUS {b}" for b in excl])
        scale = ["   SCALE ALL FOR EXPORT INCLUDE OFFLINE EXCEPT",
                 f"      SUBSYSTEM '{export_name}_EXCL'", "   END"]
    else:
        scale = ["   SCALE ALL FOR EXPORT INCLUDE OFFLINE"]
    block(export_name, [*(f"   AREA {a}" for a in export_areas), *scale,
                        "   STUDY", f"      SUBSYSTEM '{study_name}'", "   END"])
    L.append(f"// sink {sink_name}: report load-ratio split; each area scaled by Pgen")
    for a in sorted(weights):
        g = fp.get(f"SNK{a}")
        if g.areas_exact:
            block(f"SNK{a}", members(g) + ["   SCALE ALL GENERATION"])
        else:
            # one area minus a region: SRP's proven form (its 'Inertia' subsystem)
            # AREA n / SCALE ALL GENERATION EXCEPT / BUS ... / END
            out = sorted(fp.network.area_buses(a) - g.buses)
            block(f"SNK{a}", [f"   AREA {a}", "   SCALE ALL GENERATION EXCEPT",
                              *(f"      BUS {n}" for n in out), "   END"])
    L.append(f"SUBSYSTEM '{sink_name}'")
    L.append("   PARTICIPATE")
    L.extend(f"      SUBSYSTEM 'SNK{a}' {weights[a]:g}" for a in sorted(weights))
    L.extend(["   END", "END", ""])
    L.append(f'TVA_GI_DATA "{gi_workbook}"')
    return "\n".join(L) + "\n"


# ------------------------------------------------------------- GI workbook ----
GI_CONSTANTS = Path(__file__).parent / "commfile" / "templates" / "gd_gi_constants.json"


def gi_generator_rows(units: list[Unit], export_areas: list[int], study: list[dict],
                      queue: str) -> list[list]:
    """Generator sheet rows: every unit in the export areas (on- and offline,
    as the export subsystem says INCLUDE OFFLINE) plus study units.

    study: [{"bus", "id", "q", "poi_bus", "poi_name", "nris_mw"}]; Pmax/Pmin
    always equal the case (TARA rejects a mismatch). Column 6 carries the
    unit's share of the project NRIS MW, column 7 the case Pmax (memo reading:
    NRIS / ERIS MW; unconfirmed)."""
    sk = {(s["bus"], s["id"]): s for s in study}
    by_q: dict[str, float] = {}
    for s in study:
        u = next((u for u in units if u.key == (s["bus"], s["id"])), None)
        by_q[s["q"]] = by_q.get(s["q"], 0.0) + max(u.pmax if u else s.get("pmax", 0), 0.0)
    # Plant ID in TVA GD's own form "Q<yy><nn>" (SRP: Q2601 under queue SRP26):
    # the year digits are taken from the queue name; the APS queue number goes
    # to Plant ID2 and Bus Name. Runs 117/118 found no queue units with Plant
    # ID = the APS number. Column 13 (Bus Name) also carries the plant ID, as in
    # SRP's workbook; Plant ID2 is left empty (it drives the helper rule). The
    # APS queue number stays in gd.json / the project table. POI Bus / POI Bus
    # Name are left empty as in SRP's queue rows (filled, runs 117-129 found no
    # queue units; SRP's own inputs work on 26.02.1, run 130).
    yy = "".join(ch for ch in queue if ch.isdigit())[-2:]
    plant: dict[str, str] = {}
    for s in study:
        plant.setdefault(s["q"], f"Q{yy}{len(plant) + 1:02d}")
    rows, k = [], 0
    for u in sorted(units, key=lambda u: (u.key not in sk, u.bus, u.id)):
        if u.area not in export_areas and u.key not in sk:
            continue
        gid = int(u.id) if u.id.isdigit() else u.id
        if u.key in sk:
            s = sk[u.key]
            k += 1
            tot = by_q.get(s["q"]) or 1.0
            nris = round(min(s["nris_mw"], tot) * max(u.pmax, 0.0) / tot, 2) if u.pmax > 0 else u.pmax
            rows.append([u.bus, f"{queue}-{k:03d}", gid, u.pmax, u.pmin, nris, u.pmax, None,
                         plant[s["q"]], None, None, None, plant[s["q"]]])
        else:
            # Plant ID for non-study units: first 3 letters of the name, but never
            # starting with "Q" + digit - TVA GD reads such IDs as queue numbers
            # (Q2405 = 2024 queue), and case names like "Q236 PV" would turn
            # existing units into queue projects.
            pid = u.name[:3]
            if len(pid) > 1 and pid[0] in "Qq" and pid[1].isdigit():
                pid = "X" + pid[1:]
            rows.append([u.bus, u.name, gid, u.pmax, u.pmin, u.pmax, u.pmax, None,
                         pid, None, None, None, u.name])
    return rows


def write_gi_workbook(path: Path, generator_rows: list[list], loads: list[list] | None = None) -> Path:
    from openpyxl import Workbook
    c = json.loads(GI_CONSTANTS.read_text(encoding="utf-8"))
    wb = Workbook()
    ws = wb.active
    ws.title = "Generator"
    ws.append(c["headers"]["Generator"])
    for r in generator_rows:
        ws.append(r)
    ws = wb.create_sheet("Load")
    ws.append(c["headers"]["Load"])
    for r in loads or []:
        ws.append(r)
    ws = wb.create_sheet("TSR")
    ws.append(c["headers"]["TSR"])
    for name, rows in c["sheets"].items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return Path(path)


# --------------------------------------------------------------- reporting ----
def project_table_md(projects: list[Project]) -> str:
    head = ("| Q | Cycle | MW | Service (queue / report) | Tech | Report dispatch | POI (queue text) | "
            "POI bus | In case? | Action | Why / notes |\n|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [head]
    for p in projects:
        rd = (f"{p.rpt['pmax']:g}/{p.rpt['pmin']:g}" if p.rpt else "")
        if p.poi_bus:
            poi = f"{p.poi_bus} {p.poi_bus_name} (confirmed)"
        elif p.modelled_poi:
            poi = "; ".join(f"{b} {n} (case model)" for b, n in p.modelled_poi)
        elif p.candidates:
            poi = "needs accept-poi; candidates " + ", ".join(f"{b} {n} {kv:g} kV ({s})"
                                                               for b, n, kv, s in p.candidates[:3])
        else:
            poi = "needs accept-poi; no candidate bus by name"
        inc = ""
        if p.modelled:
            inc = "yes: " + ", ".join(f"{u.name.strip()}/{u.id} {u.pmax:g}/{u.pmin:g}" for u in p.modelled)
        elif p.radial_evidence:
            ev = {}
            for u, b in p.radial_evidence:
                ev.setdefault(b, []).append(u)
            inc = "not by name; units radially behind " + "; ".join(
                f"{b}: " + ", ".join(f"{u.name.strip()}/{u.id} {u.pmax:g}" for u in us[:6])
                for b, us in ev.items())
        else:
            inc = "no"
        why = p.reason + ("; " + "; ".join(p.notes) if p.notes else "")
        svc = p.service + (f" / {p.service_report}" if p.service_report else "")
        lines.append(f"| {p.q} | {p.cycle} | {p.mw:g} | {svc} | {p.tech} | {rd} | {p.poi_text} | "
                     f"{poi} | {inc} | **{p.action}** | {why} |")
    return "\n".join(lines) + "\n"


def summary(projects: list[Project]) -> dict:
    s: dict = {}
    for p in projects:
        d = s.setdefault(p.action, {"n": 0, "mw": 0.0, "q": []})
        d["n"] += 1
        d["mw"] += p.mw
        d["q"].append(p.q)
    return s
