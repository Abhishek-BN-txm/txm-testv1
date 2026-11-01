"""Case information: a compact, reviewable summary of one solved case.

Built from a basecase run's TARA reports (CaseSum, AreaSum, BusData,
BranchData, Warn_Sum). Used by the `caseinfo` pipeline stage and the
Footprint Builder's "Case information" view.

What it flags, because an engineer would want to know before trusting any
study result built on the case:
- the solution itself (converged, iterations, largest mismatch);
- swing buses dispatched outside their own limits;
- areas whose actual interchange is far from scheduled;
- voltages outside 0.95-1.05 pu and line loadings near or over Rate A,
  restricted to the study area when one is given (a WECC case otherwise
  floods the list with other utilities' issues);
- TARA warnings at error level or above.

Rate 9999 means "no rating": such lines are skipped and counted, never
judged against 9999 MVA (see docs/tara_engine_notes.md).
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

V_LOW, V_HIGH, V_MIN_KV = 0.95, 1.05, 100.0
LOAD_WATCH = 90.0          # % of Rate A worth listing
TOP_N = 12
NO_RATING = 9999.0


def _rows(path: Path, first: str) -> tuple[list[str], list[list[str]]]:
    header, rows = None, []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for r in csv.reader(f):
            cells = [c.strip() for c in r]
            if header is None:
                if cells and cells[0] == first:
                    header = cells
                continue
            if cells and re.fullmatch(r"-?\d+", cells[0]):
                rows.append(cells)
    return header or [], rows


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _col(header: list[str], name: str) -> int | None:
    return header.index(name) if name in header else None


def case_summary(results: Path) -> dict:
    """CaseSum: solution, counts, swing buses."""
    text = (results / "CaseSum.csv").read_text(encoding="utf-8", errors="replace")
    kv = {}
    for line in text.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 2 and p[0] and not p[0][0].isdigit():
            kv.setdefault(p[0], p[1])
    counts = {}
    for key, label in (("Buses", "buses"), ("Machines", "generators"), ("Loads", "loads"),
                       ("NonTranBrn", "branches"), ("Transformers", "transformers"),
                       ("Phase Shifters", "phase_shifters"), ("Two-term.DC", "dc_lines"),
                       ("SwitcShunts", "switched_shunts"), ("Total Areas", "areas"),
                       ("Total Zones", "zones"), ("SwingBuses", "swing_buses")):
        if key in kv and _num(kv[key]) is not None:
            counts[label] = int(_num(kv[key]))
    swings = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("Bus#") and "curPGen" in line:
            hdr = [c.strip() for c in line.split(",")]
            ix = {n: hdr.index(n) for n in ("Bus#", "Bus Name", "Area", "AreaName",
                                            "curPGen", "MinPGen", "MaxPGen", "curQGen",
                                            "MinQGen", "MaxQGen") if n in hdr}
            for row in lines[i + 1:]:
                c = [x.strip() for x in row.split(",")]
                if not c or not re.fullmatch(r"\d+", c[0]):
                    break
                p, pmin, pmax = (_num(c[ix["curPGen"]]), _num(c[ix["MinPGen"]]),
                                 _num(c[ix["MaxPGen"]]))
                q, qmin, qmax = (_num(c[ix["curQGen"]]), _num(c[ix["MinQGen"]]),
                                 _num(c[ix["MaxQGen"]]))
                issues = []
                if p is not None and pmax is not None and p > pmax + 0.1:
                    issues.append(f"P {p:.1f} MW above Pmax {pmax:.1f}")
                if p is not None and pmin is not None and p < pmin - 0.1:
                    issues.append(f"P {p:.1f} MW below Pmin {pmin:.1f}")
                if q is not None and qmax and qmin is not None and not (qmin - 0.1 <= q <= qmax + 0.1):
                    issues.append(f"Q {q:.1f} Mvar outside {qmin:.1f}..{qmax:.1f}")
                swings.append({"bus": int(c[0]), "name": c[ix["Bus Name"]],
                               "area": int(_num(c[ix["Area"]], 0)), "area_name": c[ix["AreaName"]],
                               "p": p, "pmin": pmin, "pmax": pmax, "q": q,
                               "issues": issues})
            break
    status = kv.get("ConvStatus", "")
    return {"converged": status.lower() == "solved", "status": status,
            "iterations": int(_num(kv.get("Iterations"), 0) or 0),
            "max_p_mismatch_mw": _num(kv.get("busPmismMax")),
            "counts": counts, "swing_buses": swings}


def area_summary(results: Path) -> list[dict]:
    h, rows = _rows(results / "AreaSum.csv", "Area")
    need = ("Area", "AreaName", "Buses", "TotGen", "LoadAct", "TotLosses",
            "AreaInterTies", "DesInter")
    if not all(n in h for n in need):
        return []
    ix = {n: h.index(n) for n in need}
    out = []
    for r in rows:
        actual, sched = _num(r[ix["AreaInterTies"]], 0.0), _num(r[ix["DesInter"]], 0.0)
        out.append({"area": int(r[ix["Area"]]), "name": r[ix["AreaName"]],
                    "buses": int(_num(r[ix["Buses"]], 0)),
                    "gen": _num(r[ix["TotGen"]], 0.0), "load": _num(r[ix["LoadAct"]], 0.0),
                    "losses": _num(r[ix["TotLosses"]], 0.0),
                    "interchange": actual, "scheduled": sched,
                    "deviation": round(actual - sched, 1)})
    return out


def voltages(results: Path, buses: set[int] | None) -> dict:
    h, rows = _rows(results / "BusData.csv", "Bus#")
    iv, ikv, ia = _col(h, "Vmag [PU]"), _col(h, "Volt"), _col(h, "Area")
    if iv is None or ikv is None:
        return {}
    low, high, n = [], [], 0
    for r in rows:
        bus = int(r[0])
        if buses is not None and bus not in buses:
            continue
        kv, v = _num(r[ikv], 0.0), _num(r[iv])
        if kv < V_MIN_KV or v is None or v == 0:
            continue
        n += 1
        rec = {"bus": bus, "name": r[1], "kv": kv, "area": int(_num(r[ia], 0)), "v": round(v, 4)}
        if v < V_LOW:
            low.append(rec)
        elif v > V_HIGH:
            high.append(rec)
    return {"buses_checked": n, "min_kv": V_MIN_KV, "band": [V_LOW, V_HIGH],
            "below": len(low), "above": len(high),
            "lowest": sorted(low, key=lambda x: x["v"])[:TOP_N],
            "highest": sorted(high, key=lambda x: -x["v"])[:TOP_N]}


def loadings(results: Path, buses: set[int] | None) -> dict:
    h, rows = _rows(results / "BranchData.csv", "Fr Bus")
    im, ia, ist = _col(h, "MVA_flow"), _col(h, "RateA"), _col(h, "St")
    if im is None or ia is None:
        return {}
    watch, over, unrated, n = [], 0, 0, 0
    for r in rows:
        if ist is not None and r[ist] != "1":
            continue
        a, b = int(r[0]), int(r[5])
        if buses is not None and a not in buses and b not in buses:
            continue
        rate, mva = _num(r[ia]), _num(r[im], 0.0)
        if rate is None or rate <= 0 or rate >= NO_RATING:
            unrated += 1
            continue
        n += 1
        pct = 100.0 * abs(mva) / rate
        if pct >= 100.0:
            over += 1
        if pct >= LOAD_WATCH:
            watch.append({"from": a, "from_name": r[1], "to": b, "to_name": r[6],
                          "kv": _num(r[2]), "ckt": r[10], "mva": round(abs(mva), 1),
                          "rate_a": rate, "pct": round(pct, 1)})
    return {"lines_checked": n, "unrated_skipped": unrated, "over_100": over,
            "at_or_over_watch": len(watch), "watch_pct": LOAD_WATCH,
            "top": sorted(watch, key=lambda x: -x["pct"])[:TOP_N],
            "note": "lines (BranchData) against Rate A; transformers not included"}


def warnings(results: Path) -> list[dict]:
    path = results / "Warn_Sum.csv"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 4 and re.fullmatch(r"\d+", p[0]):
            sev = int(p[2][0]) if p[2][:1].isdigit() else 0
            out.append({"id": int(p[0]), "count": int(_num(p[1], 0)), "severity": sev,
                        "level": p[2].split(" ")[0], "message": p[3]})
    return sorted(out, key=lambda w: (-w["severity"], -w["count"]))


def case_info(results: Path, study_buses: set[int] | None = None,
              label: str = "", run: int | None = None, case: str = "") -> dict:
    results = Path(results)
    s = case_summary(results)
    areas = area_summary(results)
    totals = {"gen": round(sum(a["gen"] for a in areas), 1),
              "load": round(sum(a["load"] for a in areas), 1),
              "losses": round(sum(a["losses"] for a in areas), 1)}
    study_areas = None
    if study_buses:
        h, rows = _rows(results / "BusData.csv", "Bus#")
        ia = _col(h, "Area")
        study_areas = sorted({int(r[ia]) for r in rows if int(r[0]) in study_buses}) if ia is not None else None
    warn = warnings(results)
    flags = []
    if not s["converged"]:
        flags.append(f"case did not solve ({s['status'] or 'no status'})")
    for sw in s["swing_buses"]:
        for i in sw["issues"]:
            flags.append(f"swing bus {sw['bus']} {sw['name']} (area {sw['area']}): {i}")
    big = [a for a in areas if abs(a["deviation"]) > 10]
    if big:
        flags.append(f"{len(big)} area(s) more than 10 MW off scheduled interchange")
    errs = [w for w in warn if w["severity"] >= 3]
    if errs:
        flags.append(f"{sum(w['count'] for w in errs)} TARA warning(s) at 'potentially critical' or worse")
    return {"label": label, "run": run, "case": case, "solution": s, "totals": totals,
            "areas": areas, "study_areas": study_areas,
            "scope": "study area" if study_buses else "whole case",
            "voltages": voltages(results, study_buses),
            "loadings": loadings(results, study_buses),
            "warnings": [w for w in warn if w["severity"] >= 2][:20],
            "warnings_total": sum(w["count"] for w in warn),
            "flags": flags}


def study_buses_from_tags(bus_tags: Path, group: str) -> set[int] | None:
    """Buses of the footprint's study area, from the compiled bus_tags.csv."""
    if not Path(bus_tags).exists():
        return None
    out = set()
    with open(bus_tags, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if group in r.get("groups", "").split(";"):
                out.add(int(r["bus"]))
    return out or None


def render_md(infos: list[dict], study: str) -> str:
    """One short table across cases, for digests and the reviewer."""
    lines = [f"# Case information — {study}", "",
             "| Case | Run | Solved | Iter | Max mismatch MW | Gen MW | Load MW | Losses MW "
             "| V out of band | Lines ≥100% | Flags |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i in infos:
        s, v, ld = i["solution"], i["voltages"], i["loadings"]
        lines.append(f"| {i['label']} | {i['run']} | {'yes' if s['converged'] else 'NO'} "
                     f"| {s['iterations']} | {s['max_p_mismatch_mw']} | {i['totals']['gen']:,.0f} "
                     f"| {i['totals']['load']:,.0f} | {i['totals']['losses']:,.0f} "
                     f"| {v.get('below', 0)} low / {v.get('above', 0)} high "
                     f"| {ld.get('over_100', 0)} | {len(i['flags'])} |")
    lines += ["", f"Voltage and loading figures are for the {infos[0]['scope']}." if infos else ""]
    for i in infos:
        if i["flags"]:
            lines += ["", f"**{i['label']}**"] + [f"- {f}" for f in i["flags"]]
    return "\n".join(lines) + "\n"


def write(info: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{info['label']}.json"
    p.write_text(json.dumps(info, indent=1), encoding="utf-8")
    return p
