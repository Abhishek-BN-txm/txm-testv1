"""What the study desk shows for the APS queue (Research step, Interconnection queue tab).

Read-only: everything here comes from files the pipeline already wrote under
QueueData.root. Nothing is rebuilt, fetched or confirmed from the desk; the
CLI (scripts/aps_queue.py) stays the only writer.

    view(qd)          -> dict for /api/research/area (key "queue")
    resolve(qd, rel)  -> a servable file under pages/ export/ deliver/ library/, or None
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from tara_automation.ingest.aps import AREAS, QueueData, contracted
from tara_automation.ingest.aps.areas import PROCS
from tara_automation.ingest.aps.library import poi_key, read_poi_map

# folder -> file types the desk may serve from it
SERVE = {"pages": {".html"}, "export": {".csv"}, "deliver": {".csv"}, "library": {".csv", ".xlsx", ".md"}}
CTYPE = {".html": "text/html; charset=utf-8", ".csv": "text/csv; charset=utf-8",
         ".md": "text/markdown; charset=utf-8",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
PENDING_EXCLUDE = ("In service", "Surplus")
REQUEST_FIELDS = ["FERC Q Number", "Study Area", "Confidence", "Process", "Electrical Output MW",
                  "Requested POI", "County (normalized)", "Energy Source Type",
                  "Interconnection Request Type", "Current Status", "Submission Year"]


def _rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _mw(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _mtime(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M") if p.exists() else ""


def _service(t: str) -> str:
    t = (t or "").lower()
    return "NRIS" if "network" in t else "ERIS" if "energy" in t else ""


def _library(qd: QueueData) -> dict:
    """Counts from the last library run plus the live POI review state."""
    out: dict = {"built": (qd.library / "projects.xlsx").exists(), "built_at": _mtime(qd.library / "projects.xlsx"),
                 "run": None, "counts": {}, "reasons": {}}
    rep = qd.library / "ingest_report.md"
    if rep.exists():
        text = rep.read_text(encoding="utf-8")
        m = re.search(r"candidates from run (\d+)", text)
        out["run"] = int(m.group(1)) if m else None
        section = None
        for line in text.splitlines():
            if line.startswith("## "):
                section = line[3:].strip().lower()
            m = re.match(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*$", line)
            if m:
                (out["reasons"] if section and section.startswith("why") else out["counts"])[m.group(1)] = int(m.group(2))
    review = _rows(qd.library / "poi_review.csv")
    out["review"] = {"pois": len(review),
                     "line_taps": sum(bool(r.get("line_tap")) for r in review),
                     "use_bus_filled": sum(bool((r.get("use_bus") or "").strip()) for r in review),
                     "exact_candidates": sum(_mw(r.get("cand1_score")) >= 0.999 and not r.get("line_tap")
                                             for r in review)}
    out["confirmed_pois"] = len(read_poi_map(qd))
    return out


def view(qd: QueueData) -> dict:
    meta = qd.meta
    by_area = _rows(qd.queue_by_area)
    clean = {r["FERC Q Number"]: r for r in _rows(qd.queue_clean)}
    confirmed = read_poi_map(qd)
    name_to_key = {a: k for k, (a, _, _) in AREAS.items()}
    cmap, cproblems = contracted.read(qd.contracted)

    requests = []
    for r in by_area:
        q = r["FERC Q Number"]
        x = {f: r.get(f, "") for f in REQUEST_FIELDS}
        x["area_key"] = name_to_key.get(r.get("Study Area", ""), "")
        x["isd"] = clean.get(q, {}).get("LGP Projected In Service Date", "")
        x["service"] = _service(r.get("Interconnection Request Type", ""))
        x["poi_confirmed"] = poi_key(r.get("Requested POI", "")) in confirmed
        x["offtake"] = cmap.get(q)                    # None: no known offtake
        requests.append(x)

    areas = []
    for k, (name, _, title) in AREAS.items():
        mine = [x for x in requests if x["area_key"] == k]
        lad = _json(qd.ladders / f"{k}.json") or {}
        phases = lad.get("phases", [])
        dates = sorted(p.get("date", "") for p in phases if p.get("date"))
        page = qd.pages / f"{k}_ladder.html"
        areas.append({
            "key": k, "name": name, "title": title,
            "requests": len(mine),
            "by_process": {p: {"n": sum(x["Process"] == p for x in mine),
                               "mw": round(sum(_mw(x["Electrical Output MW"]) for x in mine if x["Process"] == p), 1)}
                           for p in PROCS},
            "pending_mw": round(sum(_mw(x["Electrical Output MW"]) for x in mine
                                    if x["Process"] not in PENDING_EXCLUDE), 1),
            "reports": len(phases), "latest_report": dates[-1] if dates else "",
            "unstudied": len(_json(qd.ladders / f"{k}_2026.json") or []),
            "serial": (qd.serial / f"{k}_serial_ladder.json").exists(),
            "contingent": (qd.contingent / f"{k}.json").exists(),
            "page": f"pages/{page.name}" if page.exists() else None,
            "contracted": contracted.summarize([x for x in mine if x["Process"] not in PENDING_EXCLUDE], cmap),
            "contracted_in_service": contracted.summarize([x for x in mine if x["Process"] == "In service"], cmap),
            **_area_summary(qd, k, mine, phases),
        })
    other = sorted({x["Study Area"] for x in requests if not x["area_key"]})

    files = []
    for d, exts in SERVE.items():
        folder = qd.root / d
        if not folder.exists():
            continue
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.suffix.lower() in exts and d != "pages":
                n = None
                if p.suffix.lower() == ".csv":
                    with open(p, encoding="utf-8", newline="") as f:
                        n = max(sum(1 for _ in f) - 1, 0)
                files.append({"path": f"{d}/{p.name}", "folder": d, "name": p.name, "rows": n,
                              "kb": round(p.stat().st_size / 1024, 1), "updated": _mtime(p)})

    conf: dict[str, int] = {}
    for x in requests:
        conf[x["Confidence"]] = conf.get(x["Confidence"], 0) + 1
    return {
        "posting": qd.posting, "source": meta.get("source", ""), "reports_through": str(meta.get("reports_through", "")),
        "built_at": _mtime(qd.queue_by_area),
        "totals": {"requests": len(requests),
                   "pending": sum(x["Process"] not in PENDING_EXCLUDE for x in requests),
                   "pending_mw": round(sum(_mw(x["Electrical Output MW"]) for x in requests
                                           if x["Process"] not in PENDING_EXCLUDE), 1),
                   "in_service": sum(x["Process"] == "In service" for x in requests),
                   "unassigned": sum(x["Study Area"] == "Unassigned" for x in requests),
                   "reports": len(_rows(qd.deliver / "aps_studies.csv")) or sum(a["reports"] for a in areas)},
        "contracted": {"present": qd.contracted.exists(), "file": qd.contracted.name, "rows": len(cmap),
                       "problems": cproblems, "classes": contracted.CLASSES, "none": contracted.NONE_LABEL,
                       "pending": contracted.summarize([x for x in requests if x["Process"] not in PENDING_EXCLUDE], cmap),
                       "in_service": contracted.summarize([x for x in requests if x["Process"] == "In service"], cmap),
                       "all": contracted.summarize(requests, cmap),
                       "announced": sorted(q for q, c in cmap.items() if c.get("announced")),
                       # ids looked up in the area table and in the Active Queue file itself (exact strings)
                       "not_in_queue": sorted(set(cmap) - {x["FERC Q Number"] for x in requests} - set(clean))},
        "confidence": conf, "processes": PROCS, "areas": areas, "other_areas": other,
        "requests": requests, "files": files, "library": _library(qd),
        "folder": str(qd.root),
    }


def _tech(t: str) -> str:
    t = (t or "").lower()
    return ("Gas (incl. hybrids)" if re.search(r"gas|diesel", t) else "Wind (incl. hybrids)" if "wind" in t
            else "Solar + storage" if "solar" in t and "storage" in t else "Storage" if "storage" in t
            else "Solar" if "solar" in t else "Other")


def _area_summary(qd: QueueData, key: str, mine: list[dict], phases: list[dict]) -> dict:
    """What the Study area summary tab shows for one area: its outline from the
    report figure, the latest reports, upgrade cost and the largest upgrades,
    the sink the study used, and the technology mix of the pending requests."""
    sa = _json(qd.root / "study_areas" / "study_areas.json") or {}
    poly = sa.get(key)
    ups = [u for u in _rows(qd.export / "network_upgrades.csv")
           if u.get("area_key") == key and u.get("is_latest_for_cycle") == "True" and u.get("cycle") != "Serial"]
    seen, uniq = set(), []
    for u in sorted(ups, key=lambda u: -_mw(u.get("cost_usd"))):
        if u["upgrade"] not in seen:
            seen.add(u["upgrade"]); uniq.append(u)
    sinks = [r for r in _rows(qd.root / "sinks" / "sinks_by_study.csv")
             if r.get("area_key") == key and (r.get("sink_method") or "not stated") != "not stated"]
    rank = {"Transition": 0}                         # current cycle first (2025 > 2024 > Transition), then newest report
    sink = max(sinks, key=lambda r: (rank.get(r.get("cycle"), int(r["cycle"]) if str(r.get("cycle", "")).isdigit() else -1),
                                     r.get("report_date", ""))) if sinks else None
    tech: dict[str, float] = {}
    for x in mine:
        if x["Process"] not in PENDING_EXCLUDE:
            k2 = _tech(x.get("Energy Source Type", ""))
            tech[k2] = round(tech.get(k2, 0) + _mw(x["Electrical Output MW"]), 1)
    latest: dict = {}
    for ph in phases:
        latest[(ph.get("cycle"), ph.get("sub", ""))] = ph
    return {
        "outline": {"polygon": poly["polygon"], "source": poly.get("source", ""), "fit_rms_px": poly.get("fit_rms_px")} if poly else None,
        "upgrades_total_usd": round(sum(_mw(u.get("cost_usd")) for u in uniq)),
        "upgrades_n": len(uniq),
        "top_upgrades": [{"label": u["upgrade"], "cost_usd": _mw(u.get("cost_usd")), "phase": u.get("phase", ""),
                          "first_q": u.get("first_q", ""), "trigger_mw": u.get("trigger_cum_mw", "")} for u in uniq[:5]],
        "latest_reports": [{"label": p.get("label", ""), "date": p.get("date", ""), "file": p.get("file", ""),
                            "projects": len(p.get("projects", [])), "upgrades": len(p.get("upgrades", []))}
                           for p in sorted(latest.values(), key=lambda p: p.get("date", ""), reverse=True)],
        "sink": {"method": sink["sink_method"], "quote": sink.get("quote", ""), "report": sink.get("report_file", ""),
                 "date": sink.get("report_date", ""), "cycle": sink.get("cycle", ""), "phase": sink.get("phase", ""),
                 "elements": sink.get("sink_elements", ""), "group": f"{key.upper()}_{'TC' if sink.get('cycle') == 'Transition' else sink.get('cycle')}_SINK"}
                if sink else None,
        "tech_pending_mw": dict(sorted(tech.items(), key=lambda kv: -kv[1])),
        "contingent_facilities": _contingent(qd, key, phases),
    }


def _contingent(qd: QueueData, key: str, phases: list[dict]) -> list[dict]:
    """Planned facilities from contingent/<key>.json, each with its status in the
    newest report that mentions it (C contingent, M modeled, X modeled out, CX)."""
    c = _json(qd.contingent / f"{key}.json")
    if not c:
        return []
    label = {p.get("file"): p.get("label", "") for p in phases}
    date = {p.get("file"): p.get("date", "") for p in phases}
    out = []
    for col in c.get("columns", []):
        hits = [(date.get(f, ""), f, st[col["id"]]) for f, st in c.get("status", {}).items() if col["id"] in st]
        if not hits:
            continue
        d, f, code = max(hits)
        out.append({"id": col["id"], "label": col["label"], "isd": col.get("isd", ""), "note": col.get("note", ""),
                    "latest_status": code, "latest_phase": label.get(f, f),
                    "contingent_in": [label.get(f2, f2) for _, f2, s2 in sorted(hits) if s2 in ("C", "CX")]})
    return out


def resolve(qd: QueueData, rel: str) -> Path | None:
    """`pages/dsr_ladder.html` -> its path, only inside the served folders and types."""
    parts = (rel or "").replace("\\", "/").split("/")
    if len(parts) != 2 or parts[0] not in SERVE:
        return None
    d, name = parts
    if not name or name.startswith(".") or Path(name).name != name or Path(name).suffix.lower() not in SERVE[d]:
        return None
    folder = (qd.root / d).resolve()
    p = (folder / name).resolve()
    return p if p.parent == folder and p.is_file() else None
