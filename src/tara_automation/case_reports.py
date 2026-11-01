"""A solved case, read from TARA's own case reports (a basecase run).

Cross-case work (task 27 differences, task 28 carrying projects) reads the
tables TARA wrote when it solved the case (`runs/N/results/`), so the review
sees the case as TARA models it:

  * 3-winding transformers are TARA's three 2-winding legs to a star bus;
  * transformer R / X are per unit on the 100 MVA system base (what an INCH
    record needs: TARA ignores CZ);
  * buses carry TARA's solved type, and every element its status.

Tables used: BusData, BranchData (lines AND transformer legs), TransfmrData
(tap / regulation data, and which branch rows are transformers), GenData,
LoadData, ShuntData (fixed, bus and switched shunts, SVDs). Columns are found
by header name; a missing column is an error naming the table, so a TARA
report layout change fails loudly instead of misreading data.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

TABLES = {"bus": "BusData.csv", "branch": "BranchData.csv", "transformer": "TransfmrData.csv",
          "generator": "GenData.csv", "load": "LoadData.csv", "shunt": "ShuntData.csv"}
KINDS = ("bus", "branch", "transformer", "generator", "load", "shunt")


class ReportError(ValueError):
    pass


def _table(path: Path) -> tuple[list[str], list[list[str]]]:
    """(header, data rows): the header is the first row whose first cell is a
    column name, data rows start with a bus number."""
    if not path.exists():
        raise ReportError(f"{path.parent.parent.name}: {path.name} missing (not a full basecase run?)")
    header, rows = None, []
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        for p in csv.reader(fh):
            cells = [c.strip() for c in p]
            if not cells:
                continue
            if header is None:
                if cells[0] in ("Bus#", "Fr Bus"):
                    header = cells
                continue
            if re.fullmatch(r"\d+", cells[0]):
                # some rows stop early (empty trailing columns): pad to the header
                rows.append(cells + [""] * (len(header) - len(cells)))
    if header is None:
        raise ReportError(f"{path.name}: no header row")
    return header, rows


class _Cols:
    """Header-name lookup; `nth` picks a repeated name (Volt, Bus#)."""

    def __init__(self, path: Path, header: list[str]):
        self.path, self.header = path, header

    def __call__(self, name: str, nth: int = 0) -> int:
        hits = [i for i, h in enumerate(self.header) if h == name]
        if len(hits) <= nth:
            raise ReportError(f"{self.path.name}: column '{name}' not found; the TARA report "
                              f"layout changed, update case_reports.py")
        return hits[nth]


def _f(x: str) -> float:
    try:
        return float(str(x).rstrip("%"))
    except ValueError:
        return 0.0


def _i(x: str) -> int:
    try:
        return int(float(x))
    except ValueError:
        return 0


@dataclass
class Record:
    kind: str
    key: tuple
    f: dict = field(default_factory=dict)
    row: list[str] = field(default_factory=list)       # the report row, as written

    @property
    def buses(self) -> tuple[int, ...]:
        if self.kind == "bus":
            return (self.f["bus"],)
        if self.kind in ("branch", "transformer"):
            return (self.f["from"], self.f["to"])
        return (self.f["bus"],)


@dataclass
class Case:
    results: Path
    run: int | None
    case_name: str
    records: dict[str, dict[tuple, Record]]
    skipped: dict = field(default_factory=dict)

    def __getitem__(self, kind: str) -> dict[tuple, Record]:
        return self.records[kind]

    def bus(self, n: int) -> Record | None:
        return self.records["bus"].get((n,))

    def adjacency(self, in_service_only: bool = True) -> dict[int, set[int]]:
        adj: dict[int, set[int]] = {}
        for kind in ("branch", "transformer"):
            for r in self.records[kind].values():
                if in_service_only and r.f["st"] != 1:
                    continue
                a, b = r.buses
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        return adj


def _branch_key(a: int, b: int, ck: str, se: int) -> tuple:
    lo, hi = sorted((a, b))
    return (lo, hi, ck.upper(), se)


def read_results(results: str | Path, run: int | None = None, case_name: str = "") -> Case:
    results = Path(results)
    recs: dict[str, dict[tuple, Record]] = {k: {} for k in KINDS}

    # buses
    p = results / TABLES["bus"]
    h, rows = _table(p)
    c = _Cols(p, h)
    ix = {n: c(n) for n in ("Bus#", "Bus Name", "Volt", "Area", "Zone", "BTyp", "Ownr", "Vmag [PU]", "Vangle")}
    for r in rows:
        n = _i(r[ix["Bus#"]])
        recs["bus"][(n,)] = Record("bus", (n,), {
            "bus": n, "name": r[ix["Bus Name"]], "kv": _f(r[ix["Volt"]]), "area": _i(r[ix["Area"]]),
            "zone": _i(r[ix["Zone"]]), "type": r[ix["BTyp"]], "owner": _i(r[ix["Ownr"]]),
            # solved state, for diagrams (never compared)
            "vpu": _f(r[ix["Vmag [PU]"]]), "angle": _f(r[ix["Vangle"]])}, r)

    # transformer legs: TransfmrData says which branch rows are transformers
    p = results / TABLES["transformer"]
    h, rows = _table(p)
    c = _Cols(p, h)
    tix = {"fr": c("Bus#"), "to": c("Bus#", 1), "ck": c("CKT"), "se": c("Section"),
           "rmin": c("Rat_Min"), "rmax": c("Rat_Max"), "step": c("Step"),
           "vmin": c("voltMin"), "vmax": c("voltMax"), "reg": c("Bus #"), "tap": c("Ratio"),
           "status": c("Status"), "ratio": c("Ratio")}
    tap = {}
    for r in rows:
        k = _branch_key(_i(r[tix["fr"]]), _i(r[tix["to"]]), r[tix["ck"]], _i(r[tix["se"]]) or 1)
        tap[k] = {"rat_min": _f(r[tix["rmin"]]), "rat_max": _f(r[tix["rmax"]]), "step": _f(r[tix["step"]]),
                  "v_min": _f(r[tix["vmin"]]), "v_max": _f(r[tix["vmax"]]), "reg_bus": _i(r[tix["reg"]]),
                  "control": r[tix["status"]], "tap_ratio": _f(r[tix["ratio"]])}

    # branches: lines and transformer legs
    p = results / TABLES["branch"]
    h, rows = _table(p)
    c = _Cols(p, h)
    bx = {n: c(n) for n in ("Fr Bus", "Fr Name", "FrAr", "To Bus", "To Name", "CKT", "Section", "St",
                            "R", "X", "Charg", "ShuntBFrom", "ShuntBTo", "RateA", "RateB", "RateC",
                            "RateD", "Length", "Own1", "MW_Flow", "MVAr_flow", "MVA_flow", "LossesMW",
                            "LossesMVAr")}
    fkv, tkv = c("Volt"), c("Volt", 1)
    bypass = 0
    for r in rows:
        if r[bx["CKT"]].upper() == "BP":
            # series-capacitor bypass records: TARA writes several per element
            # with the same key (format artefact, as in compare.py); skipped
            bypass += 1
            continue
        a, b = _i(r[bx["Fr Bus"]]), _i(r[bx["To Bus"]])
        k = _branch_key(a, b, r[bx["CKT"]], _i(r[bx["Section"]]) or 1)
        kind = "transformer" if k in tap else "branch"
        f = {"from": a, "from_name": r[bx["Fr Name"]], "from_kv": _f(r[fkv]),
             "to": b, "to_name": r[bx["To Name"]], "to_kv": _f(r[tkv]),
             "ck": r[bx["CKT"]], "se": _i(r[bx["Section"]]) or 1, "st": _i(r[bx["St"]]),
             "area": _i(r[bx["FrAr"]]), "r": _f(r[bx["R"]]), "x": _f(r[bx["X"]]), "b": _f(r[bx["Charg"]]),
             "b_from": _f(r[bx["ShuntBFrom"]]), "b_to": _f(r[bx["ShuntBTo"]]),
             "rate1": _f(r[bx["RateA"]]), "rate2": _f(r[bx["RateB"]]), "rate3": _f(r[bx["RateC"]]),
             "rate4": _f(r[bx["RateD"]]), "length": _f(r[bx["Length"]]), "owner": _i(r[bx["Own1"]]),
             # solved flow at the FROM end, and the losses: the to-end flow is
             # -(from-end flow - losses). For diagrams only, never compared.
             "mw": _f(r[bx["MW_Flow"]]), "mvar": _f(r[bx["MVAr_flow"]]), "mva": _f(r[bx["MVA_flow"]]),
             "loss_mw": _f(r[bx["LossesMW"]]), "loss_mvar": _f(r[bx["LossesMVAr"]])}
        if kind == "transformer":
            f.update(tap[k])
        recs[kind][k] = Record(kind, k, f, r)

    # generators
    p = results / TABLES["generator"]
    h, rows = _table(p)
    c = _Cols(p, h)
    gx = {n: c(n) for n in ("Bus#", "Bus Name", "Area", "ID", "Sta", "PGen", "Pmin", "Pmax", "Qmin",
                            "Qmax", "CtrlB#", "VoltTarg", "MBase", "XSource", "Own1")}
    gkv = c("Volt")
    for r in rows:
        n = _i(r[gx["Bus#"]])
        k = (n, r[gx["ID"]].upper())
        recs["generator"][k] = Record("generator", k, {
            "bus": n, "name": r[gx["Bus Name"]], "kv": _f(r[gkv]), "id": r[gx["ID"]], "area": _i(r[gx["Area"]]),
            "st": _i(r[gx["Sta"]]), "pgen": _f(r[gx["PGen"]]), "pmin": _f(r[gx["Pmin"]]),
            "pmax": _f(r[gx["Pmax"]]), "qmin": _f(r[gx["Qmin"]]), "qmax": _f(r[gx["Qmax"]]),
            "reg_bus": _i(r[gx["CtrlB#"]]), "vsched": _f(r[gx["VoltTarg"]]), "mbase": _f(r[gx["MBase"]]),
            "xsource": _f(r[gx["XSource"]]), "owner": _i(r[gx["Own1"]])}, r)

    # loads
    p = results / TABLES["load"]
    h, rows = _table(p)
    c = _Cols(p, h)
    lx = {n: c(n) for n in ("Bus#", "Bus Name", "Area", "ID", "St", "Pconst", "Qconst", "Pcurrt",
                            "Qcurrt", "PAdmit", "QAdmit", "Ownr")}
    lkv = c("Volt")
    for r in rows:
        n = _i(r[lx["Bus#"]])
        k = (n, r[lx["ID"]].upper())
        recs["load"][k] = Record("load", k, {
            "bus": n, "name": r[lx["Bus Name"]], "kv": _f(r[lkv]), "id": r[lx["ID"]], "area": _i(r[lx["Area"]]),
            "st": _i(r[lx["St"]]), "mw": _f(r[lx["Pconst"]]), "mvar": _f(r[lx["Qconst"]]),
            "mw_i": _f(r[lx["Pcurrt"]]), "mvar_i": _f(r[lx["Qcurrt"]]),
            "mw_z": _f(r[lx["PAdmit"]]), "mvar_z": _f(r[lx["QAdmit"]]), "owner": _i(r[lx["Ownr"]])}, r)

    # shunts (fixed, bus, switched; SVDs)
    p = results / TABLES["shunt"]
    h, rows = _table(p)
    c = _Cols(p, h)
    sx = {n: c(n) for n in ("Bus#", "Bus Name", "Area", "ShuntID", "Status", "Mod", "ModDescr",
                            "GShunt", "BShunt", "AdmitMin", "AdmitMax")}
    skv = c("Volt")
    for r in rows:
        n = _i(r[sx["Bus#"]])
        k = (n, r[sx["ShuntID"]].upper())
        mode = _i(r[sx["Mod"]])
        recs["shunt"][k] = Record("shunt", k, {
            "bus": n, "name": r[sx["Bus Name"]], "kv": _f(r[skv]), "id": r[sx["ShuntID"]], "area": _i(r[sx["Area"]]),
            # for switched shunts / SVDs (mode > 0) TARA's Status is the block
            # step it switched to (solved state, 7 -> 6 between solutions), so
            # only fixed and bus shunts carry an on/off status
            # (and for bus shunts it can be a count, 7 vs 6 = both on): on/off only
            "st": (1 if _i(r[sx["Status"]]) > 0 else 0) if mode <= 0 else None,
            "step_on": _i(r[sx["Status"]]),
            "mode": mode, "mode_name": r[sx["ModDescr"]],
            # nominal B for fixed / bus shunts; the switched range for controlled ones
            "b": _f(r[sx["BShunt"]]) if mode <= 0 else None, "g": _f(r[sx["GShunt"]]),
            "bmin": _f(r[sx["AdmitMin"]]) if mode > 0 else None,
            "bmax": _f(r[sx["AdmitMax"]]) if mode > 0 else None}, r)

    if not recs["bus"] or not recs["branch"]:
        raise ReportError(f"{results}: no buses or branches read")
    return Case(results, run, case_name, recs, {"series-cap bypass records": bypass})
