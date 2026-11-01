"""Footprint compiler: footprint.yaml -> TARA study data file (.sub) + bus tags.

A *footprint* says where a study looks: the study area (contingencies and
monitoring), the sub-areas results are reported by, and the source / sink
groups used for transfers. It is written as data (YAML) and resolved against
the case's own bus table, so changing the geography of a study never means
hand-editing a .sub file.

Every group is resolved in Python to an exact set of buses, then written with
the two subsystem statements already proven on the engine (runs 54-64):
``AREA n`` when the set is exactly whole areas, otherwise ``BUS n`` one per
line. Manual 2.2 traps this deliberately avoids:

- ``AREAS a b`` / ``BUSES a b`` are RANGES (a through b), not lists;
- ``AREA n`` + ``KVRANGE`` without a ``JOIN`` block is an OR, not an AND.

Selector grammar (one group):

    areas / zones / owners / buses : ints or "a-b" ranges   } unioned into
    near: {bus, hops, via_kv_min}  (or a list of them)      } the candidate
    polygon: {file: x.geojson|x.kml} or [[lat, lon], ...]   } set
    groups: [other group names]                             }
    kv_min / kv_max / name_regex                            -> then filter it
    exclude: <selector>                                     -> then remove

A top-level ``exclude`` applies to every group.

Polygon rule (user-specified, 2026-10-11):

    1. Bus locations come from bus_coords.csv (primary), then the case's own
       vetted lat/lon for buses the file does not cover (switchable).
    2. Every located bus inside the polygon is included.
    3. From the included buses, the selection floods through neighbouring
       buses that have NO location, including each one, across lines and
       transformers. It stops at a located bus outside the polygon.

No position is ever estimated. Cases carry coordinates only partly (EAGL 30HS:
34 % of buses), so the flood is what fills the gaps. The report shows how
much of each polygon came from it.

Case coordinates are vetted first. TARA flags none as suspicious, yet on run 25
677 of 10060 were junk (longitude typed into the latitude field with 0.5 /
1.0 as longitude; latitude == longitude). `plausible_coord` rejects exactly
those; they are counted in the report, never used.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SET_KEYS = ("areas", "zones", "owners", "buses", "near", "polygon", "groups")
FILTER_KEYS = ("kv_min", "kv_max", "name_regex")
SELECTOR_KEYS = set(SET_KEYS) | set(FILTER_KEYS) | {"exclude"}
SECTIONS = ("sub_areas", "sources", "sinks")
TOP_KEYS = {"version", "study_area", "exclude", "monitor", "con", "mon",
            "coordinates", "base", *SECTIONS}
BASE_KEYS = ("sub", "con", "mon")
APPEND_MARK = "// ===== appended by tara_automation.footprint"
COORD_KEYS = {"file", "interconnect", "use_case_coords", "max_flood_hops"}


def plausible_coord(lat: float, lon: float) -> bool:
    """In range, not near zero, and not the same number typed twice."""
    return (-90 <= lat <= 90 and -180 <= lon <= 180
            and abs(lat) >= 1 and abs(lon) >= 1 and abs(lat - lon) > 0.01)


# Subsystem names are written bare (as in every proven .sub so far), so keep
# them to characters that need no quoting.
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
TEMPLATES = Path(__file__).parent / "commfile" / "templates"


class FootprintError(ValueError):
    """The footprint cannot be compiled as written (bad spec or bad network)."""


# ------------------------------------------------------------- network -----
@dataclass
class Bus:
    number: int
    name: str
    kv: float
    area: int
    zone: int
    owner: int
    lat: float | None = None
    lon: float | None = None
    located_by: str | None = None    # file | case


@dataclass
class Network:
    """Buses and in-service connectivity of one solved case."""

    buses: dict[int, Bus]
    adjacency: dict[int, set[int]]
    source: str = ""          # where it came from, for the report
    located: dict | None = None      # summary once locate() has run
    rejected_coords: list[int] = field(default_factory=list)   # junk case lat/lon

    def area_buses(self, area: int) -> set[int]:
        return {n for n, b in self.buses.items() if b.area == area}


def _table_rows(path: Path, first_header: str) -> tuple[list[str], list[list[str]]]:
    """A TARA report CSV -> (header, data rows). Two banner lines precede the
    header; data rows start with a bus number."""
    header: list[str] | None = None
    rows: list[list[str]] = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for p in csv.reader(f):
            cells = [c.strip() for c in p]
            if header is None:
                if cells and cells[0] == first_header:
                    header = cells
                continue
            if cells and re.fullmatch(r"\d+", cells[0]):
                rows.append(cells)
    if header is None:
        raise FootprintError(f"{path.name}: header row '{first_header}' not found")
    return header, rows


def _expect(path: Path, header: list[str], wanted: dict[int, str]) -> None:
    """Fail loudly if TARA moved a column — positional parsing is only safe
    while the header says what we think it says."""
    for i, name in wanted.items():
        if i >= len(header) or header[i] != name:
            got = header[i] if i < len(header) else "<missing>"
            raise FootprintError(
                f"{path.name}: column {i} is '{got}', expected '{name}'. "
                f"The report layout changed; update footprint.py.")


def load_network(results_dir: Path) -> Network:
    """Read BusData.csv + BranchData.csv from a basecase run's results.

    + TransfmrData.csv.

    BranchData holds only non-transformer branches (its row count matches
    CaseSum's NonTranBrn; its `Mt` column is the metered end, not a
    transformer flag). Transformers come from TransfmrData, which has no
    in-service column (its Status is the tap-regulation state), so every
    transformer counts as a connection. Lines connect only when St = 1."""
    results_dir = Path(results_dir)
    bus_csv = results_dir / "BusData.csv"
    br_csv = results_dir / "BranchData.csv"
    tx_csv = results_dir / "TransfmrData.csv"
    for p in (bus_csv, br_csv, tx_csv):
        if not p.exists():
            raise FootprintError(f"{p} not found — the footprint needs a basecase "
                                 f"run's results (run the basecase stage first)")

    h, rows = _table_rows(bus_csv, "Bus#")
    _expect(bus_csv, h, {0: "Bus#", 1: "Bus Name", 2: "Volt", 3: "Area",
                         4: "Zone", 15: "Ownr"})
    # Coordinates are optional columns: used only when the header has them.
    lat_i = h.index("Latitude") if "Latitude" in h else None
    lon_i = h.index("Longitude") if "Longitude" in h else None
    buses = {}
    rejected: list[int] = []
    for r in rows:
        n = int(r[0])
        b = Bus(n, r[1], float(r[2]), int(r[3]), int(r[4]), int(r[15]))
        if lat_i is not None and lon_i is not None:
            try:
                lat, lon = float(r[lat_i]), float(r[lon_i])
            except (ValueError, IndexError):
                lat = lon = 0.0
            # 0/0 is how TARA writes "no coordinate"
            if abs(lat) > 0.01 or abs(lon) > 0.01:
                if plausible_coord(lat, lon):
                    b.lat, b.lon, b.located_by = lat, lon, "case"
                else:
                    rejected.append(n)
        buses[n] = b

    h, rows = _table_rows(br_csv, "Fr Bus")
    _expect(br_csv, h, {0: "Fr Bus", 5: "To Bus", 13: "St"})
    adj: dict[int, set[int]] = {n: set() for n in buses}

    def link(a: int, b: int) -> None:
        if a in adj and b in adj and a != b:
            adj[a].add(b)
            adj[b].add(a)

    for r in rows:
        if r[13] == "1":
            link(int(r[0]), int(r[5]))

    h, rows = _table_rows(tx_csv, "Bus#")
    _expect(tx_csv, h, {0: "Bus#", 2: "Volt", 3: "Bus#", 5: "Volt"})
    for r in rows:
        if re.fullmatch(r"\d+", r[3]):
            link(int(r[0]), int(r[3]))

    # Cross-check the bus count against CaseSum when it is there.
    case_sum = results_dir / "CaseSum.csv"
    if case_sum.exists():
        from tara_automation.comm_reports import parse_case_sum
        expected = parse_case_sum(case_sum).get("n_buses")
        if expected and expected != len(buses):
            raise FootprintError(f"BusData has {len(buses)} buses but CaseSum "
                                 f"reports {expected}; the parse is incomplete")
    return Network(buses, adj, source=str(results_dir.resolve()),
                   rejected_coords=rejected)


# ---------------------------------------------------------- coordinates ----
def read_coords_file(path: Path) -> dict[int, tuple[float, float]]:
    """bus_coords.csv: a header naming bus, lat and lon columns (any case,
    extra columns allowed), then one row per bus."""
    out: dict[int, tuple[float, float]] = {}
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        header = [c.strip().lower() for c in next(reader, [])]
        try:
            ib = next(i for i, c in enumerate(header) if c in ("bus", "bus#", "busnumber"))
            ia = next(i for i, c in enumerate(header) if c in ("lat", "latitude"))
            io = next(i for i, c in enumerate(header) if c in ("lon", "long", "longitude"))
        except StopIteration:
            raise FootprintError(f"{Path(path).name}: needs a header with bus, "
                                 f"lat and lon columns") from None
        for k, r in enumerate(reader, start=2):
            if not r or not "".join(r).strip():
                continue
            try:
                n, lat, lon = int(r[ib]), float(r[ia]), float(r[io])
            except (ValueError, IndexError):
                raise FootprintError(f"{Path(path).name} row {k}: expected "
                                     f"bus, lat, lon numbers") from None
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise FootprintError(f"{Path(path).name} row {k}: lat/lon out of range")
            out[n] = (lat, lon)
    return out


def locate(net: Network, coords_file: Path | None = None,
           use_case_coords: bool = True, interconnect: str = "WECC",
           cache_dir: Path | None = None) -> dict:
    """Locate buses. Nothing is estimated.

    `coords_file` is either the reference workbook (.xlsx, see bus_coords.py:
    named-substation rows > case > estimated rows) or a plain bus,lat,lon CSV
    (which overrides the case). The case's own vetted lat/lon fills the rest
    unless use_case_coords is False."""
    if net.located is not None:
        return net.located
    if not use_case_coords:
        for b in net.buses.values():
            if b.located_by == "case":
                b.lat = b.lon = b.located_by = None
    unknown_in_file = 0
    reference = None
    if coords_file and Path(coords_file).suffix.lower() in (".xlsx", ".xlsm"):
        from tara_automation import bus_coords
        kw = {"cache_dir": cache_dir} if cache_dir is not None else {}
        try:
            ref = bus_coords.load_reference(Path(coords_file), interconnect, **kw)
        except bus_coords.BusCoordsError as e:
            raise FootprintError(str(e)) from None
        reference = bus_coords.apply_reference(net, ref)
    elif coords_file:
        for n, (lat, lon) in read_coords_file(coords_file).items():
            b = net.buses.get(n)
            if b is None:
                unknown_in_file += 1
                continue
            b.lat, b.lon, b.located_by = lat, lon, "file"

    counts: dict[str, int] = {}
    for b in net.buses.values():
        key = b.located_by or "none"
        counts[key] = counts.get(key, 0) + 1
    net.located = {"by_source": counts, "use_case_coords": use_case_coords,
                   "case_coords_rejected": len(net.rejected_coords),
                   "case_coords_rejected_sample": net.rejected_coords[:20],
                   "coords_file": str(coords_file) if coords_file else None,
                   "coords_file_unknown_buses": unknown_in_file,
                   "interconnect": interconnect if reference is not None else None,
                   "reference": reference}
    return net.located


# -------------------------------------------------------------- polygons ---
Ring = list[tuple[float, float]]          # (lat, lon) vertices


@dataclass
class Polygon:
    outer: Ring
    holes: list[Ring] = field(default_factory=list)

    def contains(self, lat: float, lon: float) -> bool:
        return _in_ring(self.outer, lat, lon) and not any(
            _in_ring(h, lat, lon) for h in self.holes)


def _in_ring(ring: Ring, lat: float, lon: float) -> bool:
    """Ray casting in the lat/lon plane (fine at regional scale)."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (yi > lat) != (yj > lat):
            if lon < xi + (lat - yi) * (xj - xi) / (yj - yi):
                inside = not inside
        j = i
    return inside


def _ring(points, where: str, lonlat: bool) -> Ring:
    ring: Ring = []
    for p in points:
        try:
            a, b = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise FootprintError(f"{where}: vertex {p!r} is not a coordinate pair") from None
        lat, lon = (b, a) if lonlat else (a, b)
        if abs(lat) > 90 and abs(lon) <= 90:
            raise FootprintError(f"{where}: vertex {p!r} looks like [lon, lat]; "
                                 f"inline polygons are written [lat, lon]")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise FootprintError(f"{where}: vertex {p!r} is out of range")
        ring.append((lat, lon))
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3:
        raise FootprintError(f"{where}: a polygon needs at least 3 vertices")
    return ring


def _geojson_polygons(doc, where: str) -> list[Polygon]:
    t = doc.get("type") if isinstance(doc, dict) else None
    if t == "FeatureCollection":
        return [p for f in doc.get("features", []) for p in _geojson_polygons(f, where)]
    if t == "Feature":
        return _geojson_polygons(doc.get("geometry") or {}, where)
    if t == "Polygon":
        rings = doc["coordinates"]
        return [Polygon(_ring(rings[0], where, True),
                        [_ring(r, where, True) for r in rings[1:]])]
    if t == "MultiPolygon":
        return [Polygon(_ring(p[0], where, True), [_ring(r, where, True) for r in p[1:]])
                for p in doc["coordinates"]]
    if t == "GeometryCollection":
        return [p for g in doc.get("geometries", []) for p in _geojson_polygons(g, where)]
    return []


def _kml_coords(el) -> list[list[float]]:
    text = next((c.text for c in el.iter() if c.tag.endswith("coordinates")), "") or ""
    return [[float(v) for v in tok.split(",")[:2]] for tok in text.split()]


def _kml_polygons(path: Path, where: str) -> list[Polygon]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise FootprintError(f"{where}: {path.name} is not valid KML: {e}") from None
    out = []
    for poly in (e for e in root.iter() if e.tag.endswith("Polygon")):
        outer = next((e for e in poly if e.tag.endswith("outerBoundaryIs")), None)
        if outer is None:
            continue
        holes = [e for e in poly if e.tag.endswith("innerBoundaryIs")]
        out.append(Polygon(_ring(_kml_coords(outer), where, True),
                           [_ring(_kml_coords(h), where, True) for h in holes]))
    return out


def parse_polygons(value, base_dir: Path, where: str) -> list[Polygon]:
    """`{file: x.geojson|x.json|x.kml}`, one inline ring [[lat, lon], ...], or
    a list of inline rings."""
    if isinstance(value, dict):
        if set(value) != {"file"}:
            raise FootprintError(f"{where}: polygon takes {{file: <path>}} or a "
                                 f"list of [lat, lon] points")
        f = Path(value["file"])
        f = f if f.is_absolute() else base_dir / f
        if not f.exists():
            raise FootprintError(f"{where}: polygon file {f} not found "
                                 f"(relative paths are read from the YAML's folder)")
        if f.suffix.lower() in (".geojson", ".json"):
            try:
                polys = _geojson_polygons(json.loads(f.read_text(encoding="utf-8")), where)
            except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
                raise FootprintError(f"{where}: {f.name} is not usable GeoJSON: {e}") from None
        elif f.suffix.lower() == ".kml":
            polys = _kml_polygons(f, where)
        else:
            raise FootprintError(f"{where}: polygon files must be .geojson, .json or .kml")
        if not polys:
            raise FootprintError(f"{where}: {f.name} contains no polygons")
        return polys
    if isinstance(value, list) and value:
        nested = isinstance(value[0], list) and value[0] and isinstance(value[0][0], list)
        rings = value if nested else [value]
        return [Polygon(_ring(r, f"{where} polygon {i + 1}", False))
                for i, r in enumerate(rings)]
    raise FootprintError(f"{where}: polygon takes {{file: <path>}} or a list of "
                         f"[lat, lon] points")


# ------------------------------------------------------------ selectors ----
def _ints(value, what: str) -> set[int]:
    """[14, "16-19", 21] -> {14, 16, 17, 18, 19, 21}."""
    if value is None:
        return set()
    items = value if isinstance(value, list) else [value]
    out: set[int] = set()
    for v in items:
        if isinstance(v, bool):
            raise FootprintError(f"{what}: {v!r} is not a number")
        if isinstance(v, int):
            out.add(v)
            continue
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(v))
        if not m:
            raise FootprintError(f"{what}: {v!r} is not a number or 'a-b' range")
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            raise FootprintError(f"{what}: range {v!r} runs backwards")
        out.update(range(lo, hi + 1))
    return out


def _near(net: Network, spec, where: str) -> set[int]:
    """Buses within `hops` branches of `bus`, optionally walking only through
    buses at or above `via_kv_min` (so a 230 kV neighbourhood does not leak
    through the distribution system)."""
    specs = spec if isinstance(spec, list) else [spec]
    out: set[int] = set()
    for s in specs:
        if not isinstance(s, dict) or "bus" not in s or "hops" not in s:
            raise FootprintError(f"{where}.near needs {{bus, hops}}, got {s!r}")
        unknown = set(s) - {"bus", "hops", "via_kv_min"}
        if unknown:
            raise FootprintError(f"{where}.near: unknown key(s) {sorted(unknown)}")
        hops = int(s["hops"])
        via = float(s.get("via_kv_min", 0) or 0)
        for start in _ints(s["bus"], f"{where}.near.bus"):
            if start not in net.buses:
                raise FootprintError(f"{where}.near: bus {start} is not in the case")
            seen = {start}
            frontier = deque([(start, 0)])
            while frontier:
                n, d = frontier.popleft()
                if d == hops:
                    continue
                for m in net.adjacency[n]:
                    if m not in seen and net.buses[m].kv >= via:
                        seen.add(m)
                        frontier.append((m, d + 1))
            out |= seen
    return out


def flood_polygon(net: Network, polys: list[Polygon],
                  max_hops: int | None = None) -> dict[str, set[int]]:
    """The polygon rule. Located buses inside the polygon seed the selection;
    it then spreads through neighbouring buses that have no location, and
    stops at any located bus outside the polygon.

    Returns inside (located, in the polygon), flooded (unlocated, reached),
    stops (located outside, where the flood stopped) and capped (unlocated
    buses left unvisited because max_hops was reached)."""
    inside = {n for n, b in net.buses.items()
              if b.lat is not None and any(p.contains(b.lat, b.lon) for p in polys)}
    flooded: set[int] = set()
    stops: set[int] = set()
    capped: set[int] = set()
    frontier = deque((n, 0) for n in sorted(inside))
    while frontier:
        n, d = frontier.popleft()
        for m in sorted(net.adjacency[n]):
            if m in inside or m in flooded:
                continue
            if net.buses[m].lat is not None:
                stops.add(m)          # located outside the polygon: the edge
            elif max_hops is not None and d >= max_hops:
                capped.add(m)
            else:
                flooded.add(m)
                frontier.append((m, d + 1))
    return {"inside": inside, "flooded": flooded, "stops": stops,
            "capped": capped - flooded}


class _Resolver:
    def __init__(self, net: Network, groups: dict[str, dict], global_exclude,
                 base_dir: Path = Path("."), max_flood_hops: int | None = None):
        self.net = net
        self.base_dir = base_dir
        self.max_flood_hops = max_flood_hops
        # group -> {"inside", "flooded", "stops", "capped"} bus sets
        self.polygon_detail: dict[str, dict[str, set[int]]] = {}
        self.groups = groups
        self.global_exclude = global_exclude
        self.cache: dict[str, set[int]] = {}
        self.stack: list[str] = []

    def group(self, name: str) -> set[int]:
        if name in self.cache:
            return self.cache[name]
        if name not in self.groups:
            raise FootprintError(f"group '{name}' is referenced but not defined")
        if name in self.stack:
            raise FootprintError("circular group reference: "
                                 + " -> ".join(self.stack + [name]))
        self.stack.append(name)
        buses = self.select(self.groups[name], name)
        if self.global_exclude:
            buses -= self.select(self.global_exclude, "exclude")
        self.stack.pop()
        if not buses:
            raise FootprintError(f"group '{name}' resolves to no buses")
        self.cache[name] = buses
        return buses

    def select(self, sel, where: str) -> set[int]:
        if not isinstance(sel, dict):
            raise FootprintError(f"{where}: expected a mapping, got {sel!r}")
        unknown = set(sel) - SELECTOR_KEYS - {"name"}
        if unknown:
            raise FootprintError(f"{where}: unknown key(s) {sorted(unknown)}; "
                                 f"valid: {sorted(SELECTOR_KEYS)}")
        net = self.net
        if not any(k in sel for k in SET_KEYS):
            # a pure filter (e.g. exclude: {name_regex: ...}) applies to all buses
            if where.endswith("exclude") and any(k in sel for k in FILTER_KEYS):
                chosen = set(net.buses)
            else:
                raise FootprintError(f"{where}: selects nothing — give at least "
                                     f"one of {list(SET_KEYS)}")
        else:
            chosen = set()
        for key, attr in (("areas", "area"), ("zones", "zone"), ("owners", "owner")):
            wanted = _ints(sel.get(key), f"{where}.{key}")
            if not wanted:
                continue
            present = {getattr(b, attr) for b in net.buses.values()}
            missing = sorted(wanted - present)
            # A range may span unused numbers; only a fully absent request is
            # an error, because that is always a typo.
            if missing and not (wanted & present):
                raise FootprintError(f"{where}.{key}: none of {missing} exist in the case")
            chosen |= {n for n, b in net.buses.items() if getattr(b, attr) in wanted}
        listed = _ints(sel.get("buses"), f"{where}.buses")
        missing = sorted(listed - set(net.buses))
        if missing:
            raise FootprintError(f"{where}.buses: not in the case: {missing[:10]}")
        chosen |= listed
        if "near" in sel:
            chosen |= _near(net, sel["near"], where)
        if "polygon" in sel:
            if net.located is None:
                raise FootprintError(f"{where}: polygon used before bus locations "
                                     f"were resolved")
            polys = parse_polygons(sel["polygon"], self.base_dir, where)
            detail = flood_polygon(net, polys, self.max_flood_hops)
            if not where.endswith("exclude"):
                self.polygon_detail[where] = detail
            chosen |= detail["inside"] | detail["flooded"]
        for g in sel.get("groups") or []:
            chosen |= self.group(str(g))

        if "kv_min" in sel:
            chosen = {n for n in chosen if net.buses[n].kv >= float(sel["kv_min"])}
        if "kv_max" in sel:
            chosen = {n for n in chosen if net.buses[n].kv <= float(sel["kv_max"])}
        if "name_regex" in sel:
            rx = re.compile(str(sel["name_regex"]))
            chosen = {n for n in chosen if rx.search(net.buses[n].name)}
        if "exclude" in sel:
            chosen -= self.select(sel["exclude"], f"{where}.exclude")
        return chosen


# --------------------------------------------------------------- compile ---
@dataclass
class Group:
    name: str
    role: str                 # study_area | sub_area | source | sink
    buses: set[int]
    areas_exact: list[int] = field(default_factory=list)   # non-empty -> AREA form

    @property
    def form(self) -> str:
        return "AREA" if self.areas_exact else "BUS"


@dataclass
class Footprint:
    spec_path: Path
    spec_sha: str
    network: Network
    groups: list[Group]
    monitor_kv: tuple[float, float] | None
    con: str | None           # "auto" | path | None
    mon: str | None
    warnings: list[str]
    polygon_notes: list[str] = field(default_factory=list)
    # The user's own study files the footprint is appended to (never replaced)
    base: dict[str, Path] = field(default_factory=dict)
    base_names: set[str] = field(default_factory=set)   # subsystems the base .sub defines
    # group -> {inside, flooded, stops, capped}: how each polygon selected its buses
    polygon_detail: dict[str, dict[str, set[int]]] = field(default_factory=dict)

    def get(self, name: str) -> Group:
        return next(g for g in self.groups if g.name == name)

    @property
    def study_area(self) -> Group:
        return next(g for g in self.groups if g.role == "study_area")


def load_spec(path: Path) -> dict:
    path = Path(path)
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise FootprintError(f"{path.name}: not valid YAML: {e}") from e
    if not isinstance(spec, dict):
        raise FootprintError(f"{path.name}: expected a mapping at the top level")
    unknown = set(spec) - TOP_KEYS
    if unknown:
        raise FootprintError(f"{path.name}: unknown top-level key(s) "
                             f"{sorted(unknown)}; valid: {sorted(TOP_KEYS)}")
    if "study_area" not in spec:
        raise FootprintError(f"{path.name}: 'study_area' is required")
    return spec


def compile_footprint(spec_path: Path, net: Network,
                      force_bus: bool = False) -> Footprint:
    """Resolve every group in the spec against `net`."""
    spec_path = Path(spec_path)
    spec = load_spec(spec_path)
    sa = dict(spec["study_area"] or {})
    sa_name = str(sa.pop("name", "STUDY"))

    named: dict[str, tuple[str, dict]] = {sa_name: ("study_area", sa)}
    roles = {"sub_areas": "sub_area", "sources": "source", "sinks": "sink"}
    for section, role in roles.items():
        for name, sel in (spec.get(section) or {}).items():
            name = str(name)
            if name in named:
                raise FootprintError(f"group name '{name}' is used twice")
            named[name] = (role, sel or {})
    for name in named:
        if not NAME_RE.match(name):
            raise FootprintError(f"group name '{name}': use letters, digits and "
                                 f"underscores, starting with a letter")

    coords = spec.get("coordinates") or {}
    if not isinstance(coords, dict) or set(coords) - COORD_KEYS:
        raise FootprintError(f"coordinates: expected a mapping with any of "
                             f"{sorted(COORD_KEYS)}")
    max_hops = coords.get("max_flood_hops")
    if max_hops is not None and (not isinstance(max_hops, int) or max_hops < 0):
        raise FootprintError("coordinates.max_flood_hops: a whole number ≥ 0, or omit it")
    early_warnings: list[str] = []
    if _uses_polygon(spec) or coords:
        # Default: the reference workbook the application ships with.
        # `file: none` = the case's own coordinates only.
        cfile = coords.get("file", "default")
        if cfile in (None, "none", "None", False):
            cfile = None
            if _uses_polygon(spec):
                early_warnings.append("coordinates.file is none: polygons use the "
                                      "case's own coordinates only")
        elif cfile == "default":
            from tara_automation.bus_coords import DEFAULT_WORKBOOK
            cfile = DEFAULT_WORKBOOK
            if not cfile.exists():
                raise FootprintError(f"the shipped coordinate workbook is missing: "
                                     f"{cfile}")
        else:
            cfile = Path(cfile) if Path(cfile).is_absolute() else spec_path.parent / cfile
            if not cfile.exists():
                raise FootprintError(f"coordinates.file: {cfile} not found")
        use_case = coords.get("use_case_coords", True)
        if not isinstance(use_case, bool):
            raise FootprintError("coordinates.use_case_coords: true or false")
        if not cfile and not use_case:
            raise FootprintError("coordinates: with use_case_coords: false a "
                                 "coordinates.file is required")
        locate(net, cfile, use_case, str(coords.get("interconnect", "WECC")))
    res = _Resolver(net, {n: s for n, (_, s) in named.items()}, spec.get("exclude"),
                    base_dir=spec_path.parent, max_flood_hops=max_hops)
    groups: list[Group] = []
    for name, (role, _) in named.items():
        buses = res.group(name)
        areas = sorted({net.buses[n].area for n in buses})
        exact = set().union(*(net.area_buses(a) for a in areas)) == buses
        groups.append(Group(name, role, buses,
                            areas if exact and not force_bus else []))

    warnings: list[str] = list(early_warnings)
    study = groups[0]
    polygon_notes = _polygon_notes(net, res.polygon_detail, res.cache, warnings)
    sa_sel = named[sa_name][1]
    if any(k in sa_sel for k in ("kv_min", "kv_max")):
        warnings.append(
            f"study_area '{sa_name}' is kV-filtered. 'SINGLE TIE FROM SUBSYSTEM' "
            f"will treat every branch to a filtered-out lower-voltage bus as a "
            f"tie. Prefer whole areas/zones here and set the kV range under "
            f"'monitor'.")
    for g in groups:
        if g.role == "sub_area" and not g.buses <= study.buses:
            outside = len(g.buses - study.buses)
            warnings.append(f"sub-area '{g.name}': {outside} bus(es) lie outside "
                            f"the study area and will not be monitored")
    srcs = [g for g in groups if g.role == "source"]
    snks = [g for g in groups if g.role == "sink"]
    for s in srcs:
        for k in snks:
            both = s.buses & k.buses
            if both:
                raise FootprintError(f"source '{s.name}' and sink '{k.name}' "
                                     f"share {len(both)} bus(es), e.g. "
                                     f"{sorted(both)[:5]}")

    monitor = spec.get("monitor")
    monitor_kv = None
    if monitor is not None:
        try:
            monitor_kv = (float(monitor["kv_min"]), float(monitor["kv_max"]))
        except (TypeError, KeyError, ValueError):
            raise FootprintError("monitor: expected {kv_min, kv_max}") from None
    files = {}
    for key in ("con", "mon"):
        v = spec.get(key)
        if v is None or v == "auto":
            files[key] = v
            continue
        f = Path(v) if Path(v).is_absolute() else spec_path.parent / v
        if not f.exists():
            raise FootprintError(f"{key}: '{v}' is neither 'auto' nor an existing "
                                 f"file (relative paths are read from the YAML's folder)")
        files[key] = str(f.resolve())
    if spec.get("mon") == "auto" and monitor_kv is None:
        raise FootprintError("mon: auto needs monitor: {kv_min, kv_max}")

    # The user's own .sub/.con/.mon: the footprint is appended to them.
    base_spec = spec.get("base") or {}
    if not isinstance(base_spec, dict) or set(base_spec) - set(BASE_KEYS):
        raise FootprintError(f"base: expected a mapping with any of {list(BASE_KEYS)}")
    base: dict[str, Path] = {}
    for key in BASE_KEYS:
        v = base_spec.get(key)
        if not v:
            continue
        f = Path(v) if Path(v).is_absolute() else spec_path.parent / v
        if not f.exists() or f.stat().st_size == 0:
            raise FootprintError(f"base.{key}: '{v}' is missing or empty (relative paths "
                                 f"are read from the YAML's folder)")
        base[key] = f.resolve()
    for key in ("con", "mon"):
        if key in base and files[key] not in (None, "auto"):
            raise FootprintError(f"{key}: give base.{key} (your file, which the footprint "
                                 f"appends to) or {key}: <path>, not both")
    base_names: set[str] = set()
    if "sub" in base:
        defined = subsystem_definitions(base["sub"].read_text(encoding="utf-8",
                                                              errors="replace"))
        base_names = {n.upper() for n in defined}
        clash = sorted(g.name for g in groups if g.name.upper() in base_names)
        if clash:
            raise FootprintError(
                f"{clash} already defined in your base .sub ({base['sub'].name}). The "
                f"footprint appends to that file and never replaces a definition: "
                f"rename the footprint group(s).")
    if base.get("con") and files["con"] == "auto":
        warnings.append(f"the generated N-1 lines are appended to your "
                        f"{base['con'].name}; a contingency listed in both may be "
                        f"analysed twice")

    return Footprint(
        spec_path=spec_path,
        spec_sha=hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        network=net, groups=groups, monitor_kv=monitor_kv,
        con=files["con"], mon=files["mon"], warnings=warnings,
        polygon_notes=polygon_notes, base=base, base_names=base_names,
        polygon_detail=res.polygon_detail)


def _uses_polygon(node) -> bool:
    if isinstance(node, dict):
        return "polygon" in node or any(_uses_polygon(v) for v in node.values())
    if isinstance(node, list):
        return any(_uses_polygon(v) for v in node)
    return False


def _kv_mix(net: Network, buses) -> str:
    kvs: dict[float, int] = {}
    for n in buses:
        kvs[net.buses[n].kv] = kvs.get(net.buses[n].kv, 0) + 1
    top = sorted(kvs.items(), key=lambda kv: -kv[0])[:6]
    return ", ".join(f"{k:g}:{c}" for k, c in top) + (
        f", +{len(kvs) - 6} more" if len(kvs) > 6 else "")


def _polygon_notes(net: Network, details: dict[str, dict[str, set[int]]],
                   final: dict[str, set[int]], warnings: list[str]) -> list[str]:
    """Per polygon group, counted on the buses the group finally keeps (after
    its kV / name filters): located inside, added by flooding, and where the
    flood stopped. Warns when flooding supplies most of the group or reaches
    areas that no located bus inside the polygon belongs to."""
    notes = []
    for where, d in details.items():
        kept = final.get(where, set())
        inside = d["inside"] & kept
        flooded = d["flooded"] & kept
        by: dict[str, int] = {}
        for n in inside:
            by[net.buses[n].located_by] = by.get(net.buses[n].located_by, 0) + 1
        src = ", ".join(f"{k} {v}" for k, v in sorted(by.items())) or "none"
        notes.append(f"{where}: {len(inside)} located bus(es) inside the polygon "
                     f"({src}); {len(flooded)} unlocated bus(es) added by flooding; "
                     f"the flood stopped at {len(d['stops'])} located bus(es) outside")
        if flooded and len(flooded) > len(inside):
            warnings.append(f"{where}: {len(flooded)} of {len(kept)} buses come from "
                            f"flooding through unlocated buses (kV "
                            f"{_kv_mix(net, flooded)}). Check them in bus_tags.csv "
                            f"(located_by empty).")
        home = {net.buses[n].area for n in d["inside"]}
        strays = sorted({net.buses[n].area for n in flooded} - home)
        if strays:
            n_stray = sum(1 for n in flooded if net.buses[n].area in strays)
            warnings.append(f"{where}: flooding reached area(s) {strays} "
                            f"({n_stray} buses) where no located bus is inside the "
                            f"polygon; that region may simply lack coordinates. "
                            f"Exclude it or add coordinates if it does not belong.")
        if d["capped"]:
            notes.append(f"{where}: max_flood_hops left {len(d['capped'])} unlocated "
                         f"bus(es) unvisited")
    return notes


# --------------------------------------------------------------- writers ---
def render_sub(fp: Footprint) -> str:
    lines = [
        f"// Generated by tara_automation.footprint from {fp.spec_path.name}",
        f"// spec sha256 {fp.spec_sha[:16]}; network {fp.network.source}",
        "// Do not edit: change the YAML and recompile.",
    ]
    for g in fp.groups:
        lines += ["", f"// {g.role}: {len(g.buses)} buses", f"SUBSYSTEM {g.name}"]
        if g.areas_exact:
            lines += [f"AREA {a}" for a in g.areas_exact]
        else:
            lines += [f"BUS {n}" for n in sorted(g.buses)]
        lines.append("END")
    return "\n".join(lines) + "\n"


def _norm_stmt(line: str) -> str:
    return " ".join(_strip_comment(line).upper().split())


def _append(base_file: Path, addition: str, spec_name: str) -> str:
    """The user's file, untouched, then a marked block with the addition."""
    text = base_file.read_text(encoding="utf-8", errors="replace")
    if not text.endswith("\n"):
        text += "\n"
    if not addition.strip():
        return text
    return (f"{text}\n{APPEND_MARK} from {spec_name} (base: {base_file.name}). "
            f"Edit the YAML, not this block. =====\n{addition}")


def render_template(template: str, **values) -> str:
    return (TEMPLATES / template).read_text(encoding="utf-8").format(**values)


_SUBSYS_REF = re.compile(r"\bSUBSYSTEMS?\s+'?([A-Za-z0-9_]+)'?", re.IGNORECASE)


def referenced_subsystems(path: Path) -> set[str]:
    """Subsystem names a .con / .mon file refers to (comment lines skipped)."""
    names: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        code = line.split("//")[0]
        names.update(m.group(1) for m in _SUBSYS_REF.finditer(code))
    return names


def _strip_comment(line: str) -> str:
    for mark in ("//", "/*"):
        if mark in line:
            line = line.split(mark, 1)[0]
    return line


def subsystem_definitions(text: str) -> list[str]:
    """Names a study data file DEFINES. A `SUBSYSTEM x` line inside a nested
    block (EXCEPT, JOIN, PARTICIPATE, LATLON ...) is a reference, not a
    definition (manual 2.2), so block depth is tracked: every nested block and
    every subsystem closes with END."""
    names, depth = [], 0
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        words = line.upper().split()
        if words[0] == "END":
            depth = max(depth - 1, 0)
            continue
        if depth == 0 and words[0] == "SUBSYSTEM" and len(words) > 1:
            names.append(line.split(None, 1)[1].strip().strip("'\"\u2018\u2019").split()[0])
            depth = 1
            continue
        if depth >= 1 and (words[0] in ("JOIN", "PARTICIPATE", "EXCEPT", "LATLON")
                           or words[-1] == "EXCEPT"):
            depth += 1
    return names


def check_references(fp: Footprint, files: list[Path],
                     names: list[str] | None = None) -> list[str]:
    """Every subsystem a con/mon file (or the study's ATC config) names must be
    defined in the compiled .sub — otherwise TARA silently selects nothing."""
    defined = {g.name.upper() for g in fp.groups} | fp.base_names
    problems = []
    for f in files:
        for n in sorted(referenced_subsystems(f)):
            if n.upper() not in defined:
                problems.append(f"{Path(f).name} refers to subsystem '{n}', "
                                f"which the footprint does not define")
    for n in names or []:
        if n.upper() not in defined:
            problems.append(f"study ATC config names subsystem '{n}', "
                            f"which the footprint does not define")
    return problems


def write_outputs(fp: Footprint, out_dir: Path,
                  required_names: list[str] | None = None,
                  fallback: dict[str, Path | str | None] | None = None) -> dict:
    """Write footprint.sub (+ .con/.mon when 'auto'), bus_tags.csv, the report
    and footprint.json. Returns the json record.

    `fallback` = the study's own con/mon, used (and checked against the new
    .sub) when the spec does not set them."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sa = fp.study_area
    files: dict[str, Path] = {"sub": out_dir / "footprint.sub"}
    appended: dict[str, int] = {}
    sub_text = render_sub(fp)
    if "sub" in fp.base:
        sub_text = _append(fp.base["sub"], sub_text, fp.spec_path.name)
        appended["sub"] = len(fp.groups)
    files["sub"].write_text(sub_text, encoding="utf-8")

    generated = {}
    if fp.con == "auto":
        generated["con"] = render_template("footprint_con.txt", name=sa.name)
    if fp.mon == "auto":
        lo, hi = fp.monitor_kv
        generated["mon"] = render_template("footprint_mon.txt", name=sa.name,
                                           kv_min=f"{lo:g}", kv_max=f"{hi:g}")
    for key in ("con", "mon"):
        own = fp.con if key == "con" else fp.mon
        if key in generated:
            text = generated[key]
            if key in fp.base:
                base_text = fp.base[key].read_text(encoding="utf-8", errors="replace")
                have = {_norm_stmt(ln) for ln in base_text.splitlines()}
                new = [ln for ln in text.splitlines()
                       if ln.strip() and _norm_stmt(ln) not in have]
                appended[key] = len(new)
                text = _append(fp.base[key], "\n".join(new) + "\n" if new else "",
                               fp.spec_path.name)
            files[key] = out_dir / f"footprint.{key}"
            files[key].write_text(text, encoding="utf-8")
        elif own:
            files[key] = Path(own).resolve()
        elif key in fp.base:
            files[key] = fp.base[key]          # the user's file, unchanged
    for key, path in (fallback or {}).items():
        if key not in files and path:
            files[key] = Path(path)

    problems = check_references(
        fp, [files[k] for k in ("con", "mon") if k in files], required_names)
    if problems:
        raise FootprintError("; ".join(problems))

    # bus -> every group it belongs to (the label every result row will carry)
    tags: dict[int, list[str]] = {}
    for g in fp.groups:
        for n in g.buses:
            tags.setdefault(n, []).append(g.name)
    files["bus_tags"] = out_dir / "bus_tags.csv"
    with open(files["bus_tags"], "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bus", "name", "kv", "area", "zone", "owner", "groups",
                    "lat", "lon", "located_by"])
        for n in sorted(tags):
            b = fp.network.buses[n]
            w.writerow([n, b.name, f"{b.kv:g}", b.area, b.zone, b.owner,
                        ";".join(tags[n]),
                        "" if b.lat is None else f"{b.lat:.5f}",
                        "" if b.lon is None else f"{b.lon:.5f}",
                        b.located_by or ""])

    files["report"] = out_dir / "footprint_report.md"
    files["report"].write_text(render_report(fp), encoding="utf-8")

    files["json"] = out_dir / "footprint.json"
    record = {
        "spec": str(fp.spec_path.resolve()),
        "spec_sha256": fp.spec_sha,
        "network": fp.network.source,
        "groups": {g.name: {"role": g.role, "buses": len(g.buses), "form": g.form}
                   for g in fp.groups},
        "study_area": sa.name,
        "files": {k: str(v) for k, v in files.items()},
        "sha256": {k: hashlib.sha256(Path(v).read_bytes()).hexdigest()
                   for k, v in files.items() if k in ("sub", "con", "mon")},
        "warnings": fp.warnings,
        "coordinates": fp.network.located,
        "base": {k: {"path": str(v), "sha256": hashlib.sha256(v.read_bytes()).hexdigest()}
                 for k, v in fp.base.items()},
        "appended": appended,
    }
    files["json"].write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def render_report(fp: Footprint) -> str:
    net = fp.network
    lines = [f"# Footprint report — {fp.spec_path.name}", "",
             f"Network: `{net.source}` ({len(net.buses)} buses).  "
             f"Spec sha256 `{fp.spec_sha[:16]}`.", "",
             "| Group | Role | Buses | Form | Areas | kV mix |",
             "|---|---|---|---|---|---|"]
    for g in fp.groups:
        areas = sorted({net.buses[n].area for n in g.buses})
        kvs: dict[float, int] = {}
        for n in g.buses:
            kvs[net.buses[n].kv] = kvs.get(net.buses[n].kv, 0) + 1
        top = sorted(kvs.items(), key=lambda kv: -kv[0])[:6]
        mix = ", ".join(f"{k:g}:{c}" for k, c in top)
        if len(kvs) > 6:
            mix += f", +{len(kvs) - 6} more"
        area_txt = ", ".join(map(str, areas[:12])) + (" …" if len(areas) > 12 else "")
        lines.append(f"| {g.name} | {g.role} | {len(g.buses)} | {g.form} "
                     f"| {area_txt} | {mix} |")
    if fp.monitor_kv:
        lines += ["", f"Monitoring kV range: {fp.monitor_kv[0]:g}–{fp.monitor_kv[1]:g}."]
    if net.located:
        by = net.located["by_source"]
        total = len(net.buses)
        lines += ["", "## Bus locations", "",
                  "| Located by | Buses | Share |", "|---|---|---|"]
        for k in ("file", "case", "file_estimated", "none"):
            if by.get(k):
                lines.append(f"| {k} | {by[k]} | {by[k] / total:.0%} |")
        ref = net.located.get("reference")
        if ref:
            m = ref["match"]
            lines += ["", f"Coordinate workbook `{Path(net.located['coords_file']).name}` "
                          f"({net.located['interconnect']} rows): {ref['matched']} case "
                          f"buses matched by number (name and kV agree {m['agree']}, kV "
                          f"only {m['kv_only']}, name only {m['name_only']}); "
                          f"{ref['rejected_reused_number']} rejected as reused bus "
                          f"numbers; {ref['duplicates_far']} matched buses have "
                          f"duplicate rows more than 5 mi apart. Order: `file` (named "
                          f"substation) > `case` > `file_estimated`."]
        lines += ["", "A polygon includes the located buses inside it, then floods "
                      "through neighbouring buses with no location until it reaches "
                      "a located bus outside."]
        if not net.located.get("use_case_coords", True):
            lines += ["Case coordinates were switched off (use_case_coords: false)."]
        if net.located.get("case_coords_rejected"):
            lines += [f"{net.located['case_coords_rejected']} case coordinates were "
                      f"rejected as implausible (out of range, near 0, or lat = lon), "
                      f"e.g. buses {net.located['case_coords_rejected_sample'][:8]}."]
        lines += [""]
        lines += [f"- {n}" for n in fp.polygon_notes]
    if fp.base:
        lines += ["", "## Base files (appended to, never replaced)", ""]
        lines += [f"- {k}: `{v}`" for k, v in fp.base.items()]
        if fp.base_names:
            lines += [f"- subsystems defined by the base .sub: "
                      f"{', '.join(sorted(fp.base_names))}"]
    lines += ["", "## Warnings", ""]
    lines += [f"- {w}" for w in fp.warnings] or ["- none"]
    return "\n".join(lines) + "\n"
