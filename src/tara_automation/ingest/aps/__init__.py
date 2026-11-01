"""APS (AZPS) interconnection queue and cluster studies.

    Active Queue posting (PDF, via Firecrawl)  -> queue.py   active_queue_<date>.csv
    cluster / serial study reports (PDF)       -> table extraction (outside this package)
                                                  -> data/queue/deliver/aps_{studies,projects,upgrades,violations}.csv
    queue + reports                            -> areas.py   study area of every request
                                               -> ladder.py  per-area ladder (who triggers which network upgrade)
                                               -> pages.py   self-contained HTML ladder pages
                                               -> export.py  neutral tables (generators, upgrades, allocations, contingents)
                                               -> library.py projects.xlsx rows + POI matching (task 18 library)
    contracted.csv (curated offtake evidence)  -> contracted.py  highlight on pages, desk and digest

All data lives in the APS research area, `research/areas/APS/data/queue/`
(the queue pillar of task 29). Fetching from OASIS happens outside this
package; every step after that is local and deterministic: `scripts/aps_queue.py build`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from tara_automation.machine import REPO_ROOT

DEFAULT_ROOT = REPO_ROOT / "research" / "areas" / "APS" / "data" / "queue"

# key -> (study area as named in queue_by_study_area.csv, regex on report filenames, page title)
AREAS: dict[str, tuple[str, str, str]] = {
    "fccho":  ("Four Corners-Cholla", "FcCho", "Four Corners–Cholla"),
    "phx":    ("Phoenix Metro", "PhoenixMetro|Phoenix_Metro", "Phoenix Metro"),
    "dsr":    ("Delaney-Sun Valley-Redhawk", "Delaney-SunValley|DLN-SNV-RW|Del-Svly-Red|Redhawk",
               "Delaney–Sun Valley–Redhawk"),
    "gila":   ("Gila Bend", "GilaBend", "Gila Bend"),
    "sofphx": ("South of Phoenix", "SofPhx|soPhx|South_of_Phoenix", "South of Phoenix"),
    "hdw":    ("Hoodoo Wash-Yuma", "HDW", "Hoodoo Wash–Yuma"),
    "nsts":   ("NSTS (Navajo South)", "NSTS", "NSTS (Navajo South)"),
    "north":  ("Northern (69 kV Flagstaff/Winslow)", "Northern", "Northern (69 kV)"),
}


@dataclass(frozen=True)
class QueueData:
    """Folder layout of the APS queue pillar.

    queue.yaml   posting: '20261013'   (which Active Queue posting is current)
    raw/         Firecrawl markdown of the queue + every report (git-ignored)
    parsed/      queue CSVs, per-track extraction CSVs, queue_by_study_area.csv
    deliver/     consolidated aps_{studies,projects,upgrades,violations}.csv
    ladders/     <key>.json + <key>_2026.json (generated)
    serial/      <key>_serial_ladder.json (curated from serial SIS/FaS/MMA)
    contingent/  <key>.json (curated: planned facilities each report assumed)
    notes/       <key>.json (tab notes on the pages)
    poi_map.csv  confirmed queue POI -> case bus (persisted, see library.py)
    contracted.csv  curated: requests likely already contracted (offtake class, confidence,
                 evidence, source); optional, see contracted.py. Reviewed before commit.
    pages/       <key>_ladder.html (generated)
    export/      neutral tables (generated)
    library/     projects.xlsx, poi_review.csv, ingest_report.md (generated)
    """

    root: Path = DEFAULT_ROOT

    def __post_init__(self):
        object.__setattr__(self, "root", Path(self.root))

    def _d(self, name: str) -> Path:
        return self.root / name

    raw = property(lambda s: s._d("raw"))
    parsed = property(lambda s: s._d("parsed"))
    deliver = property(lambda s: s._d("deliver"))
    ladders = property(lambda s: s._d("ladders"))
    serial = property(lambda s: s._d("serial"))
    contingent = property(lambda s: s._d("contingent"))
    notes = property(lambda s: s._d("notes"))
    pages = property(lambda s: s._d("pages"))
    export = property(lambda s: s._d("export"))
    library = property(lambda s: s._d("library"))
    poi_map = property(lambda s: s._d("poi_map.csv"))
    contracted = property(lambda s: s._d("contracted.csv"))

    @property
    def meta(self) -> dict:
        p = self.root / "queue.yaml"
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}

    @property
    def posting(self) -> str:
        return str(self.meta.get("posting", ""))

    @property
    def queue_raw(self) -> Path:
        return self.raw / f"Active_Queue_{self.posting}.json"

    @property
    def queue_csv(self) -> Path:
        return self.parsed / f"active_queue_{self.posting}.csv"

    @property
    def queue_clean(self) -> Path:
        return self.parsed / f"active_queue_{self.posting}_clean.csv"

    @property
    def queue_by_area(self) -> Path:
        return self.parsed / "queue_by_study_area.csv"
