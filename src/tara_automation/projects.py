"""Project library: one spreadsheet of queue / IRP / TYP / own projects.

    projects.xlsx  ->  select a subset  ->  ChangeSet  ->  INCH (task 11 code)

A *project* is one row: a generator, storage plant, load or transmission
project, wherever it came from. This module never writes INCH itself. It
turns the selected rows into the same `ChangeSet` the case-change workbook
produces, so the INCH writer, the run manager stage and every check that
already exists apply unchanged — and the generated changes can be written out
as an ordinary change workbook that an engineer can open and audit.

How each kind becomes change rows:

  gen / storage   New_Generators row at `poi_bus` (Pmax = mw)
  load            New_Loads row at `poi_bus` (P = mw)
  transmission    the rows of scenario `changes_ref` in the change workbook

Any row may carry `changes_ref`. For a generator that is its interconnection
facilities — the new POI bus, GSU and tie — built by hand on the change
workbook, with `poi_bus` naming the new bus as `@BusName`. The library row is
always the generator itself, so a referenced scenario that also adds a
generator at the POI is an error (it would double the MW).

Hard rules, all reported with sheet / row / column like the change workbook:

  * a POI bus not found in the case is an error that lists the closest name /
    kV matches — the bus is never guessed;
  * a machine or load ID that already exists at the bus is an error; a blank
    ID is assigned the lowest free one, checked against the case;
  * bus numbers auto-assigned in the change workbook are frozen when copied,
    so two projects whose facilities claim the same number collide loudly
    instead of being renumbered.
"""

from __future__ import annotations

import copy
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from tara_automation.casechange.model import (
    ALL_SCENARIOS, ChangeSet, Problem, Scenario, WorkbookError, _blank,
    _sheet_rows, _validate, new_bus_number,
)
from tara_automation.casechange.schema import (
    BUS_REF_PREFIX, CHANGE_SHEETS, Column, Sheet,
)

KINDS = ("gen", "storage", "load", "transmission")
SOURCES = ("queue", "IRP", "TYP", "own")
DEFAULT_SCENARIO = "PROJ"


# ----------------------------------------------------------------- schema ----
PROJECTS = Sheet(
    "Projects",
    "One row per project, whatever its source.",
    (
        Column("id", required=True, width=16,
               help="Unique key, e.g. the queue number Q1234 or TYP_HASSAYAMPA. "
                    "Scenario selections and reports refer to projects by it."),
        Column("name", required=True, width=28),
        Column("source", kind="choice", required=True, choices=SOURCES,
               width=10, help="queue = interconnection queue, IRP = resource "
                              "plan, TYP = utility ten-year plan, own = ours."),
        Column("kind", kind="choice", required=True, choices=KINDS, width=13,
               help="gen / storage -> a new machine, load -> a new load, "
                    "transmission -> the rows of changes_ref."),
        Column("poi_bus", kind="bus", width=12,
               help="Existing bus number, or @BusName for a bus that "
                    "changes_ref creates. Required for gen, storage and load."),
        Column("poi_name", width=16,
               help="POI name as the source data spells it. Not used to find "
                    "the bus — only to list close matches when poi_bus is "
                    "missing or wrong, and to flag a number/name mismatch."),
        Column("poi_kv", kind="float", width=9,
               help="POI voltage, kV. Same use as poi_name."),
        Column("mw", kind="float", width=10,
               help="Nameplate MW: Pmax for gen / storage, P for load."),
        Column("pgen", kind="float", width=10,
               help="Dispatch MW. Blank = mw (full output). The balancing "
                    "policy (task 19) decides what this MW displaces. Storage "
                    "may be negative (charging)."),
        Column("mvar", kind="float", width=9,
               help="Load only: Q, MVAr. Blank = 0."),
        Column("fuel", width=12, help="solar, wind, gas, battery, ..."),
        Column("status", width=12,
               help="Queue / plan status as the source states it, e.g. SIS, "
                    "FS, GIA, approved. Used by selections and attrition."),
        Column("cod", width=12,
               help="Commercial operation date: a year (2030) or a date. "
                    "Selections compare the year."),
        Column("cluster", width=10, help="Queue cluster, e.g. 2023."),
        Column("utility", width=12, help="Transmission provider."),
        Column("queue_pos", width=11, help="Queue position, as published."),
        Column("p_complete", kind="float", width=11,
               help="Completion probability 0-1. Blank = set by the attrition "
                    "rule of the scenario that uses it."),
        Column("changes_ref", width=14,
               help="ScenarioID in the study's change workbook whose rows "
                    "belong to this project: the network for a transmission "
                    "project, the POI facilities for a generator."),
        Column("gen_id", width=8,
               help="Machine / load ID. Blank = lowest free ID at the bus."),
        Column("enabled", kind="flag", default="Y", width=9,
               help="N = never selected. Parks a project without deleting it."),
        Column("comment", width=34),
    ),
    example=("Q1234", "Example Solar", "queue", "gen", 14005, "WESTWING",
             500.0, 300.0, "", "", "solar", "FS", 2029, "2023", "APS", "1234",
             "", "", "", "Y", "EXAMPLE: delete this row"),
    notes=(
        "One row per project. A selection such as {source: queue, status: "
        "[FS, GIA], cod_by: 2030} picks the projects a scenario adds.",
        "A POI that needs a new bus or a line tap: build it on the change "
        "workbook (New_Buses / Line_Taps / New_Transformers ...) as its own "
        "scenario, put that ScenarioID in changes_ref and @BusName in poi_bus.",
    ),
)

SELECT_KEYS = ("ids", "exclude", "source", "kind", "status", "cluster",
               "utility", "fuel", "cod_by", "cod_from")


# ------------------------------------------------------------------ model ----
@dataclass
class Project:
    row: int
    id: str
    name: str
    source: str
    kind: str
    poi_bus: int | str | None = None
    poi_name: str = ""
    poi_kv: float | None = None
    mw: float | None = None
    pgen: float | None = None
    mvar: float | None = None
    fuel: str = ""
    status: str = ""
    cod: int | None = None
    cluster: str = ""
    utility: str = ""
    queue_pos: str = ""
    p_complete: float | None = None
    changes_ref: str = ""
    gen_id: str = ""
    enabled: bool = True
    comment: str = ""

    @property
    def dispatch(self) -> float | None:
        return self.mw if self.pgen is None else self.pgen


@dataclass
class Library:
    projects: list[Project]
    problems: list[Problem] = field(default_factory=list)
    source: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def by_id(self) -> dict[str, Project]:
        return {p.id: p for p in self.projects}


_SOURCE_CASE = {s.upper(): s for s in SOURCES}


def _year(value) -> int | None:
    if _blank(value):
        return None
    m = re.search(r"(19|20)\d{2}", str(value))
    if not m:
        raise ValueError(f"no year in {value!r}; write 2030 or a date")
    return int(m.group(0))


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        v = int(v)                      # Excel turns cluster 2023 into 2023.0
    return str(v).strip()


def read_library(path: Path) -> Library:
    """Parse projects.xlsx. Bad data is collected on Library.problems, never
    raised, so the engineer sees every problem in one pass."""
    from openpyxl import load_workbook

    path = Path(path)
    if not path.exists():
        raise WorkbookError(f"Project library not found: {path}")
    wb = load_workbook(path, data_only=True)
    if PROJECTS.name not in wb.sheetnames:
        raise WorkbookError(
            f"{path.name} has no '{PROJECTS.name}' sheet — generate a fresh "
            f"library with: python scripts/projects.py template")
    problems: list[Problem] = []
    rows = _sheet_rows(wb[PROJECTS.name], PROJECTS, problems)
    lib = Library([], problems, path)
    for r in rows:
        try:
            cod = _year(r.get("cod"))
        except ValueError as e:
            problems.append(Problem(PROJECTS.name, r["_row"], "cod", str(e)))
            cod = None
        lib.projects.append(Project(
            row=r["_row"],
            id=_text(r.get("id")),
            name=_text(r.get("name")),
            source=_SOURCE_CASE.get(str(r.get("source") or "").upper(), ""),
            kind=str(r.get("kind") or "").lower(),
            poi_bus=r.get("poi_bus"),
            poi_name=_text(r.get("poi_name")),
            poi_kv=r.get("poi_kv"),
            mw=r.get("mw"),
            pgen=r.get("pgen"),
            mvar=r.get("mvar"),
            fuel=_text(r.get("fuel")),
            status=_text(r.get("status")),
            cod=cod,
            cluster=_text(r.get("cluster")),
            utility=_text(r.get("utility")),
            queue_pos=_text(r.get("queue_pos")),
            p_complete=r.get("p_complete"),
            changes_ref=_text(r.get("changes_ref")),
            gen_id=_text(r.get("gen_id")),
            enabled=bool(r.get("enabled", True)),
            comment=_text(r.get("comment")),
        ))
    _validate_library(lib)
    return lib


def _validate_library(lib: Library) -> None:
    P = lib.problems
    seen: set[str] = set()

    def bad(p: Project, col: str, msg: str) -> None:
        P.append(Problem(PROJECTS.name, p.row, col, msg))

    for p in lib.projects:
        if p.id in seen:
            bad(p, "id", f"duplicate project id {p.id!r}")
        seen.add(p.id)
        if p.kind == "transmission":
            if not p.changes_ref:
                bad(p, "changes_ref", "a transmission project is its change "
                    "workbook rows — name their ScenarioID here")
            continue
        if not p.kind:
            continue                        # already reported as required
        if p.poi_bus is None:
            hint = (f" (source data says {p.poi_name} {p.poi_kv or ''} kV — "
                    f"look the number up; it is never guessed)"
                    if p.poi_name else "")
            bad(p, "poi_bus", f"required for kind {p.kind}{hint}")
        elif isinstance(p.poi_bus, str) and not p.changes_ref:
            bad(p, "poi_bus", f"{p.poi_bus} names a new bus, so changes_ref "
                "must point at the change-workbook scenario that creates it")
        if p.mw is None or p.mw <= 0:
            bad(p, "mw", "must be a positive MW")
            continue
        d = p.dispatch
        if p.kind in ("gen", "storage") and d > p.mw + 1e-9:
            bad(p, "pgen", f"{d} MW exceeds mw {p.mw}")
        if p.kind == "gen" and d < 0:
            bad(p, "pgen", "a generator cannot dispatch negative MW — use "
                "kind storage for a charging plant")
        if p.kind == "storage" and d < -p.mw - 1e-9:
            bad(p, "pgen", f"charging {-d} MW exceeds mw {p.mw}")
        if p.kind != "load" and p.mvar is not None:
            bad(p, "mvar", "only used for kind load")
        if p.p_complete is not None and not 0 <= p.p_complete <= 1:
            bad(p, "p_complete", f"must be 0-1 (60% is 0.6), got {p.p_complete}")


# -------------------------------------------------------------- selection ----
class SelectionError(ValueError):
    pass


def _as_set(v) -> set[str]:
    vals = v if isinstance(v, (list, tuple, set)) else [v]
    return {_text(x).upper() for x in vals}


def select(lib: Library, spec: dict | None) -> list[Project]:
    """Projects matching a selection, in library order.

    Every key narrows the set (AND); a list value matches any of its items
    (OR); text compares case-insensitively. `ids` picks projects by id (an
    unknown id is an error), `exclude` drops ids, `cod_by` / `cod_from` bound
    the COD year — a project with no COD fails either bound. Disabled
    projects are never selected, even by id.
    """
    spec = dict(spec or {})
    unknown = set(spec) - set(SELECT_KEYS)
    if unknown:
        raise SelectionError(f"unknown selection key(s) {sorted(unknown)}; "
                             f"allowed: {', '.join(SELECT_KEYS)}")
    by_id = lib.by_id()
    if "ids" in spec:
        missing = [i for i in _as_list(spec["ids"]) if i not in by_id]
        if missing:
            raise SelectionError(f"ids not in the library: {missing}")
    for i in _as_list(spec.get("exclude", [])):
        if i not in by_id:
            raise SelectionError(f"exclude id {i!r} is not in the library")

    out = []
    for p in lib.projects:
        if not p.enabled:
            continue
        if "ids" in spec and p.id not in _as_list(spec["ids"]):
            continue
        if p.id in _as_list(spec.get("exclude", [])):
            continue
        if not all(_text(getattr(p, k)).upper() in _as_set(spec[k])
                   for k in ("source", "kind", "status", "cluster", "utility",
                             "fuel") if k in spec):
            continue
        if "cod_by" in spec and (p.cod is None or p.cod > int(spec["cod_by"])):
            continue
        if "cod_from" in spec and (p.cod is None
                                   or p.cod < int(spec["cod_from"])):
            continue
        out.append(p)
    return out


def _as_list(v) -> list[str]:
    vals = v if isinstance(v, (list, tuple, set)) else [v]
    return [_text(x) for x in vals]


def parse_selection(text: str | None) -> dict:
    """`{source: queue, status: [FS, GIA]}` (YAML flow) or `ids=Q1,Q2` form."""
    if not text:
        return {}
    import yaml
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SelectionError(f"selection is not valid YAML: {e}") from e
    if not isinstance(spec, dict):
        raise SelectionError("selection must be a mapping, e.g. "
                             "'{source: queue, cod_by: 2030}'")
    return spec


# ------------------------------------------------------------- case index ----
@dataclass
class CaseIndex:
    """What the library needs to know about the case it modifies: bus names
    and kV (to check POIs) and the machine / load IDs already in use."""

    buses: dict[int, tuple[str, float]]
    gen_ids: dict[int, set[str]]
    load_ids: dict[int, set[str]]
    source: str = ""

    @classmethod
    def from_run(cls, results_dir: Path) -> "CaseIndex":
        from tara_automation.footprint import _expect, _table_rows

        d = Path(results_dir)
        for name in ("BusData.csv", "GenData.csv", "LoadData.csv"):
            if not (d / name).exists():
                raise FileNotFoundError(
                    f"{d / name} not found — the project library checks POIs "
                    f"against a basecase run's results")
        h, rows = _table_rows(d / "BusData.csv", "Bus#")
        _expect(d / "BusData.csv", h, {0: "Bus#", 1: "Bus Name", 2: "Volt"})
        buses = {int(r[0]): (r[1], float(r[2])) for r in rows}
        h, rows = _table_rows(d / "GenData.csv", "Bus#")
        _expect(d / "GenData.csv", h, {0: "Bus#", 6: "ID"})
        gens: dict[int, set[str]] = {}
        for r in rows:
            gens.setdefault(int(r[0]), set()).add(r[6].strip().upper())
        h, rows = _table_rows(d / "LoadData.csv", "Bus#")
        _expect(d / "LoadData.csv", h, {0: "Bus#", 5: "ID"})
        loads: dict[int, set[str]] = {}
        for r in rows:
            loads.setdefault(int(r[0]), set()).add(r[5].strip().upper())
        return cls(buses, gens, loads, str(d))

    def closest(self, name: str = "", kv: float | None = None,
                number: int | None = None, n: int = 5) -> list[str]:
        """The n buses most like (name, kV) — or, with no name, nearest by
        number — formatted for an error message."""
        def score(item):
            num, (bname, bkv) = item
            s = (difflib.SequenceMatcher(None, name.upper(), bname.upper())
                 .ratio() if name else 0.0)
            if kv and abs(bkv - kv) < 0.5:
                s += 0.5
            if not name and number is not None:
                s -= abs(num - number) / 1e6
            return s
        best = sorted(self.buses.items(), key=score, reverse=True)[:n]
        return [f"{num} {bname} {bkv:g} kV" for num, (bname, bkv) in best]


# ------------------------------------------------------------- changeset -----
def _blank_row(sheet_name: str) -> dict:
    """Every column of a change sheet at its default, as model.py reads it."""
    from tara_automation.casechange.schema import sheet
    return {c.name: c.default for c in sheet(sheet_name).columns}


def _next_id(taken: set[str]) -> str:
    i = 1
    while str(i) in taken:
        i += 1
    return str(i)


def to_changeset(projects: list[Project], *,
                 changes: ChangeSet | None = None,
                 case: CaseIndex | None = None,
                 scenario_id: str = DEFAULT_SCENARIO,
                 name: str = "", description: str = "",
                 base_case: str = "", new_bus_start: int | None = None,
                 source: Path | None = None) -> ChangeSet:
    """One scenario holding every selected project, ready for write_inch.

    Problems are collected on the returned ChangeSet. Sheet names in them are
    `Projects` for library rows and the change sheet for copied rows.
    """
    # Copied bus numbers are frozen, so NewBusStart only matters for the
    # record; by default it follows the referenced scenarios.
    if new_bus_start is None:
        refs = [s.new_bus_start for s in (changes.scenarios if changes else [])
                if any(p.changes_ref == s.id for p in projects)]
        new_bus_start = min(refs) if refs else 990001
    sc = Scenario(id=scenario_id, name=name or scenario_id,
                  description=description, base_case=base_case,
                  new_bus_start=new_bus_start)
    rows: dict[str, list[dict]] = {s.name: [] for s in CHANGE_SHEETS}
    P: list[Problem] = []
    copied: set[tuple[str, int]] = set()
    new_gen_ids: dict[object, set[str]] = {}
    new_load_ids: dict[object, set[str]] = {}

    def bad(p: Project, col: str, msg: str) -> None:
        P.append(Problem(PROJECTS.name, p.row, col, f"{p.id}: {msg}"))

    ref_ids = {s.id for s in changes.scenarios} if changes else set()

    for p in projects:
        ref_names: set[str] = set()
        ref_gen_buses: set[object] = set()
        if p.changes_ref:
            if changes is None:
                bad(p, "changes_ref", f"refers to scenario {p.changes_ref!r} "
                    "but no change workbook was given (--changes)")
                continue
            if p.changes_ref not in ref_ids:
                bad(p, "changes_ref", f"scenario {p.changes_ref!r} is not on "
                    f"the Scenarios sheet of {changes.source.name if changes.source else 'the change workbook'}")
                continue
            for sh in CHANGE_SHEETS:
                for r in changes.rows_for(sh.name, p.changes_ref):
                    if sh.name in ("New_Buses", "Line_Taps"):
                        ref_names.add(str(r.get("BusName") or r.get(
                            "NewBusName") or "").strip().upper())
                    if sh.name == "New_Generators":
                        ref_gen_buses.add(_norm_bus(r.get("BusNum")))
                    key = (sh.name, r["_row"])
                    if key in copied:
                        continue
                    copied.add(key)
                    rows[sh.name].append(_copy_row(sh.name, r, p.changes_ref,
                                                   scenario_id))

        if p.kind == "transmission":
            continue

        bus = p.poi_bus
        if isinstance(bus, str):
            if bus[1:].strip().upper() not in ref_names:
                bad(p, "poi_bus", f"{bus} is not a BusName on New_Buses / "
                    f"Line_Taps of scenario {p.changes_ref!r}")
                continue
        elif case is not None:
            if bus not in case.buses:
                bad(p, "poi_bus", f"bus {bus} is not in the case. Closest: "
                    + "; ".join(case.closest(p.poi_name, p.poi_kv, bus)))
                continue
            bname, bkv = case.buses[bus]
            if p.poi_kv and abs(bkv - p.poi_kv) > 0.5:
                bad(p, "poi_kv", f"bus {bus} {bname} is {bkv:g} kV, the "
                    f"library says {p.poi_kv:g} kV — check the POI")
                continue

        if p.kind in ("gen", "storage") and _norm_bus(bus) in ref_gen_buses:
            bad(p, "changes_ref", f"scenario {p.changes_ref!r} already adds a "
                f"generator at {bus}. The library row is the generator; keep "
                f"only the facilities on the change workbook, or the MW would "
                f"be added twice")
            continue

        is_load = p.kind == "load"
        existing = ((case.load_ids if is_load else case.gen_ids).get(bus, set())
                    if case is not None and isinstance(bus, int) else set())
        assigned = (new_load_ids if is_load else new_gen_ids).setdefault(
            _norm_bus(bus), set())
        uid = p.gen_id.upper()
        if uid:
            if uid in existing or uid in assigned:
                bad(p, "gen_id", f"ID {p.gen_id} is already used at bus {bus}"
                    + (" in the case" if uid in existing else
                       " by another project"))
                continue
        else:
            uid = _next_id(existing | assigned)
        assigned.add(uid)

        note = f"{p.id} {p.name} [{p.source} {p.kind}" + (
            f" {p.fuel}" if p.fuel else "") + "]"
        if is_load:
            r = _blank_row("New_Loads")
            r.update({"Scenarios": [scenario_id], "BusNum": bus,
                      "LoadID": uid, "LoadP0": p.mw,
                      "LoadQ0": p.mvar if p.mvar is not None else 0.0,
                      "Comment": note})
            rows["New_Loads"].append(r)
        else:
            r = _blank_row("New_Generators")
            r.update({"Scenarios": [scenario_id], "BusNum": bus,
                      "GenID": uid, "GenMW": p.dispatch, "GenMWMax": p.mw,
                      "Comment": note})
            if p.kind == "storage":
                r["GenMWMin"] = -p.mw
            rows["New_Generators"].append(r)
        r["_row"] = p.row
        r["_project"] = p.id

    cs = ChangeSet(scenarios=[sc], rows=rows, problems=P, source=source)
    _validate(cs)
    return cs


def _norm_bus(v) -> object:
    return v.strip().upper() if isinstance(v, str) else v


def _copy_row(sheet_name: str, r: dict, ref: str, sid: str) -> dict:
    """A change-workbook row re-tagged to the library scenario, with any
    auto-assigned bus number frozen to what it was in its own scenario."""
    out = {k: copy.deepcopy(v) for k, v in r.items()
           if not k.startswith("_")}
    out["_row"] = r["_row"]
    out["_from"] = ref
    out["Scenarios"] = [sid]
    num = new_bus_number(r, ref)
    if sheet_name == "New_Buses":
        out["BusNum"] = num
    elif sheet_name == "Line_Taps":
        out["NewBusNum"] = num
    return out


# --------------------------------------------------------------- outputs -----
def write_changes_workbook(cs: ChangeSet, path: Path) -> Path:
    """The generated ChangeSet as an ordinary change workbook, so the
    pipeline's casechange stage and an engineer read the same thing."""
    from tara_automation.casechange.workbook import append_rows, write_template

    path = Path(path)
    write_template(path)
    sc = cs.scenarios[0]
    append_rows(path, "Scenarios", [{
        "ScenarioID": sc.id, "Name": sc.name, "Description": sc.description,
        "BaseCase": sc.base_case or None, "OutputFormat": sc.out_format,
        "OutputVersion": sc.out_version, "OutputName": sc.out_name or None,
        "Solve": "Y" if sc.solve else "N", "NewBusStart": sc.new_bus_start,
        "Enabled": "Y"}])
    for sh in CHANGE_SHEETS:
        out = []
        for r in cs.rows.get(sh.name, []):
            row = {k: v for k, v in r.items() if not k.startswith("_")}
            tags = row.get("Scenarios")
            row["Scenarios"] = (ALL_SCENARIOS if tags == ALL_SCENARIOS
                                else ",".join(tags))
            out.append(row)
        if out:
            append_rows(path, sh.name, out)
    return path


def summary(projects: list[Project]) -> str:
    """Markdown: what was selected, MW by source and kind."""
    lines = ["| id | name | source | kind | POI | MW | dispatch | status | COD |",
             "|---|---|---|---|---|---:|---:|---|---|"]
    for p in projects:
        lines.append(
            f"| {p.id} | {p.name} | {p.source} | {p.kind} | "
            f"{p.poi_bus if p.poi_bus is not None else ''} | "
            f"{'' if p.mw is None else f'{p.mw:g}'} | "
            f"{'' if p.dispatch is None else f'{p.dispatch:g}'} | "
            f"{p.status} | {p.cod or ''} |")
    tot: dict[tuple[str, str], float] = {}
    for p in projects:
        if p.kind != "transmission":
            k = (p.source, p.kind)
            tot[k] = tot.get(k, 0.0) + (p.dispatch or 0.0)
    if tot:
        lines += ["", "| source | kind | dispatch MW |", "|---|---|---:|"]
        lines += [f"| {s} | {k} | {mw:g} |" for (s, k), mw in sorted(tot.items())]
    return "\n".join(lines)


def write_template(path: Path) -> Path:
    """A blank, documented projects.xlsx."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment

    from tara_automation.casechange import workbook as wbk

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    wbk._write_sheet(wb, PROJECTS)
    ws = wb.create_sheet("README", 0)
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 11
    ws.column_dimensions["C"].width = 100
    ws.cell(row=1, column=1, value="Project library").font = wbk.TITLE
    ws.cell(row=2, column=1, value=(
        "Every queue, IRP, TYP and own project, one row each. "
        "scripts/projects.py selects a subset and turns it into a change "
        "workbook / INCH file for TARA.")).font = wbk.SUB
    r = 4
    for i, head in enumerate(("Column", "Required", "What it means"), start=1):
        c = ws.cell(row=r, column=i, value=head)
        c.fill, c.font = wbk.HDR_FILL, wbk.HDR_FONT
    for col in PROJECTS.columns:
        r += 1
        ws.cell(row=r, column=1, value=col.name).font = wbk.BASE
        ws.cell(row=r, column=2, value="yes" if col.required else "").font = \
            wbk.BASE
        text = col.help
        if col.choices:
            text = "One of: " + ", ".join(col.choices) + (
                ". " + text if text else "")
        hc = ws.cell(row=r, column=3, value=text)
        hc.font = wbk.BASE
        hc.alignment = Alignment(wrap_text=True, vertical="top")
    r += 2
    for note in PROJECTS.notes:
        ws.cell(row=r, column=1, value="Note: " + note).font = wbk.SUB
        r += 1
    wb.active = 0
    wb.save(path)
    return path


def append_projects(path: Path, rows: list[dict]) -> None:
    """Append project rows (column -> value), for fixtures and examples."""
    from openpyxl import load_workbook

    wb = load_workbook(path)
    ws = wb[PROJECTS.name]
    order = [c.name for c in PROJECTS.columns]
    for row in rows:
        unknown = set(row) - set(order)
        if unknown:
            raise KeyError(f"Projects: no such column(s): {sorted(unknown)}")
        ws.append([row.get(n) for n in order])
    wb.save(path)
