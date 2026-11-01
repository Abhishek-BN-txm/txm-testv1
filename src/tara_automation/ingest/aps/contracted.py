"""Which APS queue requests are likely already contracted (offtake).

`contracted.csv` in the queue folder is curated by a person (git-tracked, like
contingent/<key>.json and data/grid/planned_subs.csv) from public evidence:
APS RFP results, ACC filings, PPA announcements, developer press releases.
Nothing here fetches or guesses: a request not in the file has no known offtake.

    q,project_name,developer,mw,tech,offtaker,contract_type,contract_status,class,confidence,evidence,source_url,note

class       aps_owned | aps_ppa | aps_selected | aps_likely | other_offtaker
confidence  high | medium | low
contract_status  free text; 'announced' (proposed, not yet ACC-approved / executed) is flagged and drawn
            distinctly from executed / approved rows.

The file is optional: without it every reader gets an empty map and no
highlight. A row with a bad class / confidence is reported, never fatal, and
left out; a row without a queue number (unmatched project) is ignored.

    read(path)            -> (map, problems)       map: {Q123: {...}}
    load(qd)              -> map                    (problems dropped)
    summarize(reqs, map)  -> {class: {n, mw}}      over the given requests
"""

from __future__ import annotations

import csv
from pathlib import Path

FIELDS = ["q", "project_name", "developer", "mw", "tech", "offtaker", "contract_type", "contract_status",
          "class", "confidence", "evidence", "source_url", "note"]
# class -> display label (order = strength of the APS commitment, used for legends)
CLASSES: dict[str, str] = {
    "aps_owned": "APS-owned / BTA",
    "aps_ppa": "APS PPA / tolling",
    "aps_selected": "APS RFP selected",
    "aps_likely": "Likely APS (inferred)",
    "other_offtaker": "Other offtaker",
}
NONE_LABEL = "No known offtake"
CONFIDENCE = ("high", "medium", "low")
KEEP = ["project_name", "developer", "offtaker", "contract_type", "contract_status", "class", "confidence",
        "evidence", "source_url", "note"]


def path_of(qd) -> Path:
    return Path(qd.root) / "contracted.csv"


def norm_q(v: str) -> str:
    """Queue ids match the Active Queue posting as exact strings ('Q467', 'SPI01', 'Q236/272'); only
    surrounding blanks are dropped, and a bare number gets its Q ('467' -> 'Q467'). '' stays ''."""
    s = str(v or "").strip()
    return f"Q{s}" if s.isdigit() else s


def read(p: Path) -> tuple[dict[str, dict], list[str]]:
    p = Path(p)
    if not p.exists():
        return {}, []
    out: dict[str, dict] = {}
    problems: list[str] = []
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            cols = [c.strip() for c in (rd.fieldnames or [])]
            missing = [c for c in ("q", "class", "confidence") if c not in cols]
            if missing:
                return {}, [f"{p.name}: missing column(s) {', '.join(missing)}"]
            for i, raw in enumerate(rd, start=2):
                r = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k}
                q = norm_q(r.get("q", ""))
                if not q:
                    continue                                   # unmatched project: no queue number
                cls, conf = r.get("class", "").lower(), r.get("confidence", "").lower()
                if cls not in CLASSES:
                    problems.append(f"{p.name} line {i} ({q}): class '{r.get('class', '')}' is not one of {', '.join(CLASSES)}")
                    continue
                if conf not in CONFIDENCE:
                    problems.append(f"{p.name} line {i} ({q}): confidence '{r.get('confidence', '')}' is not high/medium/low")
                    continue
                if q in out:
                    problems.append(f"{p.name} line {i}: {q} listed twice; the first row is kept")
                    continue
                x = {k: r.get(k, "") for k in KEEP}
                x.update({"class": cls, "confidence": conf, "label": CLASSES[cls],
                          # proposed, not yet approved / executed (e.g. APS-owned plants before ACC approval)
                          "announced": "announced" in x["contract_status"].lower(),
                          # source_url may hold several links separated by '|'
                          "sources": [u.strip() for u in x["source_url"].split("|") if u.strip()]})
                out[q] = x
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        return {}, [f"{p.name}: could not be read ({type(e).__name__}: {e})"]
    return out, problems


def load(qd) -> dict[str, dict]:
    return read(path_of(qd))[0]


def summarize(reqs, cmap: dict[str, dict], q_of=lambda x: x["FERC Q Number"],
              mw_of=lambda x: x.get("Electrical Output MW")) -> dict[str, dict]:
    """{class: {n, mw}} for the requests that are in the map (classes in CLASSES order, empty ones left out)."""
    acc = {k: {"n": 0, "mw": 0.0} for k in CLASSES}
    for x in reqs:
        c = cmap.get(q_of(x))
        if not c:
            continue
        try:
            mw = float(mw_of(x) or 0)
        except (TypeError, ValueError):
            mw = 0.0
        acc[c["class"]]["n"] += 1
        acc[c["class"]]["mw"] += mw
    return {k: {"n": v["n"], "mw": round(v["mw"], 1)} for k, v in acc.items() if v["n"]}
