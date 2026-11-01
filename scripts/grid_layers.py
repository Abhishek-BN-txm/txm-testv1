"""Load the transmission lines and substations for a research area's power-flow footprint.

Rule: every state the area's case areas reach (area.yaml footprint.areas ->
coordinate-workbook buses of those areas -> their states), whole states.

    python scripts/grid_layers.py APS                       # states from the case areas (APS: area 14)
    python scripts/grid_layers.py APS --states AZ NM        # override
    python scripts/grid_layers.py APS --min-kv 69           # default 0: every line in the layer

Writes research/areas/<ID>/data/grid/grid.json (git-ignored; licensed data).
The APS ladder pages embed it on the next `scripts/aps_queue.py build --pages-only`.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from tara_automation import grid_layers as G  # noqa: E402
from tara_automation import research as R  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("area")
    ap.add_argument("--states", nargs="*", help="state abbreviations (default: from the case areas)")
    ap.add_argument("--min-kv", type=float, default=0)
    ap.add_argument("--min-buses", type=int, default=5, help="a state needs this many area buses to count")
    a = ap.parse_args(argv)
    area = R.get_area(a.area)
    for p in (G.LINES_KMZ, G.SUBS_KMZ):
        if not p.exists():
            print(f"STOP: {p} not found")
            return 1
    if a.states:
        states, basis = [s.upper() for s in a.states], {"given": True}
    else:
        areas = (area.meta().get("footprint") or {}).get("areas") or []
        if not areas:
            print(f"STOP: {area.id} has no case areas in area.yaml footprint.areas; pass --states")
            return 1
        basis = G.area_states(areas, a.min_buses)
        states = list(basis)
        print(f"case areas {areas} -> buses by state: {basis}")
    out = G.write_area_states(area.root, states, a.min_kv, basis)
    d = json.loads(out.read_text(encoding="utf-8"))
    kv = collections.Counter(int(l["kv"]) for l in d["lines"])
    print(f"{out}: states {states}; {len(d['lines'])} lines, {len(d['subs'])} substations, "
          f"{out.stat().st_size // 1024} KB")
    print("lines by kV:", ", ".join(f"{k} kV {n}" for k, n in sorted(kv.items(), reverse=True)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
