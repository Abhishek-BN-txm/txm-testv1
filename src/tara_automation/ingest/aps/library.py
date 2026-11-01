"""APS queue -> project library rows (task 18 schema), with POI matching (task 23).

    queue_by_study_area.csv + active queue + ladders
        -> library/projects.xlsx     requests whose POI is a confirmed case bus
        -> library/poi_review.csv    every other POI, with ranked case-bus candidates
        -> library/ingest_report.md  rows read / kept / needing attention

POI rule (same as projects.py): the bus is never guessed. A POI reaches the
library only once it is in `poi_map.csv`, and only a person puts it there:
fill `use_bus` in poi_review.csv, then `scripts/aps_queue.py accept-poi`.
Line taps ("Cholla-Pinnacle Peak 345 kV line") need a new bus built on the
change workbook (changes_ref), so they always stay in review.
"""

from __future__ import annotations

import csv
import datetime
import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from tara_automation.ingest.aps import AREAS, QueueData

SKIP_PROCESSES = ("In service",)
CLUSTER = {"Transition Cluster": "Transition", "2024 Cluster": "2024", "2025 Cluster": "2025",
           "2026 Cluster": "2026", "Pre-2024 (Transition or serial)": "Serial", "Surplus": "Surplus"}
MAP_FIELDS = ["poi_text", "poi_kv", "bus", "bus_name", "bus_kv", "confirmed_by", "confirmed", "note"]
_STOP = {"SUBSTATION", "SUB", "SWITCHYARD", "SWYD", "SS", "APS", "KV", "THE", "NEW", "PROPOSED", "FUTURE",
         "STATION", "SWITCHING", "INTERCONNECTION", "CUT", "IN", "CUTIN", "BUS", "500KV", "345KV", "230KV", "69KV"}
_ALIAS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "MOUNTAIN": "MT", "MOUNT": "MT"}


# ------------------------------------------------------------------ POI text --
def poi_kv(text: str) -> float | None:
    m = re.search(r"(\d{2,3}(?:\.\d)?)\s*-?\s*kv", text or "", re.I)
    return float(m.group(1)) if m else None


def is_line_tap(text: str) -> bool:
    """A POI on a line rather than at a substation: "A - B 500 kV line",
    "HANG1 500kV Cut-In", "line tap on ...". "County Line 500kV" is a substation."""
    t = (text or "").lower()
    return bool(re.search(r"\btap\b|cut-?\s?in\b|kv\s+line\b|\bline\s+tap\b", t)) or \
        bool(re.search(r"[a-z]\s*-\s*[a-z]", t))


def poi_key(text: str) -> str:
    """Spelling-insensitive key of a POI: name tokens without 'substation',
    'APS', 'kV' ... plus the kV. "Cholla 500kV" and "Cholla 500kV Substation"
    share CHOLLA|500, so one confirmation in poi_map.csv covers both."""
    kv = poi_kv(text)
    return f"{_compact(text)}|{kv:g}" if kv is not None else _compact(text)


def _tokens(text: str) -> list[str]:
    t = re.sub(r"\(.*?\)", " ", (text or "").upper())
    t = re.sub(r"\d{2,3}(\.\d)?\s*-?\s*KV", " ", t)
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    return [_ALIAS.get(w, w) for w in t.split() if w not in _STOP]


def _compact(text: str) -> str:
    return "".join(_tokens(text))


def score(poi: str, bus_name: str) -> float:
    a, b = _compact(poi), re.sub(r"[^A-Z0-9]", "", (bus_name or "").upper())
    if not a or not b:
        return 0.0
    s = difflib.SequenceMatcher(None, a, b).ratio()
    if b.startswith(a[:min(len(a), 6)]) or a.startswith(b[:min(len(b), 6)]):
        s = max(s, 0.8)
    if a == b:
        s = 1.0
    return round(s, 3)


# ------------------------------------------------------------------ case bus --
@dataclass
class Bus:
    number: int
    name: str
    kv: float
    area: int | None


def buses_from_run(results_dir: Path) -> dict[int, Bus]:
    """BusData.csv of a basecase run -> buses with area (CaseIndex has no area)."""
    from tara_automation.footprint import _table_rows

    h, rows = _table_rows(Path(results_dir) / "BusData.csv", "Bus#")
    ia = h.index("Area") if "Area" in h else None
    out = {}
    for r in rows:
        try:
            area = int(r[ia]) if ia is not None and r[ia] else None
        except ValueError:
            area = None
        out[int(r[0])] = Bus(int(r[0]), r[1].strip(), float(r[2]), area)
    return out


def candidates(poi: str, kv: float | None, buses: dict[int, Bus], prefer_area: int = 14,
               n: int = 3) -> list[tuple[Bus, float]]:
    pool = [b for b in buses.values() if kv is None or abs(b.kv - kv) < 0.6]
    ranked = sorted(((b, score(poi, b.name) + (0.05 if b.area == prefer_area else 0)) for b in pool),
                    key=lambda x: -x[1])
    return [(b, round(min(s, 1.0), 3)) for b, s in ranked[:n] if s >= 0.45]


# ------------------------------------------------------------------- mapping --
def read_poi_map(qd: QueueData) -> dict[str, dict]:
    if not qd.poi_map.exists():
        return {}
    with open(qd.poi_map, encoding="utf-8", newline="") as f:
        return {poi_key(r["poi_text"]): r for r in csv.DictReader(f) if r.get("bus")}


def _fuel(tech: str) -> tuple[str, str]:
    t = (tech or "").lower()
    if "storage" in t and not any(w in t for w in ("solar", "wind", "gas")):
        return "storage", "battery"
    parts = [w for w in ("solar", "wind", "gas") if w in t]
    fuel = "+".join(parts) or t or "unknown"
    if "storage" in t:
        fuel += "+storage"
    return "gen", fuel


def _year(s: str):
    m = re.search(r"/(\d{2,4})\s*$", s or "")
    if not m:
        return None
    y = m.group(1)
    return int("20" + y) if len(y) == 2 else int(y)


def _latest_costs(qd: QueueData) -> dict[str, str]:
    """Q -> 'latest study: total $x M' from each area's ladder (latest report per cycle)."""
    out = {}
    for k in AREAS:
        p = qd.ladders / f"{k}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        for ph in d["phases"]:                      # phases are in date order: last wins
            for r in ph["projects"]:
                out[r["q"]] = f"{ph['label']} ({ph['date']}): {r['service']}, total ${r['total'] / 1e6:.1f}M"
    return out


def build_library(qd: QueueData, buses: dict[int, Bus] | None = None, run_label: str = "",
                  out_dir: Path | None = None) -> dict:
    """Write projects.xlsx, poi_review.csv and ingest_report.md. Returns counts."""
    from tara_automation import projects as pl

    out_dir = Path(out_dir or qd.library)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(qd.queue_clean, encoding="utf-8", newline="") as f:
        Q = {r["FERC Q Number"]: r for r in csv.DictReader(f)}
    with open(qd.queue_by_area, encoding="utf-8", newline="") as f:
        A = list(csv.DictReader(f))
    pmap, costs = read_poi_map(qd), _latest_costs(qd)

    kept, attention, review = [], [], {}
    skipped = 0
    for a in A:
        q = a["FERC Q Number"]
        if a["Process"] in SKIP_PROCESSES:
            skipped += 1
            continue
        r = Q[q]
        poi = r["Requested POI"] or ""
        kv = poi_kv(poi)
        try:
            mw = float(r["Electrical Output MW"] or 0)
        except ValueError:
            mw = 0.0
        kind, fuel = _fuel(r["Energy Source Type"])
        svc = a["Interconnection Request Type"]
        svc_s = "NRIS+ERIS" if ("Network" in svc and "Energy" in svc) else ("NRIS" if "Network" in svc else ("ERIS" if "Energy" in svc else svc))
        row = {"id": q, "name": f"{fuel} {mw:g} MW @ {poi}"[:60], "source": "queue", "kind": kind,
               "poi_bus": None, "poi_name": poi, "poi_kv": kv, "mw": mw or None, "fuel": fuel,
               "status": r["Current Status"], "cod": _year(r["LGP Projected In Service Date"]),
               "cluster": CLUSTER.get(a["Process"], a["Process"]), "utility": "APS",
               "queue_pos": re.sub(r"\D", "", q) or q, "enabled": "Y",
               "comment": "; ".join(x for x in (f"area: {a['Study Area']} ({a['Confidence']})", svc_s,
                                                costs.get(q, "no study results yet")) if x)}
        why = []
        if mw <= 0:
            why.append("0 MW in the posting (uprate or add-on: check the request)")
        m = pmap.get(poi_key(poi))
        if m:
            row["poi_bus"] = int(m["bus"])
        elif is_line_tap(poi):
            why.append("line tap: build the POI on the change workbook and reference it (changes_ref)")
        else:
            why.append("POI not yet mapped to a case bus (see poi_review.csv)")
        if not m:
            rv = review.setdefault(poi_key(poi), {"poi_text": poi, "poi_kv": kv if kv is not None else "",
                                                   "line_tap": "Y" if is_line_tap(poi) else "",
                                                   "queue_ids": [], "mw_total": 0.0, "areas": set()})
            rv.setdefault("spellings", set()).add(poi)
            rv["queue_ids"].append(q)
            rv["mw_total"] += mw
            rv["areas"].add(a["Study Area"])
        (attention if why else kept).append((row, why))

    # projects.xlsx: confirmed POIs only, so the library always validates
    lib_path = out_dir / "projects.xlsx"
    pl.write_template(lib_path)
    pl.append_projects(lib_path, [r for r, _ in kept])
    lib = pl.read_library(lib_path)

    # poi_review.csv
    rv_rows = []
    for k, rv in sorted(review.items(), key=lambda kv: -kv[1]["mw_total"]):
        out = {"poi_text": rv["poi_text"], "poi_kv": rv["poi_kv"], "line_tap": rv["line_tap"],
               "n_requests": len(rv["queue_ids"]), "mw_total": round(rv["mw_total"], 1),
               "queue_ids": " ".join(rv["queue_ids"]), "study_areas": "; ".join(sorted(rv["areas"])),
               "spellings": " | ".join(sorted(rv.get("spellings", ())))}
        cands = candidates(rv["poi_text"], rv["poi_kv"] or None, buses) if (buses and not rv["line_tap"]) else []
        for i in range(3):
            b, s = cands[i] if i < len(cands) else (None, "")
            out[f"cand{i + 1}_bus"] = b.number if b else ""
            out[f"cand{i + 1}_name"] = b.name if b else ""
            out[f"cand{i + 1}_kv"] = b.kv if b else ""
            out[f"cand{i + 1}_area"] = b.area if b else ""
            out[f"cand{i + 1}_score"] = s
        out["use_bus"] = ""
        out["note"] = ""
        rv_rows.append(out)
    review_path = out_dir / "poi_review.csv"
    fields = (["poi_text", "poi_kv", "line_tap", "n_requests", "mw_total", "queue_ids", "study_areas", "spellings"]
              + [f"cand{i}_{c}" for i in (1, 2, 3) for c in ("bus", "name", "kv", "area", "score")]
              + ["use_bus", "note"])
    with open(review_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rv_rows)

    # ingest_report.md
    counts = {"read": len(A), "skipped_in_service": skipped, "kept": len(kept), "attention": len(attention),
              "pois_in_review": len(rv_rows), "library_problems": len(lib.problems)}
    reasons: dict[str, int] = {}
    for _, why in attention:
        for w_ in why:
            reasons[w_] = reasons.get(w_, 0) + 1
    lines = [f"# APS queue -> project library: ingest report",
             "", f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M} from Active Queue posting {qd.posting}"
             + (f"; POI candidates from {run_label}" if run_label else "; no basecase run given, so no POI candidates") + ".",
             "", "| | requests |", "|---|---|",
             f"| read (queue_by_study_area.csv) | {counts['read']} |",
             f"| skipped: already in service | {counts['skipped_in_service']} |",
             f"| kept in projects.xlsx (POI confirmed) | {counts['kept']} |",
             f"| need attention | {counts['attention']} |",
             "", "## Why rows need attention", "", "| reason | requests |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])]
    lines += ["", f"## POIs to confirm ({len(rv_rows)})", "",
              "Largest first. Fill `use_bus` in `poi_review.csv` for each POI you confirm "
              "(a bus number from the case), then run `python scripts/aps_queue.py accept-poi`. "
              "Line taps need a new bus on the change workbook instead.", "",
              "| POI | kV | requests | MW | best candidate |", "|---|---|---|---|---|"]
    for r in rv_rows[:40]:
        best = (f"{r['cand1_bus']} {r['cand1_name']} ({r['cand1_score']})" if r["cand1_bus"] else
                ("line tap" if r["line_tap"] else "none"))
        lines.append(f"| {r['poi_text']} | {r['poi_kv']} | {r['n_requests']} | {r['mw_total']:g} | {best} |")
    if lib.problems:
        lines += ["", "## Library problems", ""] + [f"- {p}" for p in lib.problems]
    (out_dir / "ingest_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return counts


def accept_review(qd: QueueData, review_csv: Path | None = None, who: str = "user",
                  buses: dict[int, Bus] | None = None) -> tuple[int, list[str]]:
    """Move rows with `use_bus` filled from poi_review.csv into poi_map.csv.
    With `buses`, a bus number not in the case or with the wrong kV is refused."""
    review_csv = Path(review_csv or qd.library / "poi_review.csv")
    with open(review_csv, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("use_bus") or "").strip()]
    existing = read_poi_map(qd)
    problems, added = [], []
    for r in rows:
        try:
            bus = int(float(r["use_bus"]))
        except ValueError:
            problems.append(f"{r['poi_text']}: use_bus {r['use_bus']!r} is not a bus number")
            continue
        name, bkv = "", ""
        if buses is not None:
            b = buses.get(bus)
            if b is None:
                problems.append(f"{r['poi_text']}: bus {bus} is not in the case")
                continue
            if r["poi_kv"] and abs(b.kv - float(r["poi_kv"])) > 0.6:
                problems.append(f"{r['poi_text']}: bus {bus} is {b.kv} kV, the POI says {r['poi_kv']} kV")
                continue
            name, bkv = b.name, b.kv
        else:
            for i in (1, 2, 3):
                if str(r.get(f"cand{i}_bus")) == str(bus):
                    name, bkv = r[f"cand{i}_name"], r[f"cand{i}_kv"]
        if poi_key(r["poi_text"]) in existing:
            problems.append(f"{r['poi_text']}: already in poi_map.csv (edit it there)")
            continue
        added.append({"poi_text": r["poi_text"], "poi_kv": r["poi_kv"], "bus": bus, "bus_name": name,
                      "bus_kv": bkv, "confirmed_by": who, "confirmed": f"{datetime.date.today()}",
                      "note": r.get("note", "")})
    if added:
        new = not qd.poi_map.exists()
        with open(qd.poi_map, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=MAP_FIELDS)
            if new:
                w.writeheader()
            w.writerows(added)
    return len(added), problems
