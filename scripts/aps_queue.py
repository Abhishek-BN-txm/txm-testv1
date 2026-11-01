"""APS interconnection queue + cluster studies (task 23, APS adapter).

    # rebuild everything local from the saved posting + parsed study CSVs
    python scripts/aps_queue.py build
    python scripts/aps_queue.py build --pages-only

    # queue -> project library, POI candidates from a basecase run (BusData.csv)
    python scripts/aps_queue.py library --run 71

    # after filling use_bus in library/poi_review.csv: persist the confirmed POIs
    python scripts/aps_queue.py accept-poi --run 71
    python scripts/aps_queue.py library --run 71        # re-run: those requests now join projects.xlsx

    python scripts/aps_queue.py status

    # the queue as research source QUEUE (digest computed here, $0) for the area summary
    python scripts/aps_queue.py digest              # also run at the end of `build`

Data: research/areas/APS/data/queue/ (see tara_automation.ingest.aps.QueueData).
Fetching new postings / reports from APS OASIS happens outside this
package. Nothing here runs TARA.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from tara_automation.ingest.aps import AREAS, DEFAULT_ROOT, QueueData  # noqa: E402
from tara_automation.ingest.aps import library, pipeline  # noqa: E402
from tara_automation.machine import REPO_ROOT  # noqa: E402

RUNS = REPO_ROOT / "runs"


def _buses(run):
    if run is None:
        return None, ""
    d = RUNS / str(run) / "results"
    return library.buses_from_run(d), f"run {run} ({d / 'BusData.csv'})"


def _digest(qd) -> bool:
    """QUEUE source of the research area whose data folder this is (research/areas/<ID>/data/queue)."""
    from tara_automation import research as R
    from tara_automation.ingest.aps import digest
    area_root = qd.root.resolve().parent.parent
    try:
        area = R.get_area(area_root.name)
    except R.ResearchError:
        print(f"no research area at {area_root}: QUEUE source not written")
        return False
    if area.root.resolve() != area_root:
        print(f"{qd.root} is not a research area's queue folder: QUEUE source not written")
        return False
    d = digest.write_area_source(area, qd)
    print(f"research source QUEUE for {area.id}: {len(d['findings'])} findings, {len(d['projects'])} pending requests")
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="queue data folder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="queue -> study areas -> ladders -> pages -> export")
    b.add_argument("--pages-only", action="store_true")
    b.add_argument("--area", action="append", choices=sorted(AREAS), help="limit ladders/pages to these keys")
    lb = sub.add_parser("library", help="queue -> library/projects.xlsx + poi_review.csv + ingest_report.md")
    lb.add_argument("--run", type=int, help="basecase run whose BusData.csv gives POI candidates")
    ac = sub.add_parser("accept-poi", help="persist use_bus rows of poi_review.csv into poi_map.csv")
    ac.add_argument("--run", type=int, help="check each bus number and kV against this basecase run")
    ac.add_argument("--who", default="user")
    sub.add_parser("status", help="what is in the queue data folder")
    sub.add_parser("digest", help="write research source QUEUE (digest of the posting + reports) for the area")
    a = ap.parse_args(argv)
    qd = QueueData(a.root)

    if a.cmd == "build":
        if a.area:
            if not a.pages_only:
                pipeline.build_queue(qd)
                pipeline.build_ladders(qd, a.area)
            pipeline.build_pages(qd, a.area)
        else:
            pipeline.build_all(qd, pages_only=a.pages_only)
        if not a.pages_only:
            _digest(qd)
        return 0
    if a.cmd == "digest":
        return 0 if _digest(qd) else 1
    if a.cmd == "library":
        buses, label = _buses(a.run)
        c = library.build_library(qd, buses, label)
        print(f"read {c['read']}, in service skipped {c['skipped_in_service']}, kept {c['kept']}, "
              f"attention {c['attention']}, POIs in review {c['pois_in_review']}, library problems {c['library_problems']}")
        print(f"-> {qd.library}")
        return 1 if c["library_problems"] else 0
    if a.cmd == "accept-poi":
        buses, _ = _buses(a.run)
        n, problems = library.accept_review(qd, who=a.who, buses=buses)
        print(f"{n} POI(s) added to {qd.poi_map}")
        for p in problems:
            print("  refused:", p)
        return 1 if problems else 0
    if a.cmd == "status":
        print(f"root {qd.root}\nposting {qd.posting or '(queue.yaml missing)'}")
        for name in ("raw", "parsed", "deliver", "ladders", "serial", "contingent", "notes", "pages", "export", "library"):
            p = getattr(qd, name)
            print(f"  {name:10s} {len(list(p.glob('*'))) if p.exists() else 0} files")
        print(f"  poi_map    {len(library.read_poi_map(qd))} confirmed POIs")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
