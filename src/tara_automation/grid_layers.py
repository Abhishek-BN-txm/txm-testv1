"""Transmission lines and substations from the North America KMZ layers, cut to a region.

    data/reference/grid/NrthAmrc_Lines.kmz   lines: Name, COMPANY, Voltage_kV, Proposed,
                                             From_Sub, To_Sub, NUM_LINES, Length_mi ...
    data/reference/grid/NrthAmrc_Subs.kmz    substations: Name, Owner, MX_VOLT_KV,
                                             MN_VOLT_KV, Proposed, County, State ...

Both are 2018 snapshots (Velocity Suite style export): facilities built or
planned since then are missing. Licensed reference data: the KMZs and
anything cut from them stay out of git.

    area_states(case_areas)           -> states the case areas' buses are in (the rule)
    extract_states(states, min_kv)    -> every line touching / substation in those states
    write_area_states(area_root, ...) -> research/areas/<ID>/data/grid/grid.json
    (extract / write_area: the older box cut, kept for other uses)
"""

from __future__ import annotations

import html
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from tara_automation.machine import REPO_ROOT

GRID_DIR = REPO_ROOT / "data" / "reference" / "grid"
LINES_KMZ = GRID_DIR / "NrthAmrc_Lines.kmz"
SUBS_KMZ = GRID_DIR / "NrthAmrc_Subs.kmz"

_PM = re.compile(r"<Placemark>(.*?)</Placemark>", re.S)
_DESC = re.compile(r"<description><!\[CDATA\[(.*?)\]\]></description>", re.S)
_FIELD = re.compile(r"<B>([^<]+)</B> = (.*?)(?=<BR>|$)", re.S)
_COORDS = re.compile(r"<coordinates>(.*?)</coordinates>", re.S)


def _kml(kmz: Path) -> str:
    with zipfile.ZipFile(kmz) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
        return z.read(name).decode("utf-8", errors="replace")


def _fields(block: str) -> dict:
    m = _DESC.search(block)
    return {k.strip(): html.unescape(v.strip()) for k, v in _FIELD.findall(m.group(1))} if m else {}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rdp(pts: list, eps: float) -> list:
    """Douglas-Peucker on [lon, lat] points."""
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    n2 = dx * dx + dy * dy
    far, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        x, y = pts[i]
        if n2 == 0:
            d = (x - x1) ** 2 + (y - y1) ** 2
        else:
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / n2))
            d = (x - x1 - t * dx) ** 2 + (y - y1 - t * dy) ** 2
        if d > far:
            far, idx = d, i
    if far <= eps * eps:
        return [pts[0], pts[-1]]
    return _rdp(pts[: idx + 1], eps)[:-1] + _rdp(pts[idx:], eps)


def extract(box: tuple, min_kv: float = 69, eps: float = 0.0008, lines_kmz: Path = LINES_KMZ,
            subs_kmz: Path = SUBS_KMZ) -> dict:
    """box = (lat_min, lon_min, lat_max, lon_max). Lines with any vertex in the box."""
    la0, lo0, la1, lo1 = box
    inside = lambda lon, lat: la0 <= lat <= la1 and lo0 <= lon <= lo1   # noqa: E731
    lines = []
    for block in _PM.findall(_kml(lines_kmz)):
        cm = _COORDS.search(block)
        if not cm:
            continue
        pts = [[float(a), float(b)] for a, b, *_ in (c.split(",") for c in cm.group(1).split())]
        if not any(inside(x, y) for x, y in pts):
            continue
        f = _fields(block)
        kv = _num(f.get("Voltage_kV"))
        if kv is None or kv < min_kv:
            continue
        simple = _rdp(pts, eps)
        lines.append({"n": f.get("Name", ""), "kv": kv, "o": f.get("COMPANY", ""), "p": f.get("Proposed", ""),
                      "f": f.get("From_Sub", ""), "t": f.get("To_Sub", ""), "nl": f.get("NUM_LINES", ""),
                      "ug": f.get("UNDERGRND") == "T", "dc": f.get("Type", "AC") != "AC",
                      "c": [[round(y, 4), round(x, 4)] for x, y in simple]})
    subs = []
    for block in _PM.findall(_kml(subs_kmz)):
        cm = _COORDS.search(block)
        if not cm:
            continue
        x, y = (float(v) for v in cm.group(1).split(",")[:2])
        if not inside(x, y):
            continue
        f = _fields(block)
        kv = _num(f.get("MX_VOLT_KV"))
        if kv is None or kv < min_kv:
            continue
        subs.append({"n": f.get("Name", ""), "kv": kv, "kvmin": _num(f.get("MN_VOLT_KV")), "o": f.get("Owner", ""),
                     "p": f.get("Proposed", ""), "tap": f.get("Tap") == "T", "s": f.get("State", ""),
                     "cty": f.get("County", ""), "ll": [round(y, 5), round(x, 5)]})
    return {"lines": lines, "subs": subs}


def write_area(area_root: Path, box: tuple, min_kv: float = 69) -> Path:
    data = extract(box, min_kv)
    data.update({"box": list(box), "min_kv": min_kv, "extracted": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                 "source": "NrthAmrc_Lines.kmz / NrthAmrc_Subs.kmz (2018 snapshot)"})
    out = Path(area_root) / "data" / "grid" / "grid.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return out


def load_area(area_root: Path) -> dict | None:
    p = Path(area_root) / "data" / "grid" / "grid.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ------------------------------------------------------------------ states --
# Rule (user, 2026-10-22): a study area loads the transmission infrastructure of
# EVERY state its power-flow case areas reach, whole states, not a box.
PAGE = REPO_ROOT / "tools" / "footprint_builder.html"
_STATES: dict = {}


def state_shapes() -> dict:
    """{'AZ': {'b': [lon0, lat0, lon1, lat1], 'r': [[(lon, lat), ...], ...]}} from the state outlines
    embedded in the Footprint Builder (Census 2010 1:500k, simplified; scripts/build_map_borders.py)."""
    if _STATES:
        return _STATES
    raw = PAGE.read_text(encoding="utf-8")
    i, j = raw.index("/*BORDERS-START*/") + len("/*BORDERS-START*/"), raw.index("/*BORDERS-END*/")
    d = json.loads(raw[i:j])
    q = d["q"]
    for s in d["states"]:
        rings = []
        for r in s["r"]:
            x = y = 0
            pts = []
            for k in range(0, len(r), 2):
                x += r[k]; y += r[k + 1]
                pts.append((x / q, y / q))
            rings.append(pts)
        _STATES[s["a"]] = {"b": s["b"], "r": rings, "n": s["n"]}
    return _STATES


def _ring_hit(ring, lon, lat) -> bool:
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < xi + (lat - yi) * (xj - xi) / (yj - yi):
            inside = not inside
        j = i
    return inside


def in_state(abbr: str, lon: float, lat: float) -> bool:
    s = state_shapes()[abbr]
    b = s["b"]
    if not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]):
        return False
    return sum(_ring_hit(r, lon, lat) for r in s["r"]) % 2 == 1      # holes count as outside


def state_at(lon: float, lat: float):
    return next((a for a in state_shapes() if in_state(a, lon, lat)), None)


def area_states(case_areas, min_buses: int = 5) -> dict:
    """States the case areas' buses sit in (coordinate workbook, WECC rows): {'AZ': 2104, 'NM': 31}.
    States with fewer than min_buses buses are dropped as stray coordinates."""
    import csv
    from tara_automation import bus_coords as BC
    wb = BC.DEFAULT_WORKBOOK
    cache = BC.CACHE_DIR / f"bus_area_state_{BC._sha(wb)[:16]}.csv"
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
                la, lo, a = float(r[ix["Y_LAT"]]), float(r[ix["X_LONG"]]), int(r[ix["POWERFLOW_AREAID"]])
            except (TypeError, ValueError):
                continue
            rows.append([r[ix["BUSNUM"]], a, la, lo, state_at(lo, la) or ""])
        cache.parent.mkdir(parents=True, exist_ok=True)
        with open(cache, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["bus", "area", "lat", "lon", "state"])
            w.writerows(rows)
    want = {int(a) for a in case_areas}
    count: dict = {}
    with open(cache, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if int(r["area"]) in want and r["state"]:
                count[r["state"]] = count.get(r["state"], 0) + 1
    return {s: n for s, n in sorted(count.items(), key=lambda kv: -kv[1]) if n >= min_buses}


def extract_states(states, min_kv: float = 0, eps: float = 0.0008, lines_kmz: Path = LINES_KMZ,
                   subs_kmz: Path = SUBS_KMZ) -> dict:
    """Every line with a vertex inside any of the states, every substation in them."""
    shapes = state_shapes()
    states = [s for s in states if s in shapes]
    bx = [min(shapes[s]["b"][0] for s in states), min(shapes[s]["b"][1] for s in states),
          max(shapes[s]["b"][2] for s in states), max(shapes[s]["b"][3] for s in states)]
    in_box = lambda x, y: bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3]   # noqa: E731
    inside = lambda x, y: in_box(x, y) and any(in_state(s, x, y) for s in states)   # noqa: E731
    lines = []
    for block in _PM.findall(_kml(lines_kmz)):
        cm = _COORDS.search(block)
        if not cm:
            continue
        pts = [[float(a), float(b)] for a, b, *_ in (c.split(",") for c in cm.group(1).split())]
        if not any(in_box(x, y) for x, y in pts):
            continue
        f = _fields(block)
        kv = _num(f.get("Voltage_kV"))
        if kv is None or kv < min_kv:
            continue
        if not any(inside(x, y) for x, y in pts):
            continue
        simple = _rdp(pts, eps)
        lines.append({"n": f.get("Name", ""), "kv": kv, "o": f.get("COMPANY", ""), "p": f.get("Proposed", ""),
                      "f": f.get("From_Sub", ""), "t": f.get("To_Sub", ""), "nl": f.get("NUM_LINES", ""),
                      "ug": f.get("UNDERGRND") == "T", "dc": f.get("Type", "AC") != "AC",
                      "o2": f.get("Owner2", ""), "mi": round(_num(f.get("Length_mi")) or 0, 1), "src": f.get("Source", ""),
                      "c": [[round(y, 4), round(x, 4)] for x, y in simple]})
    subs = []
    for block in _PM.findall(_kml(subs_kmz)):
        cm = _COORDS.search(block)
        if not cm:
            continue
        x, y = (float(v) for v in cm.group(1).split(",")[:2])
        f = _fields(block)
        if f.get("State") not in states and not inside(x, y):
            continue
        kv = _num(f.get("MX_VOLT_KV"))
        if kv is not None and kv < min_kv:
            continue
        subs.append({"n": f.get("Name", ""), "kv": kv or 0, "kvmin": _num(f.get("MN_VOLT_KV")), "o": f.get("Owner", ""),
                     "p": f.get("Proposed", ""), "tap": f.get("Tap") == "T", "s": f.get("State", ""),
                     "cty": f.get("County", ""), "ll": [round(y, 5), round(x, 5)]})
    return {"lines": lines, "subs": subs, "box": [bx[1], bx[0], bx[3], bx[2]]}


def write_area_states(area_root: Path, states, min_kv: float = 0, basis: dict | None = None) -> Path:
    data = extract_states(states, min_kv)
    data.update({"states": list(states), "state_basis": basis or {}, "min_kv": min_kv,
                 "extracted": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                 "source": "NrthAmrc_Lines.kmz / NrthAmrc_Subs.kmz (2018 snapshot)"})
    out = Path(area_root) / "data" / "grid" / "grid.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    return out
