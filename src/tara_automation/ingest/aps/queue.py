"""Active Queue posting (Firecrawl markdown of APS's PDF) -> tidy CSV."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

HEADER = ["FERC Q Number", "Electrical Output MW", "Project Location County", "Project Location State",
          "Requested POI", "LGP Projected In Service Date", "Date of Issuance of Queue", "Queue Timestamp",
          "Current Status", "Interconnection Request Type", "Interconnection Request Submission Date",
          "Energy Source Type", "Studies Available", "Application Program Type", "Customer Activity"]

_QID = re.compile(r"^(Q\d+[A-Za-z]?(/\d+)?|SPI?-?\d+)$")

# spellings seen in the 2026-10-13 posting
COUNTY = {"Mariocopa": "Maricopa", "Mariopa": "Maricopa", "Maricapa": "Maricopa", "Yama": "Yuma",
          "Navijo": "Navajo", "Naxivo": "Navajo", "Yawapi": "Yavapai"}


def parse_markdown(md: str) -> tuple[list[dict], list[str]]:
    """Table rows of the posting -> one dict per request (first occurrence of
    each queue id). Returns (rows, ids whose MW is not a number)."""
    rows, seen, odd = [], set(), []
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        idx = next((i for i, c in enumerate(cells[:3]) if _QID.match(c)), None)
        if idx is None:
            continue
        f = cells[idx:]
        # the PDF->markdown step sometimes repeats the MW cell ("| 250 | 250 |")
        if len(f) > 2 and f[1] == f[2] and re.match(r"^[\d.]+$", f[1]):
            f = f[:2] + f[3:]
        # state column missing -> county followed directly by the POI
        if len(f) > 3 and f[3] not in ("AZ", "NM", "") and not re.match(r"^[A-Z]{2}$", f[3]):
            f = f[:3] + [""] + f[3:]
        rec = dict(zip(HEADER, f + [""] * (len(HEADER) - len(f))))
        if rec["FERC Q Number"] in seen:
            continue
        seen.add(rec["FERC Q Number"])
        rows.append(rec)
        if not re.match(r"^[\d.]+$", rec["Electrical Output MW"] or ""):
            odd.append(rec["FERC Q Number"])
    return rows, odd


def clean(rows: list[dict]) -> list[dict]:
    """Add 'County (normalized)' and 'Submission Year'."""
    out = []
    for r in rows:
        r = dict(r)
        c = r["Project Location County"].strip()
        r["County (normalized)"] = COUNTY.get(c, c)
        d = r["Interconnection Request Submission Date"] or r["Date of Issuance of Queue"]
        m = re.search(r"/(\d{2,4})\s*$", d or "")
        y = m.group(1) if m else ""
        r["Submission Year"] = ("20" + y) if len(y) == 2 else y
        out.append(r)
    return out


def write_csv(rows: list[dict], path: Path, fields: list[str] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def read_posting(raw_json: Path) -> str:
    """Markdown of a saved Firecrawl result ({markdown, metadata})."""
    return json.loads(Path(raw_json).read_text(encoding="utf-8"))["markdown"]
