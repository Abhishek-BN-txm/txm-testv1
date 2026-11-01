"""Large loads a utility plans to serve (data centers, fabs, mines) and the
transmission built for them: map positions for the desk's Large loads tab.

    research/areas/<AREA>/data/large_loads/
        large_loads.csv       one row per load (or an aggregate row), researched   (input)
        tx_for_load.csv       transmission facilities built for load growth         (input)
        notes.md              totals, tariffs, conflicts between sources, gaps      (input)
        locate_hints.csv      which substation / city each row is placed at         (input, curated)
        large_loads_geo.json  computed positions; the desk only reads this          (generated)

    python scripts/large_loads.py build APS

Positions are never invented. A load sits at its serving substation when one
is named and found (research/areas/<AREA>/data/grid: planned_subs.csv, the grid
substations, then the case-bus workbook, via ingest.aps.geo.place); otherwise
at the centre of its city or area, using coordinates written in
locate_hints.csv with their source; otherwise it is not placed. Every placed
row records how ("substation", "city centre", "area centre", "county"), an
uncertainty in miles and whether the position is estimated. Aggregate rows
are never placed.

Transmission: a chain of substations ("A > B > C") is drawn between its located
nodes, along the grid line's route when the grid has that line (grid_line),
otherwise straight and marked estimated. An unlocated middle node is skipped
(the segment is marked estimated and lists it under "via"). Substation-only
facilities are points. A "cut line" (an existing line looped into a new,
unlocated substation) is drawn only when the grid has the line's route.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import json
import re
from pathlib import Path

FOLDER = ("data", "large_loads")
LOADS = "large_loads.csv"
TX = "tx_for_load.csv"
NOTES = "notes.md"
HINTS = "locate_hints.csv"
GEO = "large_loads_geo.json"

GRID_BG_KV = 230           # background transmission drawn on the map (>= this kV, inside AZ_BOX)
AZ_BOX = (31.2, -115.0, 37.1, -108.9)
DEFAULT_SUB_UNC = 0.5      # miles, a substation found in the grid / bus workbook (mapped coordinates)


# ------------------------------------------------------------------ parsing --
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_MW = re.compile(rf"(~|≈|>|<|about|approx\.?|approximately|up to|over|nearly)?\s*{_NUM}(?:\s*(?:-|–|to)\s*{_NUM})?\s*(MW|GW)\b", re.I)


def parse_mw(text: str) -> dict:
    """First number with MW (or GW) in free text. Conservative: blank when the text says
    the MW is not public, when there is no unit, or when the first figure is generation.
    -> {"mw": float | None, "mw_hi": float | None, "approx": bool}"""
    t = (text or "").strip()
    out = {"mw": None, "mw_hi": None, "approx": False}
    if not t or re.search(r"not public|n/a|not stated|unknown", t, re.I):
        return out
    m = _MW.search(t)
    if not m:
        return out
    tail = t[m.end():m.end() + 40].lower()
    if re.match(r"\s*(of\s+)?(on-site\s+)?(gas\s+)?generation", tail):
        return out
    k = 1000.0 if m.group(4).upper() == "GW" else 1.0
    lo = float(m.group(2).replace(",", "")) * k
    hi = float(m.group(3).replace(",", "")) * k if m.group(3) else None
    approx = bool(m.group(1)) or hi is not None or bool(re.match(r"\s*(-plus|\+|plus)", tail)) \
        or bool(re.search(r"\b(about|approx|roughly|estimated)\b|~", t[:m.start()], re.I))
    out.update(mw=lo, mw_hi=hi, approx=approx)
    return out


STATUS_CLASSES = ["operating", "committed", "planned", "speculative"]


def status_class(status: str, confidence: str = "") -> str:
    """operating / committed (or under construction) / planned / speculative, from the status text."""
    s = (status or "").lower()
    c = (confidence or "").lower()
    if re.search(r"\boperating\b|\bin service\b", s):
        return "operating"
    if re.search(r"under construction|under contract|\bcommitted\b|ramping", s) and "uncommitted" not in s:
        return "committed"
    if re.search(r"speculative|conceptual|concept\b|uncommitted", s + " " + c):
        return "speculative"
    return "planned"


def is_aggregate(row: dict) -> bool:
    return "aggregate" in (row.get("project") or "").lower()


def is_home(row: dict, utility: str) -> bool:
    """The area's own utility ('APS', 'APS (likely; not stated)', 'APS (grid tie)')."""
    return (row.get("utility") or "").strip().upper().startswith(utility.upper())


def urls(text: str) -> list[str]:
    return [u.strip() for u in re.split(r"\s;\s|\s+", text or "") if u.strip().startswith("http")]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]


def _sha(paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        if p.exists():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def parse_notes(md: str) -> dict:
    """notes.md -> {date, sections: {title: markdown}, peak, commitments, tariff}."""
    out = {"date": "", "title": "", "sections": {}, "peak": None, "commitments": None, "tariff": ""}
    m = re.search(r"^#\s+(.+)$", md or "", re.M)
    if m:
        out["title"] = m.group(1).strip()
        d = re.search(r"\d{4}-\d{2}-\d{2}", m.group(1))
        out["date"] = d.group(0) if d else ""
    parts = re.split(r"^##\s+(.+)$", md or "", flags=re.M)
    for i in range(1, len(parts) - 1, 2):
        out["sections"][parts[i].strip()] = parts[i + 1].strip()
    p = re.search(r"([\d,]{4,})\s*MW on (\d{4}-\d{2}-\d{2})", md or "")
    if p:
        out["peak"] = {"mw": float(p.group(1).replace(",", "")), "date": p.group(2)}
    c = re.search(r"New data-center commitments since ([\d-]+)\s*\|\s*([^|]+?)\s*\|", md or "")
    if c:
        out["commitments"] = {"since": c.group(1), "count": c.group(2)}
    for title, body in out["sections"].items():
        if "tariff" in title.lower():
            for line in body.splitlines():
                if "rate case" in line.lower():
                    out["tariff"] = line.lstrip("-* ").strip()
                    break
    return out


# -------------------------------------------------------------- positioning --
def _node(spec: str):
    """'Morgan@500' -> ('Morgan', 500.0); 'Wafer' -> ('Wafer', None)."""
    name, _, kv = spec.partition("@")
    try:
        return name.strip(), float(kv) if kv.strip() else None
    except ValueError:
        return name.strip(), None


def _kvs(text: str) -> list[float]:
    return [float(k) for k in re.findall(r"\d{2,3}", text or "") if 60 <= float(k) <= 765]


def find_sub(name: str, kvs, grid: dict, ref=None):
    """A named substation through ingest.aps.geo.place, tried at each kV given (never kV-free
    when a kV is known, so 'West Phoenix 230' cannot match '(City of) Phoenix').
    -> {lat, lon, label, src, unc_mi, est} or None."""
    from tara_automation.ingest.aps import geo
    tries = list(kvs) if kvs else [None]
    for kv in tries:
        p = geo.place(name, kv, grid, ref)
        if p:
            est = p["src"] == "planned" and "estimated" in (p.get("label") or "")
            est = est or (p.get("conf") or "").lower() == "low"
            return {"lat": round(p["lat"], 5), "lon": round(p["lon"], 5), "label": p.get("label") or name,
                    "src": p["src"], "unc_mi": p.get("unc") or DEFAULT_SUB_UNC, "est": bool(est)}
    return None


_SUB_RE = re.compile(r"^(?:[Nn]ew\s+)?(TS-?\d+|[A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+){0,2})\s+(?:(\d{2,3})(?:/[\d/]+)?\s*kV|substation)")


def serving_subs(text: str) -> list[tuple[str, float | None, bool]]:
    """Named substations at the start of each ';' clause of the serving-substation text.
    -> [(name, kv, inferred)]. 'likely X 230 kV - inferred' gives inferred=True."""
    out = []
    for clause in re.split(r";", text or ""):
        c = clause.strip()
        inferred = bool(re.search(r"\b(likely|inferred|probably)\b", c, re.I))
        c = re.sub(r"^(existing|likely|probably|planned)\s+", "", c, flags=re.I)
        m = _SUB_RE.match(c)
        if m and m.group(1).lower() not in ("dedicated", "existing", "various", "not", "aps", "srp", "tep", "new"):
            out.append((m.group(1), float(m.group(2)) if m.group(2) else None, inferred))
    return out


def locate_load(row: dict, hint: dict | None, grid: dict, ref=None, utility: str = "APS") -> dict:
    """Where one load row goes on the map. Rules, in order:
    aggregate -> not placed; another utility's load -> not placed (context only);
    a named serving substation that is found -> 'substation';
    the hint's city / area centre (coordinates with a source) -> 'city centre' / 'area centre';
    the hint's county -> 'county' (the page puts it at the county centre); else not placed."""
    h = hint or {}
    if is_aggregate(row):
        return {"how": "not placed", "why": "aggregate row (shown in the headline)"}
    if not is_home(row, utility):
        return {"how": "not placed", "why": f"served by {row.get('utility') or 'another utility'} (context only)"}
    tried = []
    if h.get("substations"):
        cands = [(*_node(s), False) for s in h["substations"].split(";") if s.strip()]
    else:
        cands = serving_subs(row.get("nearest_or_serving_substation", ""))
    for name, kv, inferred in cands:
        tried.append(name)
        p = find_sub(name, [kv] if kv else [], grid, ref)
        if p:
            inferred = inferred or bool(re.search(rf"likely\s+{re.escape(name)}|{re.escape(name)}[^;]*inferred",
                                                  row.get("nearest_or_serving_substation", ""), re.I))
            return {"how": "substation", "lat": p["lat"], "lon": p["lon"], "unc_mi": p["unc_mi"],
                    "est": p["est"] or inferred, "sub_est": p["est"], "sub": name, "label": p["label"], "src": p["src"],
                    "inferred": inferred}
    how = (h.get("how") or "").strip().lower()
    if h.get("lat") and h.get("lon"):
        return {"how": how if how in ("city centre", "area centre") else "city centre",
                "lat": float(h["lat"]), "lon": float(h["lon"]), "unc_mi": float(h.get("unc_mi") or 0) or None,
                "est": True, "label": h.get("place", ""), "source": h.get("source", ""),
                "why": f"serving substation {'not found: ' + ', '.join(tried) if tried else 'not named'}"}
    if how == "county" and h.get("place"):
        return {"how": "county", "county": h["place"].replace(" County", "").strip(), "est": True,
                "unc_mi": float(h.get("unc_mi") or 0) or None, "label": h["place"],
                "why": f"serving substation {'not found: ' + ', '.join(tried) if tried else 'not named'}"}
    return {"how": "not placed", "why": h.get("note") or ((f"substation not found ({', '.join(tried)})" if tried
                                                          else "no substation named") + "; no city position given")}


def _chain_parse(text: str) -> list[list[str]]:
    """'El Sol - Contrail (SE corner) - Raptor sw. (2026) - Parkway; ~9 mi' -> [['El Sol', ...]]."""
    head = re.split(r";", text or "")[0]
    head = re.sub(r"\(.*?\)", " ", head)
    names = [re.sub(r"\b(sw|sub|substation)\.?$", "", n.strip(), flags=re.I).strip() for n in re.split(r"\s+[-–]\s+", head)]
    names = [n for n in names if n and len(n) < 30 and re.match(r"^[A-Z]", n) and not re.search(r"\d+\s*mi\b", n)]
    return [names] if len(names) >= 2 else []


def locate_tx(row: dict, hint: dict | None, grid: dict, ref=None) -> dict:
    """Geometry of one transmission facility: segments (chains), points (substations), cut lines."""
    from tara_automation.ingest.aps import geo
    h = hint or {}
    kvs = _kvs(row.get("kv", ""))
    out = {"segments": [], "points": [], "missing": [], "why": ""}
    if h:
        chains = [[n.strip() for n in c.split(">") if n.strip()] for c in (h.get("lines") or "").split("|") if c.strip()]
        points = [p.strip() for p in (h.get("substations") or "").split(";") if p.strip()]
        cuts = [[n.strip() for n in c.split(">") if n.strip()] for c in (h.get("cut_lines") or "").split("|") if c.strip()]
    else:
        chains, points, cuts = _chain_parse(row.get("endpoints_substations", "")), [], []
        if not chains and re.search(r"substation", row.get("facility", ""), re.I):
            m = re.match(r"\s*([A-Z][\w-]*(?:\s[A-Z][\w-]*)?)", row["facility"])
            points = [m.group(1)] if m else []
    cache: dict = {}

    def loc(spec):
        name, kv = _node(spec)
        if name not in cache:
            cache[name] = find_sub(name, [kv] if kv else kvs, grid, ref)
            if cache[name] is None and name not in out["missing"]:
                out["missing"].append(name)
        return name, cache[name]

    for chain in chains:
        nodes = [loc(s) for s in chain]
        prev, via = None, []
        for name, p in nodes:
            if not p:
                if prev:
                    via.append(name)
                continue
            if prev:
                a, pa = prev
                route = None
                if not via:
                    for kv in (kvs or [None]):
                        g = geo.grid_line(a, name, kv, grid)
                        if g:
                            route = [[round(x, 4), round(y, 4)] for x, y in g[0]["c"]]
                            # orient from a to b
                            if _d(route[0], (pa["lat"], pa["lon"])) > _d(route[-1], (pa["lat"], pa["lon"])):
                                route.reverse()
                            break
                out["segments"].append({"from": a, "to": name, "route": "grid" if route else "straight",
                                        "c": route or [[pa["lat"], pa["lon"]], [p["lat"], p["lon"]]],
                                        "via": via, "est": bool(not route or via or pa["est"] or p["est"])})
            prev, via = (name, p), []
    for spec in points:
        name, p = loc(spec)
        if p:
            out["points"].append({"name": name, **{k: p[k] for k in ("lat", "lon", "label", "unc_mi", "est")}})
    for chain in cuts:
        if len(chain) < 2:
            continue
        (a, kva), (b, kvb) = _node(chain[0]), _node(chain[1])
        g = None
        for kv in ([kva] if kva else kvs or [None]):
            g = geo.grid_line(a, b, kv, grid)
            if g:
                break
        if g:
            out["segments"].append({"from": a, "to": b, "route": "cut line", "via": [],
                                    "c": [[round(x, 4), round(y, 4)] for x, y in g[0]["c"]], "est": True})
    placed = bool(out["segments"] or out["points"])
    out["how"] = ("line" if any(s["route"] != "cut line" for s in out["segments"]) else
                  "point" if out["points"] else "cut line" if out["segments"] else "not placed")
    if not placed:
        out["why"] = (f"not located: {', '.join(out['missing'])}" if out["missing"] else "no substation named")
    out["est"] = placed and all(s["est"] for s in out["segments"]) and all(p["est"] for p in out["points"])
    return out


def _d(a, b) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


# --------------------------------------------------------------------- build --
def folder(area_root: Path) -> Path:
    return Path(area_root).joinpath(*FOLDER)


def _hints(d: Path) -> tuple[dict, dict]:
    loads, tx = {}, {}
    for r in _read_csv(d / HINTS):
        (loads if r.get("kind") == "load" else tx)[r.get("name", "")] = r
    return loads, tx


def _grid_for(area_root: Path):
    """The grid + planned substations of the area (geo._grid) and the case-bus reference (geo._ref)."""
    from tara_automation.ingest.aps import QueueData, geo
    qroot = Path(area_root) / "data" / "queue"
    g = geo._grid(QueueData(qroot))
    try:
        ref = geo._ref()
    except Exception:  # noqa: BLE001 - no workbook on this machine: grid + planned only
        ref = {}
    return g, ref


def _bg_lines(grid: dict) -> list[dict]:
    out = []
    for ln in grid.get("lines", []):
        if (ln.get("kv") or 0) < GRID_BG_KV or not ln.get("c"):
            continue
        c = ln["c"]
        if not any(AZ_BOX[0] <= la <= AZ_BOX[2] and AZ_BOX[1] <= lo <= AZ_BOX[3] for la, lo in c):
            continue
        out.append({"kv": ln["kv"], "n": ln.get("n", ""), "p": ln.get("p", ""),
                    "c": [[round(la, 3), round(lo, 3)] for la, lo in c]})
    return out


def build(area_root: Path, utility: str | None = None, grid: dict | None = None, ref=None) -> dict:
    """Compute positions for every load and transmission row; write large_loads_geo.json."""
    area_root = Path(area_root)
    d = folder(area_root)
    utility = utility or area_root.name
    if grid is None:
        grid, ref = _grid_for(area_root)
    hl, ht = _hints(d)
    hint = lambda hs, name: hs.get(name) or next((v for k, v in hs.items() if k and name.lower().startswith(k.lower())), None)  # noqa: E731
    loads, txs = _read_csv(d / LOADS), _read_csv(d / TX)
    lo = [{"project": r["project"], **locate_load(r, hint(hl, r["project"]), grid, ref, utility)} for r in loads]
    tx = [{"facility": r["facility"], **locate_tx(r, hint(ht, r["facility"]), grid, ref)} for r in txs]
    subs: dict = {}
    for r, g in zip(loads, lo):
        if g["how"] == "substation":
            subs.setdefault(g["sub"], {"name": g["sub"], "lat": g["lat"], "lon": g["lon"], "label": g["label"],
                                       "unc_mi": g["unc_mi"], "est": g["sub_est"], "serves": []})["serves"].append(r["project"])
    count = {"loads": {}, "tx": {}}
    for g in lo:
        count["loads"][g["how"]] = count["loads"].get(g["how"], 0) + 1
    for g in tx:
        count["tx"][g["how"]] = count["tx"].get(g["how"], 0) + 1
    out = {"area": area_root.name, "built_at": _dt.datetime.now().isoformat(timespec="seconds"),
           "inputs_sha": _sha([d / LOADS, d / TX, d / HINTS]), "loads": lo, "tx": tx,
           "subs": list(subs.values()), "grid": _bg_lines(grid), "counts": count,
           "rules": __doc__.split("Positions are never invented.")[1].strip().split("\n\n")[0]}
    (d / GEO).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return out


# --------------------------------------------------------------------- links --
_GENERIC = {"arizona", "multiple", "various", "named", "data", "center", "centers", "infrastructure", "landowner"}


def _owner_key(row: dict) -> str:
    o = row.get("owner_developer") or ""
    if re.match(r"\s*(not\b|tbd|n/a|multiple|various)", o, re.I):
        return ""
    m = re.search(r"[A-Za-z][\w-]{3,}", o)
    return m.group(0) if m and m.group(0).lower() not in _GENERIC else ""


def _city_key(row: dict) -> str:
    return re.split(r"[,(]", row.get("city_area") or "")[0].strip()


def tx_links(loads: list[dict], txs: list[dict], geo: dict | None = None) -> dict[int, list[int]]:
    """Which transmission rows serve which load rows (by index). A facility serves a load when its
    'loads_served' names the load's owner (plus its city when that owner has several rows), or when
    the facility's name contains one of the load's serving substations."""
    owners: dict = {}
    for r in loads:
        k = _owner_key(r).lower()
        if k:
            owners[k] = owners.get(k, 0) + 1
    geo_sub = {g["project"]: g.get("sub") for g in (geo or {}).get("loads", [])}
    out: dict[int, list[int]] = {}
    for i, r in enumerate(loads):
        if is_aggregate(r):
            continue
        ok, city = _owner_key(r), _city_key(r)
        subs = {n for n, _, _ in serving_subs(r.get("nearest_or_serving_substation", ""))}
        if geo_sub.get(r.get("project")):
            subs.add(geo_sub[r["project"]])
        hits = []
        for j, t in enumerate(txs):
            served = (t.get("loads_served") or "").lower()
            by_owner = bool(ok) and ok.lower() in served and (owners.get(ok.lower(), 0) < 2 or city.lower() in served)
            by_sub = any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", t.get("facility", ""), re.I) for n in subs)
            if by_owner or by_sub:
                hits.append(j)
        if hits:
            out[i] = hits
    return out


# ---------------------------------------------------------------------- view --
def view(area_root: Path, utility: str | None = None) -> dict | None:
    """What the desk's Large loads tab shows; None when the area has no data/large_loads folder."""
    area_root = Path(area_root)
    d = folder(area_root)
    if not d.is_dir():
        return None
    utility = utility or area_root.name
    try:
        loads, txs = _read_csv(d / LOADS), _read_csv(d / TX)
        notes = parse_notes((d / NOTES).read_text(encoding="utf-8")) if (d / NOTES).exists() else parse_notes("")
        geo = json.loads((d / GEO).read_text(encoding="utf-8")) if (d / GEO).exists() else None
        for r in loads:
            r.update(parse_mw(r.get("mw_as_stated", "")))
            r["class"] = status_class(r.get("status", ""), r.get("confidence", ""))
            r["aggregate"] = is_aggregate(r)
            r["home"] = is_home(r, utility)
            r["urls"] = urls(r.get("source_urls", ""))
        for r in txs:
            r["urls"] = urls(r.get("source_urls", ""))
        agg = [r for r in loads if r["aggregate"] and r["home"]]
        committed = next((r for r in agg if "committed" in r["project"].lower() and "uncommitted" not in r["project"].lower()), None)
        queue = next((r for r in agg if "uncommitted" in r["project"].lower()), None)
        program = next((r for r in loads if "subscription" in r["project"].lower()), None)
        head = {"committed": committed and {k: committed.get(k, "") for k in ("mw", "mw_hi", "approx", "mw_as_stated", "full_load_year")},
                "uncommitted": queue and {k: queue.get(k, "") for k in ("mw", "mw_hi", "approx", "mw_as_stated", "status")},
                "program": program and {k: program.get(k, "") for k in ("project", "mw", "approx", "mw_as_stated", "first_load_year", "status")},
                "peak": notes["peak"], "commitments": notes["commitments"], "tariff": notes["tariff"],
                "researched": notes["date"]}
        for i, js in tx_links(loads, txs, geo).items():
            loads[i]["tx_idx"] = js
            for j in js:
                txs[j].setdefault("load_idx", []).append(i)
        stale = bool(geo) and geo.get("inputs_sha") != _sha([d / LOADS, d / TX, d / HINTS])
        return {"loads": loads, "tx": txs, "notes": notes, "headline": head, "geo": geo, "stale": stale,
                "utility": utility, "folder": str(d)}
    except Exception as e:  # noqa: BLE001 - a broken folder must not break the area page
        return {"error": f"{type(e).__name__}: {e}"}
