"""Build the state and county borders embedded in tools/footprint_builder.html.

The published Footprint Builder cannot load map tiles or data files (the
artifact viewer only loads scripts from CDNs), so the borders ship inside the
page as one compact JSON block between the BORDERS markers.

    python scripts/build_map_borders.py            # rebuild + embed
    python scripts/build_map_borders.py --check    # sizes only, page untouched

Sources (provenance in data/reference/README.md):
    counties  data/reference/cb_2023_us_county_20m.zip  (US Census, 2023, 1:20M)
    states    ..\\TARA Automation\\TARA_ATC_v2 - Colorado Run\\
              gz_2010_us_040_00_500k.kml               (US Census, 2010, 1:500k)

Outlines are simplified (Douglas-Peucker) and stored as integer
milli-degrees, delta-encoded per ring: [x0, y0, dx1, dy1, ...] with x = lon.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COUNTIES = REPO / "data" / "reference" / "cb_2023_us_county_20m.zip"
STATES = (REPO.parent / "TARA Automation" / "TARA_ATC_v2 - Colorado Run"
          / "gz_2010_us_040_00_500k.kml")
PAGE = REPO / "tools" / "footprint_builder.html"
START, END = "/*BORDERS-START*/", "/*BORDERS-END*/"
Q = 1000                      # milli-degrees (~100 m)
TOL_STATE, TOL_COUNTY = 0.004, 0.006   # simplification tolerance, degrees

FIPS = {"01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
        "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
        "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
        "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
        "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
        "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
        "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
        "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
        "54": "WV", "55": "WI", "56": "WY", "72": "PR"}


# ------------------------------------------------------------- geometry ----
def simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterative. A closed ring (first == last point) has no
    baseline, so it is split at its farthest point and each half simplified."""
    if len(pts) < 4:
        return pts
    if pts[0] == pts[-1]:
        x0, y0 = pts[0]
        k = max(range(len(pts)), key=lambda i: (pts[i][0] - x0) ** 2 + (pts[i][1] - y0) ** 2)
        if 0 < k < len(pts) - 1:
            return _dp(pts[:k + 1], tol) + _dp(pts[k:], tol)[1:]
    return _dp(pts, tol)


def _dp(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            x, y = pts[i]
            d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
            if d > best:
                best, idx = d, i
        if best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def encode(ring: list[tuple[float, float]]) -> list[int] | None:
    q = [(round(x * Q), round(y * Q)) for x, y in ring]
    dedup = [q[0]] + [p for i, p in enumerate(q[1:], 1) if p != q[i - 1]]
    if len(dedup) < 3:
        return None
    out = [dedup[0][0], dedup[0][1]]
    for (x0, y0), (x1, y1) in zip(dedup, dedup[1:]):
        out += [x1 - x0, y1 - y0]
    return out


def bbox(rings) -> list[float]:
    xs = [x for r in rings for x, _ in r]
    ys = [y for r in rings for _, y in r]
    return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]


# --------------------------------------------------------------- readers ----
def read_shp(data: bytes) -> list[list[list[tuple[float, float]]]]:
    """Polygon shapefile -> per record, a list of rings of (lon, lat)."""
    pos, out = 100, []
    while pos + 8 <= len(data):
        _, clen = struct.unpack(">2i", data[pos:pos + 8])
        pos += 8
        c = data[pos:pos + clen * 2]
        pos += clen * 2
        if struct.unpack("<i", c[:4])[0] == 0:
            out.append([])
            continue
        nparts, npts = struct.unpack("<2i", c[36:44])
        parts = list(struct.unpack(f"<{nparts}i", c[44:44 + 4 * nparts])) + [npts]
        off = 44 + 4 * nparts
        flat = struct.unpack(f"<{2 * npts}d", c[off:off + 16 * npts])
        xy = list(zip(flat[0::2], flat[1::2]))
        out.append([xy[parts[i]:parts[i + 1]] for i in range(nparts)])
    return out


def read_dbf(data: bytes) -> list[dict[str, str]]:
    nrec, hlen, rlen = struct.unpack("<IHH", data[4:12])
    fields, p = [], 32
    while data[p] != 0x0D:
        name = data[p:p + 11].split(b"\0")[0].decode("ascii")
        fields.append((name, data[p + 16]))
        p += 32
    recs = []
    for i in range(nrec):
        r = data[hlen + i * rlen:hlen + (i + 1) * rlen]
        o, d = 1, {}
        for name, flen in fields:
            d[name] = r[o:o + flen].decode("utf-8", "replace").strip()
            o += flen
        recs.append(d)
    return recs


def load_counties(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as z:
        shp = next(n for n in z.namelist() if n.endswith(".shp"))
        dbf = next(n for n in z.namelist() if n.endswith(".dbf"))
        shapes, recs = read_shp(z.read(shp)), read_dbf(z.read(dbf))
    if len(shapes) != len(recs):
        raise SystemExit(f"{path.name}: {len(shapes)} shapes but {len(recs)} records")
    out = []
    for rings, rec in zip(shapes, recs):
        enc = [e for e in (encode(simplify(r, TOL_COUNTY)) for r in rings) if e]
        if enc:
            out.append({"s": rec["STATEFP"], "n": rec["NAME"], "b": bbox(rings), "r": enc})
    return out


def load_states(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    out = []
    for pm in (e for e in root.iter() if e.tag.endswith("Placemark")):
        data = {e.get("name"): (e.text or "").strip()
                for e in pm.iter() if e.tag.endswith("SimpleData")}
        fips = data.get("STATE", "")
        name = next((e.text for e in pm if e.tag.endswith("name")), "") or ""
        rings = []
        for poly in (e for e in pm.iter() if e.tag.endswith("Polygon")):
            outer = next((e for e in poly if e.tag.endswith("outerBoundaryIs")), None)
            coords = next((c.text for c in (outer.iter() if outer is not None else [])
                           if c.tag.endswith("coordinates")), "") or ""
            pts = [tuple(map(float, t.split(",")[:2])) for t in coords.split()]
            if len(pts) >= 3:
                rings.append(pts)
        enc = [e for e in (encode(simplify(r, TOL_STATE)) for r in rings) if e]
        if enc and fips in FIPS:
            out.append({"f": fips, "a": FIPS[fips], "n": name.strip(), "b": bbox(rings),
                        "r": enc})
    return sorted(out, key=lambda s: s["n"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--counties", default=str(COUNTIES))
    ap.add_argument("--states", default=str(STATES))
    ap.add_argument("--page", default=str(PAGE))
    ap.add_argument("--check", action="store_true", help="report sizes, do not embed")
    args = ap.parse_args()
    for p in (args.counties, args.states):
        if not Path(p).exists():
            print(f"STOP: {p} not found")
            return 1
    states = load_states(Path(args.states))
    counties = load_counties(Path(args.counties))
    blob = json.dumps({"q": Q, "states": states, "counties": counties},
                      separators=(",", ":"), ensure_ascii=False)
    pts = sum(len(r) // 2 for g in states + counties for r in g["r"])
    print(f"states {len(states)}, counties {len(counties)}, points {pts:,}, "
          f"JSON {len(blob) / 1024:,.0f} KB")
    if args.check:
        return 0
    page = Path(args.page)
    html = page.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(html):
        print(f"STOP: {page.name} has no {START} ... {END} block")
        return 1
    html = pattern.sub(lambda _: START + blob + END, html, count=1)
    page.write_text(html, encoding="utf-8")
    print(f"embedded into {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
