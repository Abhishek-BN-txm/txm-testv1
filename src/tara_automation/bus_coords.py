"""Bus coordinates from the reference workbook (data/reference/Bus_Coordinates.xlsx).

The workbook covers WECC, ERCOT and the Eastern Interconnection, and bus
numbers repeat across them, so rows are always filtered to one interconnection
before matching on bus number. Findings behind every rule here are in
data/reference/README.md (checked against EAGL run 25).

Each matched bus gets one location, in this order (user-approved 2026-10-11):

    file            workbook row with a named substation (SUBNAME present)
    case            the case's own lat/lon, vetted by footprint.plausible_coord
    file_estimated  workbook row with SUBNAME = NULL (estimated positions:
                    median 3.4 mi from the case, 42 % > 5 mi)

A workbook row whose name AND kV both disagree with the case bus is a bus
number reused for a different bus, and is rejected.

Reading the 13 MB workbook takes ~18 s, so the filtered rows are cached as a
CSV under data/cache/, keyed by the workbook's hash and the interconnection.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from tara_automation.machine import REPO_ROOT

DEFAULT_WORKBOOK = REPO_ROOT / "data" / "reference" / "Bus_Coordinates.xlsx"
CACHE_DIR = REPO_ROOT / "data" / "cache"
REQUIRED = ("Y_LAT", "X_LONG", "BUSNUM", "BUSNAME", "VOLTAGE", "INTERCONNECT", "SUBNAME")
CACHE_COLS = ["bus", "lat", "lon", "name", "kv", "named", "copies", "copies_far"]


class BusCoordsError(ValueError):
    """The coordinate workbook cannot be used as given."""


@dataclass
class RefBus:
    lat: float
    lon: float
    name: str
    kv: float | None
    named: bool            # SUBNAME present -> trusted tier
    copies: int = 1        # rows with this bus number in the interconnection
    copies_far: bool = False   # duplicate rows more than 5 mi apart


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 3959 * 2 * math.asin(math.sqrt(h))


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_workbook(path: Path, interconnect: str = "WECC") -> dict[int, RefBus]:
    """Rows of one interconnection with a usable lat/lon, keyed by bus number.
    For a repeated number the first row is kept and the bus is flagged."""
    import openpyxl

    from tara_automation.footprint import plausible_coord
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows, ())]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise BusCoordsError(f"{Path(path).name}: missing column(s) {missing}")
    ix = {c: header.index(c) for c in REQUIRED}
    out: dict[int, RefBus] = {}
    seen_ic = set()
    for r in rows:
        ic = r[ix["INTERCONNECT"]]
        seen_ic.add(ic)
        if ic != interconnect:
            continue
        n = _num(r[ix["BUSNUM"]])
        lat, lon = _num(r[ix["Y_LAT"]]), _num(r[ix["X_LONG"]])
        if n is None or lat is None or lon is None or not plausible_coord(lat, lon):
            continue
        n = int(n)
        sub = r[ix["SUBNAME"]]
        row = RefBus(lat, lon, str(r[ix["BUSNAME"]] or "").strip(),
                     _num(r[ix["VOLTAGE"]]), sub not in (None, "", "NULL"))
        if n in out:
            first = out[n]
            first.copies += 1
            if _miles((first.lat, first.lon), (lat, lon)) > 5:
                first.copies_far = True
            continue
        out[n] = row
    if not out:
        raise BusCoordsError(f"{Path(path).name}: no usable rows for interconnection "
                             f"'{interconnect}' (found: {sorted(map(str, seen_ic))})")
    return out


def load_reference(path: Path = DEFAULT_WORKBOOK, interconnect: str = "WECC",
                   cache_dir: Path | None = CACHE_DIR) -> dict[int, RefBus]:
    """read_workbook, through a CSV cache keyed by the workbook hash."""
    path = Path(path)
    if not path.exists():
        raise BusCoordsError(f"coordinate workbook not found: {path}")
    cache = None
    if cache_dir is not None:
        cache = Path(cache_dir) / f"bus_coords_{interconnect}_{_sha(path)[:16]}.csv"
        if cache.exists():
            out = {}
            with open(cache, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    out[int(r["bus"])] = RefBus(
                        float(r["lat"]), float(r["lon"]), r["name"],
                        float(r["kv"]) if r["kv"] else None, r["named"] == "1",
                        int(r["copies"]), r["copies_far"] == "1")
            return out
    out = read_workbook(path, interconnect)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(CACHE_COLS)
            for n in sorted(out):
                b = out[n]
                w.writerow([n, b.lat, b.lon, b.name, "" if b.kv is None else b.kv,
                            int(b.named), b.copies, int(b.copies_far)])
        tmp.replace(cache)
    return out


_norm = re.compile(r"[^A-Z0-9]")


def match(case_name: str, case_kv: float, ref: RefBus) -> str:
    """agree | kv_only | name_only | neither (bus number reused)."""
    nm = _norm.sub("", case_name.upper())[:6] == _norm.sub("", ref.name.upper())[:6]
    kv = ref.kv is not None and abs(ref.kv - case_kv) <= max(1.0, 0.05 * case_kv)
    return "agree" if nm and kv else "kv_only" if kv else "name_only" if nm else "neither"


def apply_reference(net, ref: dict[int, RefBus]) -> dict:
    """Locate the network's buses from the workbook, in the approved order.
    The case's own (vetted) coordinates are already on the buses."""
    stats = {"matched": 0, "rejected_reused_number": 0, "duplicates_far": 0,
             "match": {"agree": 0, "kv_only": 0, "name_only": 0}}
    for n, b in net.buses.items():
        r = ref.get(n)
        if r is None:
            continue
        m = match(b.name, b.kv, r)
        if m == "neither":
            stats["rejected_reused_number"] += 1
            continue
        stats["matched"] += 1
        stats["match"][m] += 1
        stats["duplicates_far"] += int(r.copies_far)
        if r.named:
            b.lat, b.lon, b.located_by = r.lat, r.lon, "file"
        elif b.lat is None:
            b.lat, b.lon, b.located_by = r.lat, r.lon, "file_estimated"
    return stats


def write_locations(net, path: Path) -> Path:
    """Every case bus with its final location and source (blank = unlocated).
    This is what the Footprint Builder loads to draw the network."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bus", "name", "kv", "area", "zone", "lat", "lon", "source"])
        for n in sorted(net.buses):
            b = net.buses[n]
            w.writerow([n, b.name, f"{b.kv:g}", b.area, b.zone,
                        "" if b.lat is None else f"{b.lat:.5f}",
                        "" if b.lon is None else f"{b.lon:.5f}", b.located_by or ""])
    return path
