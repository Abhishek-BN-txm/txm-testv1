"""Where the APS queue requests and cluster upgrades are, for the maps.

Two reference layers:
  grid      research/areas/APS/data/grid/grid.json, cut from the North America
            line / substation KMZs (2018; scripts/grid_layers.py). Substation
            names + kV range, line geometry with From_Sub / To_Sub.
  workbook  data/reference/Bus_Coordinates.xlsx: case bus numbers with
            coordinates (bus name and SUBNAME), for confirmed POIs and for the
            candidate buses offered when a person confirms a POI.

A POI gets one location, in this order (each tagged with how it was found):
    confirmed   poi_map.csv bus -> workbook
    written     a bus number or coordinates written in the request
    line_tap    "A - B 500 kV line": on the grid line from A to B when the grid
                has it (its middle), else the midpoint of A and B
    grid        grid substation, name + kV (approximate; display only)
    bus         workbook bus or SUBNAME, name + kV (approximate; display only)
    county      not located: the page places it at the county centre
Name matches stay inside APS territory (POI_BOX); a number in the name must
match ("TS-25" is not "TS2"). Nothing here writes poi_map or feeds a case.

Upgrades: "A–B nnn kV line ..." -> the grid line A–B when it exists (existing
line work: reconductor, rebuild), else a straight line between A and B
(new line); "X nnn kV station / substation / transformer ..." -> point at X.

Series devices ("series cap / capacitor / comp / compensation", "series reactor"): a POINT at
the middle of the line they sit in, with that line kept for highlighting:
    series_line      the grid line between the label's two ends (midpoint along its route)
    series_straight  no grid line: midpoint of the straight segment between the two ends
    series_at_sub    the label names one substation: the unique grid line from it (stated kV,
                     else its highest kV) whose other end is also named in the label; when
                     there is no such line, the point sits at the substation (line unknown)
A line is never invented; a named end that cannot be located leaves the upgrade unplaced.
"""

from __future__ import annotations

import collections
import csv
import json
import math
import re
from pathlib import Path

from tara_automation.ingest.aps import QueueData
from tara_automation.ingest.aps.library import is_line_tap, poi_key, poi_kv, read_poi_map, score

BBOX = (31.0, -115.6, 37.4, -107.9)          # region drawn and searched (lat_min, lon_min, lat_max, lon_max)
POI_BOX = (31.3, -114.85, 37.05, -108.3)     # APS territory: Arizona + the Four Corners edge of New Mexico
MIN_SCORE = 0.85
CAND_MILES = 3.0
_cache: dict = {}


def _in(box, lat, lon) -> bool:
    return box[0] <= lat <= box[2] and box[1] <= lon <= box[3]


def _miles(a, b) -> float:
    dy = (a[0] - b[0]) * 69.0
    dx = (a[1] - b[1]) * 69.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


# ------------------------------------------------------------- references --
def _ref() -> dict[int, dict]:
    """Workbook buses inside BBOX with their SUBNAME, cached as CSV under data/cache/."""
    if "ref" in _cache:
        return _cache["ref"]
    from tara_automation import bus_coords as BC
    out: dict[int, dict] = {}
    wb = BC.DEFAULT_WORKBOOK
    if wb.exists():
        cache = BC.CACHE_DIR / f"aps_geo_subs_{BC._sha(wb)[:16]}.csv"
        if not cache.exists():
            import openpyxl
            ws = openpyxl.load_workbook(wb, read_only=True).worksheets[0]
            it = ws.iter_rows(values_only=True)
            ix = {h: i for i, h in enumerate(next(it))}
            rows = []
            for r in it:
                if r[ix["INTERCONNECT"]] != "WECC":
                    continue
                try:
                    la, lo, n = float(r[ix["Y_LAT"]]), float(r[ix["X_LONG"]]), int(r[ix["BUSNUM"]])
                except (TypeError, ValueError):
                    continue
                if _in(BBOX, la, lo):
                    sub = r[ix["SUBNAME"]]
                    rows.append([n, la, lo, r[ix["BUSNAME"]] or "", r[ix["VOLTAGE"]] or "",
                                 "" if sub in (None, "NULL") else sub])
            cache.parent.mkdir(parents=True, exist_ok=True)
            with open(cache, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["bus", "lat", "lon", "name", "kv", "sub"])
                w.writerows(rows)
        with open(cache, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                n = int(r["bus"])
                if n in out and out[n]["sub"]:
                    continue
                out[n] = {"lat": float(r["lat"]), "lon": float(r["lon"]), "name": r["name"].strip(),
                          "kv": float(r["kv"]) if r["kv"] else None,
                          "sub": re.sub(r"\s*\(.*?\)", "", r["sub"]).strip()}
    _cache["ref"] = out
    return out


def _grid(qd: QueueData | None) -> dict:
    """grid.json of the research area that holds this queue folder ({} when not cut yet)."""
    if qd is None:
        return {}
    root = Path(qd.root).resolve().parent.parent / "data" / "grid" / "grid.json"
    key = ("grid", str(root), root.stat().st_mtime if root.exists() else 0)
    pf = root.with_name("planned_subs.csv")
    key = key + (pf.stat().st_mtime if pf.exists() else 0,)
    if key not in _cache:
        g = json.loads(root.read_text(encoding="utf-8")) if root.exists() else {}
        if pf.exists():
            with open(pf, encoding="utf-8", newline="") as f:
                g["planned"] = list(csv.DictReader(f))
        _cache[key] = g
    return _cache[key]


# ------------------------------------------------------ planned stations --
PLANNED_FIELDS = ["name", "aliases", "kv", "lat", "lon", "source", "note"]


def planned_sub(name: str, kv, grid: dict):
    """research/areas/<ID>/data/grid/planned_subs.csv: places the 2018 grid and the bus workbook
    don't have (TS30, Sitgreaves, Bianco ...), documented or estimated, each with an uncertainty
    (mi) and a confidence. Matched on the normalised name of the row or any alias (every
    spelling the queue and the upgrade labels use); rows without coordinates are ignored."""
    from difflib import SequenceMatcher

    from tara_automation.ingest.aps.library import _compact
    key = _compact(name)
    if not key:
        return None
    best = None
    for r in grid.get("planned", []):
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (TypeError, ValueError):
            continue
        if kv is not None and r.get("kv") and not any(abs(float(k) - kv) <= 1 for k in re.findall(r"\d+(?:\.\d+)?", r["kv"])):
            continue
        for n in [r["name"]] + [a.strip() for a in (r.get("aliases") or "").split(";") if a.strip()]:
            c = _compact(n)
            if not c:
                continue
            s = 1.0 if c == key else SequenceMatcher(None, c, key).ratio()
            if s >= 0.92 and (best is None or s > best[0]):
                best = (s, r, lat, lon)
    if not best:
        return None
    s, r, lat, lon = best
    unc = float(r.get("uncertainty_mi") or 0) or None
    return {"lat": lat, "lon": lon, "src": "planned", "score": round(s, 3), "unc": unc, "conf": r.get("confidence", ""),
            "label": f"{r['name']} ({'estimated' if str(r.get('method', '')).startswith('estimate') else 'documented'}"
                     f"{f', ±{unc:g} mi' if unc else ''}{', ' + r['confidence'] + ' confidence' if r.get('confidence') else ''})"}


# ---------------------------------------------------------------- matching --
def _nums(text: str) -> set:
    return set(re.findall(r"\d+", re.sub(r"\d{2,3}(\.\d)?\s*-?\s*kv", " ", text or "", flags=re.I)))


def _clean(text: str) -> str:
    t = re.sub(r"\(.*?\)", " ", text or "")
    t = re.sub(r"\b(line|tap|cut-?\s?in|loop-?in|switchyard|switching station|substation|sub|sy|yard|"
               r"on the|between|and|existing|new|future|proposed|planned|aps)\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" ,.-")


def _kv_ok(kv, lo, hi) -> bool:
    if kv is None or hi is None:
        return True
    return (lo if lo is not None else hi) - 1 <= kv <= hi + 1


def grid_sub(name: str, kv, grid: dict, box=POI_BOX):
    """Best grid substation for a name (+kV). -> (sub, score) or None."""
    nums, best = _nums(name), None
    for s in grid.get("subs", []):
        if box and not _in(box, *s["ll"]):
            continue
        if not _kv_ok(kv, s.get("kvmin"), s["kv"]):
            continue
        if nums and not nums <= set(re.findall(r"\d+", s["n"])):
            continue
        sc = max(score(name, s["n"]), score(name, re.sub(r"\s*\(.*?\)", "", s["n"])))
        if sc >= MIN_SCORE and (best is None or (sc, s["kv"]) > (best[1], best[0]["kv"])):
            best = (s, sc)
    return best


def bus_by_name(name: str, kv, ref=None, box=POI_BOX):
    """Best workbook bus for a name (+kV), on its bus name or SUBNAME. -> (bus, row, score) or None."""
    ref = _ref() if ref is None else ref
    nums, best = _nums(name), None
    for n, b in ref.items():
        if kv is not None and b["kv"] is not None and abs(b["kv"] - kv) > 1:
            continue
        if box and not _in(box, b["lat"], b["lon"]):
            continue
        if nums and not nums <= set(re.findall(r"\d+", f"{b['name']} {b['sub']}")):
            continue
        sc = max(score(name, b["name"]), score(name, b["sub"]) if b["sub"] else 0.0)
        if sc >= MIN_SCORE and (best is None or (sc, bool(b["sub"])) > (best[2], bool(best[1]["sub"]))):
            best = (n, b, sc)
    return best


def place(name: str, kv, grid: dict, ref=None, box=POI_BOX, qloc: dict | None = None):
    if re.match(r"\s*Q\d", name or ""):
        return _place(name, kv, grid, ref, box, qloc)
    key = ("place", name, kv, box, id(grid), len(grid.get("planned", [])))
    if key not in _cache:
        _cache[key] = _place(name, kv, grid, ref, box, qloc)
    return _cache[key]


def _place(name: str, kv, grid: dict, ref=None, box=POI_BOX, qloc: dict | None = None):
    """A named substation: grid first, then the workbook. -> {lat, lon, src, label, score} or None.
    "Q564" (a project's own switching station) -> that request's POI location, via qloc."""
    pl = planned_sub(name, kv, grid)
    if pl:
        return pl
    m = re.fullmatch(r"\s*(Q\d+[A-Z]?)\s*(sw\.?\s*sta\.?|switching station)?\s*", name or "", re.I)
    if m:
        loc = (qloc or {}).get(m.group(1).upper())
        return {"lat": loc["lat"], "lon": loc["lon"], "src": "project", "label": f"{m.group(1).upper()} POI ({loc.get('label', '')})",
                "score": 1.0} if loc else None
    g = grid_sub(name, kv, grid, box)
    b = bus_by_name(name, kv, ref, box)
    if g and b and b[2] > g[1]:           # the closer name wins ("North Gila": N.GILA over grid "Gila")
        g = None
    if g:
        s = g[0]
        return {"lat": s["ll"][0], "lon": s["ll"][1], "src": "grid", "label": f"{s['n']} ({s['kv']:g} kV, {s['p'].lower()})",
                "score": g[1]}
    if b:
        r = b[1]
        return {"lat": r["lat"], "lon": r["lon"], "src": "bus", "label": r["name"] + (f" ({r['sub']})" if r["sub"] else ""),
                "bus": b[0], "score": b[2]}
    return None


_LEAD = {"upgrade", "upgrades", "rebuild", "reconductor", "rebuild/reconductor", "new", "second", "third", "construct",
         "add", "of", "wapa’s", "wapa's", "aps", "srp", "tep", "the", "loop", "cut", "existing", "to", "on", "both",
         "part", "line", "tap", "series", "reactor", "cap", "capacitor", "relocate", "re-terminate", "oh", "double",
         "circuit", "structures", "cut-in", "into", "in", "and", "&", "a", "an", "at", "two", "three", "reduce",
         "reconfigure", "replace", "wapa", "install", "expand", "expansion"}
_SEG = r"[A-Z0-9][\w.'’/ ]*?"
_LINE = re.compile(rf"({_SEG})\s*[–—-]\s*([A-Z][\w.'’ ]*?)(?:\s*[–—-]\s*([A-Z][\w.'’ ]*?))?\s*(?:#\s?\d+\s*)?(\d{{2,3}})\s*-?\s*(?i:kv)")


def _trim(name: str) -> str:
    """'Upgrade WAPA’s Saguaro' -> 'Saguaro'; 'Q388Q392 SWST' -> 'Q388'."""
    m = re.match(r"\s*(Q\d+)", name or "")
    if m:
        return m.group(1)
    w = (name or "").split()
    while w and (w[0].lower() in _LEAD or re.match(r"^[\d(]|.*ohm|^NU\d", w[0], re.I) or w[0][:1].islower()):
        w.pop(0)
    rest = " ".join(w)
    m = re.match(r"\s*(Q\d+)", rest)
    return m.group(1) if m else _clean(rest)


def _endpoints(text: str):
    """'Add 5-ohm series reactor to Ocotillo-Kyrene 230 kV Line' -> (['Ocotillo', 'Kyrene'], 230.0).
    Three-ended labels ('A-B-C 230 kV') give three names."""
    m = _LINE.search(text or "")
    if m:
        names = [x for x in (_trim(g) for g in m.groups()[:3] if g) if x]
        if len(names) >= 2:
            return names, float(m.group(4))
    return None


def grid_line(a: str, b: str, kv, grid: dict):
    """The grid line whose From_Sub / To_Sub match a and b (either way round)."""
    best = None
    for ln in grid.get("lines", []):
        if kv is not None and abs(ln["kv"] - kv) > 1:
            continue
        f, t = ln.get("f", ""), ln.get("t", "")
        if not f or not t:
            continue
        s = max(min(score(a, f), score(b, t)), min(score(a, t), score(b, f)))
        if s >= MIN_SCORE and (best is None or s > best[1]):
            best = (ln, s)
    return best


def _mid(coords):
    """Point halfway along a polyline [[lat, lon], ...]."""
    d = [0.0]
    for p, q in zip(coords, coords[1:]):
        d.append(d[-1] + _miles(p, q))
    half = d[-1] / 2
    for i in range(1, len(coords)):
        if d[i] >= half:
            f = (half - d[i - 1]) / ((d[i] - d[i - 1]) or 1)
            return [coords[i - 1][0] + f * (coords[i][0] - coords[i - 1][0]),
                    coords[i - 1][1] + f * (coords[i][1] - coords[i - 1][1])]
    return coords[0]


def candidates(lat, lon, kv, ref=None, n=5) -> list[dict]:
    """Case buses near a location (same kV) for a person to confirm the POI with."""
    ref = _ref() if ref is None else ref
    out = []
    for num, b in ref.items():
        if kv is not None and b["kv"] is not None and abs(b["kv"] - kv) > 1:
            continue
        mi = _miles((lat, lon), (b["lat"], b["lon"]))
        if mi <= CAND_MILES:
            out.append({"bus": num, "name": b["name"], "kv": b["kv"], "sub": b["sub"], "mi": round(mi, 1)})
    return sorted(out, key=lambda x: x["mi"])[:n]


_BUSNO = re.compile(r"bus\s*(?:id|number|no\.?|#)?\s*[:=]?\s*(\d{4,6})", re.I)
_LATLON = re.compile(r"(-?\d{2}\.\d{3,})\s*,\s*(-?\d{3}\.\d{3,})")


def locate(poi: str, pmap: dict, ref=None, grid: dict | None = None) -> dict | None:
    ref = _ref() if ref is None else ref
    grid = grid or {}
    kv = poi_kv(poi)
    m = pmap.get(poi_key(poi))
    if m and m.get("bus") and int(m["bus"]) in ref:
        b = ref[int(m["bus"])]
        return {"lat": b["lat"], "lon": b["lon"], "how": "confirmed", "bus": int(m["bus"]), "label": b["name"]}
    c = _BUSNO.search(poi or "")
    if c and int(c.group(1)) in ref:
        b = ref[int(c.group(1))]
        return {"lat": b["lat"], "lon": b["lon"], "how": "written", "bus": int(c.group(1)),
                "label": f"{b['name']} (bus number in the request)"}
    c = _LATLON.search(poi or "")
    if c:
        la, lo = float(c.group(1)), -abs(float(c.group(2)))
        if _in(POI_BOX, la, lo):
            return {"lat": la, "lon": lo, "how": "written", "label": "coordinates in the request"}
    pl = planned_sub(poi, kv, grid)
    if pl:
        return {"lat": pl["lat"], "lon": pl["lon"], "how": "planned", "label": pl["label"], "score": pl["score"],
                "unc": pl["unc"], "conf": pl["conf"]}
    if is_line_tap(poi):
        ends = _endpoints(poi)
        if ends:
            (a_, z_), kv_ = ends[0][:1] + ends[0][-1:], ends[1]
            gl = grid_line(a_, z_, kv_, grid)
            if gl:
                lat, lon = _mid(gl[0]["c"])
                return {"lat": lat, "lon": lon, "how": "line_tap", "label": gl[0]["n"], "line": gl[0]["c"]}
            a, z = place(a_, kv_, grid, ref, BBOX), place(z_, kv_, grid, ref, BBOX)
            if a and z:
                return {"lat": (a["lat"] + z["lat"]) / 2, "lon": (a["lon"] + z["lon"]) / 2, "how": "line_tap",
                        "label": f"{a['label']} – {z['label']} (midpoint)",
                        "line": [[a["lat"], a["lon"]], [z["lat"], z["lon"]]]}
        return None
    name = _clean(poi) + " " + " ".join(re.findall(r"TS-?\s?\d+", poi or "", re.I))
    p = place(name.strip(), kv, grid, ref)
    if p:
        return {"lat": p["lat"], "lon": p["lon"], "how": p["src"], "label": p["label"], "score": p["score"],
                **({"bus": p["bus"]} if "bus" in p else {})}
    return None


def poi_layer(pois: list[str], qd: QueueData) -> dict:
    """{poi text: location (+ candidate buses unless confirmed)} for every POI text given."""
    pmap, ref, grid = _cache.setdefault(("pmap", str(qd.poi_map), qd.poi_map.stat().st_mtime if qd.poi_map.exists() else 0), read_poi_map(qd)), _ref(), _grid(qd)
    out = {}
    for t in dict.fromkeys(p for p in pois if p):
        key = ("locate", t, id(grid), id(pmap) if pmap else 0)
        if key not in _cache:
            _cache[key] = locate(t, pmap, ref, grid)
        loc = dict(_cache[key]) if _cache[key] else None
        if loc and loc["how"] != "confirmed":
            ck = ("cands", round(loc["lat"], 5), round(loc["lon"], 5), poi_kv(t))
            if ck not in _cache:
                _cache[ck] = candidates(loc["lat"], loc["lon"], poi_kv(t), ref)
            loc["cands"] = _cache[ck]
        out[t] = loc or {"how": "county"}
    return out


_SERIES = re.compile(r"\bseries[\s-]*(cap(?:acitor)?s?|comp(?:ensation)?|reactors?)\b", re.I)
_CAPRUN = r"[A-Z][\w.'’]*(?:\s+[A-Z][\w.'’]*)*"
_LINE_NOKV = re.compile(rf"({_CAPRUN})\s*[–—-]\s*({_CAPRUN})")
_GENERIC_END = {"TAP", "JUNCTION", "SPLIT", "CUT", "LOOP"}


def series_device(label: str) -> str | None:
    """'series capacitor' / 'series reactor' when the label is an in-line series device itself
    (not a new line that merely includes series caps: 'New A–B 500 kV line incl. ... series caps')."""
    m = _SERIES.search(label or "")
    if not m or re.search(r"\bincl", (label or "")[:m.start()], re.I):
        return None
    return "series reactor" if m.group(1).lower().startswith("reactor") else "series capacitor"


def _label_kv(label: str):
    m = re.search(r"(\d{2,3})\s*-?\s*kV\b", label or "", re.I)
    return float(m.group(1)) if m else None


def _series_ends(label: str):
    """Two (or three) ends of the line a series device sits in; the kV is optional here
    ('Add series cap Sitgreaves–Pinnacle Peak', 'NU #1 Sun Valley-Hassayampa Tap 5-ohm ...')."""
    e = _endpoints(label)
    if e:
        return e
    m = _LINE_NOKV.search(label or "")
    if m:
        names = [x for x in (_trim(g) for g in m.groups()) if x]
        if len(names) == 2:
            return names, _label_kv(label)
    return None


def _cap_runs(text: str) -> list[str]:
    """Capitalised word runs and their sub-runs (1-3 words), plus Q numbers: candidate place names."""
    out = []
    for run in re.findall(_CAPRUN, text or ""):
        w = run.split()
        out += [" ".join(w[i:j]) for i in range(len(w)) for j in range(i + 1, min(len(w), i + 3) + 1)]
    return out


def series_geom(label: str, device: str, grid: dict, ref=None, qloc: dict | None = None) -> dict | None:
    """Point at the middle of the line a series capacitor / reactor sits in (see module doc)."""
    base = {"kind": "point", "device": device}
    ends = _series_ends(label)
    if ends:
        names, kv = ends
        a, z = names[0], names[-1]
        gl = grid_line(a, z, kv, grid)
        if gl:
            return {**base, "how": "series_line", "c": [_mid(gl[0]["c"])], "line": gl[0]["c"], "of": gl[0]["n"]}
        pa, pz = place(a, kv, grid, ref, BBOX, qloc), place(z, kv, grid, ref, BBOX, qloc)
        if pa and pz:
            seg = [[pa["lat"], pa["lon"]], [pz["lat"], pz["lon"]]]
            return {**base, "how": "series_straight", "c": [_mid(seg)], "line": seg,
                    "of": f"{pa['label']} – {pz['label']}"}
        return {"kind": "unplaced", "need": [n for n, p in ((a, pa), (z, pz)) if not p], "kv": kv}
    # one named substation: "Relocate Cholla series cap to Sitgreaves", "Upgrade Eldorado series caps in Moenkopi line"
    m = _SERIES.search(label)
    sub = _trim(label[:m.start()]) if m else ""
    if not sub or re.fullmatch(r"Q\d+[A-Z]?", sub):
        return None
    kv = _label_kv(label)
    p = place(sub, kv, grid, ref, BBOX, qloc)
    if not p:
        return {"kind": "unplaced", "need": [sub], "kv": kv}
    lkv = kv
    if lkv is None:          # the substation's highest kV, from the grid lines that end there
        kvs = [ln["kv"] for ln in grid.get("lines", [])
               if max(score(sub, ln.get("f", "")), score(sub, ln.get("t", ""))) >= MIN_SCORE]
        lkv = max(kvs) if kvs else None
    others = [n for n in _cap_runs(label[m.end():] + " " + label[:m.start()]) if score(sub, n) < MIN_SCORE]
    hits = {}
    for ln in grid.get("lines", []) if lkv else []:
        if abs(ln["kv"] - lkv) > 1:
            continue
        f, t = ln.get("f", ""), ln.get("t", "")
        for near, far in ((f, t), (t, f)):
            if (far and near and score(sub, near) >= MIN_SCORE and score(sub, far) < MIN_SCORE
                    and far.upper() not in _GENERIC_END and any(score(o, far) >= MIN_SCORE for o in others)):
                hits.setdefault(tuple(sorted((near.upper(), far.upper()))), ln)   # parallel circuits = one corridor
    if len(hits) == 1:
        ln = next(iter(hits.values()))
        return {**base, "how": "series_line", "c": [_mid(ln["c"])], "line": ln["c"], "of": ln["n"]}
    return {**base, "how": "series_at_sub", "c": [[p["lat"], p["lon"]]], "of": p["label"],
            "note": "the line it sits in could not be identified from the label"
                    + (f" ({len(hits)} candidate lines)" if hits else "")}


def upgrade_geom(label: str, grid: dict, ref=None, qloc: dict | None = None, series: bool = True) -> dict | None:
    """Geometry of one network upgrade from its label. series=False skips the series-device
    handling (the desk map keeps drawing those as lines)."""
    ref = _ref() if ref is None else ref
    dev = series_device(label) if series else None
    if dev:
        g = series_geom(label, dev, grid, ref, qloc)
        if g is not None:
            return g
    ends = _endpoints(label)
    if ends:
        names, kv = ends
        new = re.match(r"\s*(new|second|third|construct)\b", label, re.I)
        if len(names) == 2 and not new:
            gl = grid_line(names[0], names[1], kv, grid)
            if gl:
                return {"kind": "line", "how": "grid", "c": gl[0]["c"], "of": gl[0]["n"]}
        pts = [place(n, kv, grid, ref, BBOX, qloc) for n in names]
        if all(pts):
            return {"kind": "line", "how": "straight", "c": [[p["lat"], p["lon"]] for p in pts],
                    "of": " – ".join(p["label"] for p in pts)}
        return {"kind": "unplaced", "need": [n for n, p in zip(names, pts) if not p], "kv": kv}
    m = re.search(r"([A-Z][A-Za-z.'’ ]+?)\s+(\d{2,3})(?:/\d{2,3})*\s*kV\b", label)
    if m:
        name = _trim(m.group(1))
        p = place(name, float(m.group(2)), grid, ref, BBOX, qloc) if name else None
        if p:
            return {"kind": "point", "how": p["src"], "c": [[p["lat"], p["lon"]]], "of": p["label"]}
        if name:
            return {"kind": "unplaced", "need": [name], "kv": float(m.group(2))}
    return None


def page_layer(qd: QueueData, pois: list[str], upgrades: list[str], qpoi: dict | None = None) -> dict:
    """What a ladder page embeds for its map. qpoi: queue id -> POI text (for "Q564" upgrade ends)."""
    grid, ref = _grid(qd), _ref()
    P = poi_layer(list(pois) + list((qpoi or {}).values()), qd)
    qloc = {q: P[t] for q, t in (qpoi or {}).items() if t in P and P[t].get("how") != "county"}
    up, need = {}, collections.Counter()
    for u in dict.fromkeys(upgrades):
        g = upgrade_geom(u, grid, ref, qloc)
        if g and g["kind"] == "unplaced":
            need.update(g["need"])
        elif g:
            up[u] = g
    # county centres for POIs that could not be located: mean of the grid substations in that county
    cty: dict = collections.defaultdict(list)
    for s in grid.get("subs", []):
        if s.get("s") in ("AZ", "NM") and s.get("cty"):
            cty[f"{s['cty']}|{s['s']}"].append(s["ll"])
    centres = {k: [round(sum(p[0] for p in v) / len(v), 4), round(sum(p[1] for p in v) / len(v), 4)] for k, v in cty.items()}
    return {"pois": P, "upgrades": up, "unplaced": dict(need), "box": grid.get("box") or list(BBOX), "counties": centres,
            "grid": {"lines": grid.get("lines", []), "subs": grid.get("subs", []), "source": grid.get("source", ""),
                     "states": grid.get("states", []), "extracted": grid.get("extracted", "")} if grid else None}


# ------------------------------------------------------- desk (whole APS) --
def pois(requests: list[dict], qd: QueueData) -> list[dict]:
    pmap, ref, grid = read_poi_map(qd), _ref(), _grid(qd)
    groups: dict[str, dict] = {}
    for r in requests:
        k = poi_key(r["Requested POI"])
        g = groups.setdefault(k, {"key": k, "poi": r["Requested POI"], "q": [], "county": r.get("County (normalized)", "")})
        g["q"].append(r["FERC Q Number"])
    out = []
    for g in groups.values():
        loc = locate(g["poi"], pmap, ref, grid)
        if loc:
            loc = {**loc, "bus_name": loc.get("label", "")}
        out.append({**g, **(loc or {"how": "county"})})
    return out


def upgrades(qd: QueueData) -> list[dict]:
    p = qd.export / "network_upgrades.csv"
    if not p.exists():
        return []
    grid, ref = _grid(qd), _ref()
    with open(p, encoding="utf-8", newline="") as f:
        rows = [u for u in csv.DictReader(f) if u.get("is_latest_for_cycle") == "True" and u.get("cycle") != "Serial"]
    out, seen = [], set()
    for u in rows:
        if (u["upgrade"], u["study_area"]) in seen:
            continue
        seen.add((u["upgrade"], u["study_area"]))
        g = upgrade_geom(u["upgrade"], grid, ref, series=False)
        if not g or g["kind"] != "line":
            continue
        c = g["c"]
        out.append({"label": u["upgrade"], "study_area": u["study_area"], "area_key": u["area_key"], "phase": u["phase"],
                    "cost_usd": float(u["cost_usd"] or 0), "first_q": u.get("first_q", ""), "how": g["how"], "c": c,
                    "from": [c[0][0], c[0][1], ""], "to": [c[-1][0], c[-1][1], ""]})
    return out


def write_planned_template(qd: QueueData, need: dict) -> Path:
    """Add rows for substations the maps could not place (keeps rows a person already filled)."""
    pf = Path(qd.root).resolve().parent.parent / "data" / "grid" / "planned_subs.csv"
    rows = []
    if pf.exists():
        with open(pf, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    have = {r["name"].lower() for r in rows} | {a.strip().lower() for r in rows for a in (r.get("aliases") or "").split(";")}
    for n, c in sorted(need.items(), key=lambda kv: -kv[1]):
        if n.lower() not in have and not re.fullmatch(r"Q\d+", n):
            rows.append({"name": n, "aliases": "", "kv": "", "lat": "", "lon": "", "source": "",
                         "note": f"named in {c} upgrade label(s); not found in the 2018 grid or the bus workbook"})
    pf.parent.mkdir(parents=True, exist_ok=True)
    with open(pf, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PLANNED_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return pf


def view(requests: list[dict], qd: QueueData) -> dict:
    """Desk map data, cached on the input files' modification times."""
    gp = Path(qd.root).resolve().parent.parent / "data" / "grid" / "grid.json"
    files = [qd.queue_by_area, qd.poi_map, qd.export / "network_upgrades.csv", gp, gp.with_name("planned_subs.csv")]
    stamp = tuple((str(p), p.stat().st_mtime if p.exists() else 0) for p in files)
    if _cache.get("stamp") != stamp:
        P = pois(requests, qd)
        _cache["stamp"] = stamp
        _cache["view"] = {"pois": P, "upgrades": upgrades(qd), "bbox": BBOX,
                          "located": dict(collections.Counter(p["how"] for p in P)), "reference": bool(_ref())}
    return _cache["view"]
