"""Study-area polygons from the APS cluster-study figures ("FIGURE 1. <AREA> CLUSTER").

Each APS cluster study report draws its study area as a filled shape on an
Arizona outline. This tool georeferences the figure by fitting the real state
boundary onto the drawn outline (x/y offset and scale, chamfer matching), then
traces the filled area and converts it to a lat/lon polygon.

    python scripts/study_area_figures.py add fccho path/to/figure.png [--source "report file"] [--fill 115,178,255]
    python scripts/study_area_figures.py list

Writes research/areas/APS/data/queue/study_areas/
    figures/<key>.png          the figure as given (provenance)
    study_areas.json           {key: {polygon: [[lat, lon], ...], fit_rms_px, source, added, fill}}
    check_<key>.png            the figure with the fitted state boundary (green) for a visual check

Optional dependencies (not in requirements.txt; the desk only reads the JSON):
    pip install pillow scipy
The state boundary comes from the Footprint Builder's embedded borders.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "tools" / "footprint_builder.html"
OUT = REPO / "research" / "areas" / "APS" / "data" / "queue" / "study_areas"


def state_ring(abbr: str = "AZ") -> np.ndarray:
    raw = PAGE.read_text(encoding="utf-8")
    i, j = raw.index("/*BORDERS-START*/") + 17, raw.index("/*BORDERS-END*/")
    d = json.loads(raw[i:j]); q = d["q"]
    s = next(s for s in d["states"] if s["a"] == abbr)
    rings = []
    for r in s["r"]:
        x = y = 0; pts = []
        for k in range(0, len(r), 2):
            x += r[k]; y += r[k + 1]; pts.append((x / q, y / q))
        rings.append(np.array(pts))
    return max(rings, key=len)


def densify(ring: np.ndarray, step: float = 0.01) -> np.ndarray:
    out = []
    for a, b in zip(ring, np.roll(ring, -1, 0)):
        n = max(1, int(np.hypot(*(b - a)) / step)); t = np.linspace(0, 1, n, endpoint=False)[:, None]
        out.append(a + t * (b - a))
    return np.vstack(out)


def trace(mask: np.ndarray) -> np.ndarray:
    """Outer boundary of the largest blob, as ordered (row, col) points (Moore neighbour tracing)."""
    pad = np.pad(mask, 1)
    rows, cols = np.nonzero(pad)
    start = (rows[0], cols[0])                     # top-most, then left-most pixel
    nb = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
    pts, cur, back = [start], start, 6             # came from the west
    for _ in range(pad.size):
        for k in range(8):
            d = (back + 1 + k) % 8
            r, c = cur[0] + nb[d][0], cur[1] + nb[d][1]
            if pad[r, c]:
                back = (d + 4) % 8; cur = (r, c); break
        if cur == start:
            break
        pts.append(cur)
    return np.array(pts, dtype=float) - 1


def rdp(pts: np.ndarray, eps: float) -> np.ndarray:
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]; ab = b - a; n2 = ab @ ab
    t = np.clip(((pts - a) @ ab) / n2, 0, 1) if n2 else np.zeros(len(pts))
    d = np.linalg.norm(pts - (a + t[:, None] * ab), axis=1)
    i = int(np.argmax(d))
    if d[i] <= eps:
        return np.array([a, b])
    return np.vstack([rdp(pts[: i + 1], eps)[:-1], rdp(pts[i:], eps)])


def georeference(img_path: Path, fill=(115, 178, 255), title_cut: int | None = None) -> dict:
    from PIL import Image
    from scipy import ndimage, optimize
    im = np.array(Image.open(img_path).convert("RGB")).astype(int)
    H, W, _ = im.shape
    # state outline: grey/black unsaturated pixels; largest connected piece
    dark = (im.max(-1) < 170) & ((im.max(-1) - im.min(-1)) < 45)
    if title_cut is None:                      # skip the caption: first rows that are mostly text
        title_cut = 0
    dark[:title_cut] = False
    lab, n = ndimage.label(ndimage.binary_dilation(dark, iterations=2))
    sizes = ndimage.sum(dark, lab, range(1, n + 1))
    outline = (lab == int(np.argmax(sizes)) + 1) & dark
    dt = ndimage.distance_transform_edt(~outline)
    pts = densify(state_ring("AZ"))
    lon0, lon1, lat0, lat1 = pts[:, 0].min(), pts[:, 0].max(), pts[:, 1].min(), pts[:, 1].max()
    ys, xs = np.nonzero(outline)

    def proj(p, P):
        return p[0] + (P[:, 0] - lon0) * p[1], p[2] + (lat1 - P[:, 1]) * p[3]

    def cost(p):
        x, y = proj(p, pts)
        return float(np.mean(ndimage.map_coordinates(dt, [np.clip(y, 0, H - 1), np.clip(x, 0, W - 1)], order=1) ** 2))

    p0 = [xs.min(), (xs.max() - xs.min()) / (lon1 - lon0), ys.min(), (ys.max() - ys.min()) / (lat1 - lat0)]
    res = optimize.minimize(cost, p0, method="Nelder-Mead", options={"xatol": 0.01, "fatol": 1e-4, "maxiter": 4000})
    p = res.x
    # the filled study area: pixels near the fill colour, bridged across the lines drawn over it
    m = np.abs(im - np.array(fill)).sum(-1) < 70
    m = ndimage.binary_closing(m, structure=np.ones((3, 3)), iterations=4)
    lab, n = ndimage.label(m)
    if n == 0:
        raise SystemExit(f"no area in fill colour {fill} found in {img_path}")
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    area = ndimage.binary_fill_holes(lab == int(np.argmax(sizes)) + 1)
    c = rdp(trace(area), 1.0)                             # (row, col), simplified to ~1 px
    lon = lon0 + (c[:, 1] - p[0]) / p[1]; lat = lat1 - (c[:, 0] - p[2]) / p[3]
    poly = [[round(float(a), 4), round(float(b), 4)] for a, b in zip(lat, lon)]
    check = im.astype("uint8").copy()
    x, y = proj(p, pts)
    for xi, yi in zip(x.astype(int), y.astype(int)):
        if 0 <= yi < H and 0 <= xi < W:
            check[max(0, yi - 1): yi + 2, max(0, xi - 1): xi + 2] = (0, 200, 0)
    return {"polygon": poly, "fit_rms_px": round(res.fun ** 0.5, 2), "area_px": int(area.sum()), "check": check}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("key"); a.add_argument("image", type=Path)
    a.add_argument("--source", default=""); a.add_argument("--fill", default="115,178,255")
    a.add_argument("--title-cut", type=int, default=None, help="ignore rows above this pixel (caption)")
    a.add_argument("--out", type=Path, default=OUT)
    sub.add_parser("list")
    args = ap.parse_args(argv)
    out = getattr(args, "out", OUT)
    js = out / "study_areas.json"
    data = json.loads(js.read_text(encoding="utf-8")) if js.exists() else {}
    if args.cmd == "list":
        for k, v in data.items():
            print(f"{k:8s} {len(v['polygon']):4d} vertices  fit {v['fit_rms_px']} px  {v.get('source', '')}")
        return 0
    fill = tuple(int(v) for v in args.fill.split(","))
    r = georeference(args.image, fill, args.title_cut)
    if r["fit_rms_px"] > 4:
        print(f"WARNING: state-outline fit is poor ({r['fit_rms_px']} px RMS): check check_{args.key}.png")
    (out / "figures").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.image, out / "figures" / f"{args.key}{args.image.suffix.lower()}")
    from PIL import Image
    Image.fromarray(r["check"]).save(out / f"check_{args.key}.png")
    data[args.key] = {"polygon": r["polygon"], "fit_rms_px": r["fit_rms_px"], "area_px": r["area_px"],
                      "source": args.source, "fill": list(fill), "added": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    js.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"{args.key}: {len(r['polygon'])} vertices, fit {r['fit_rms_px']} px RMS -> {js}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
