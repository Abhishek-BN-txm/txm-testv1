"""Large loads (data centers, fabs, mines) and transmission for load: map positions.

    python scripts/large_loads.py build APS     # writes research/areas/APS/data/large_loads/large_loads_geo.json
    python scripts/large_loads.py status APS    # what is placed, and how

Inputs (research/areas/<AREA>/data/large_loads/): large_loads.csv, tx_for_load.csv,
notes.md and locate_hints.csv. The study desk's Large loads tab only reads the
generated JSON. Rules: tara_automation.large_loads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from tara_automation import large_loads as LL  # noqa: E402
from tara_automation import research as R  # noqa: E402


def _print(out: dict) -> None:
    c = out["counts"]
    print(f"loads: {sum(c['loads'].values())} rows · " + ", ".join(f"{n} {k}" for k, n in sorted(c["loads"].items())))
    print(f"transmission: {sum(c['tx'].values())} rows · " + ", ".join(f"{n} {k}" for k, n in sorted(c["tx"].items())))
    for g in out["loads"]:
        where = f"{g.get('label', '')} ±{g.get('unc_mi') or '?'} mi" if g["how"] != "not placed" else g.get("why", "")
        print(f"  [{g['how']:<11}] {g['project'][:58]:<58} {where}")
    for g in out["tx"]:
        extra = f" (not located: {', '.join(g['missing'])})" if g["missing"] else ""
        print(f"  [{g['how']:<11}] {g['facility'][:58]:<58}{extra}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "status"])
    ap.add_argument("area", help="research area id, e.g. APS")
    a = ap.parse_args(argv)
    area = R.get_area(a.area)
    if not LL.folder(area.root).is_dir():
        print(f"{area.id} has no data/large_loads folder")
        return 1
    if a.cmd == "build":
        out = LL.build(area.root)
        print(f"wrote {LL.folder(area.root) / LL.GEO}")
        _print(out)
        return 0
    v = LL.view(area.root)
    if not v or not v.get("geo"):
        print("not built yet: python scripts/large_loads.py build " + area.id)
        return 1
    print(f"built {v['geo']['built_at']}{' (inputs changed since: rebuild)' if v['stale'] else ''}")
    _print(v["geo"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
