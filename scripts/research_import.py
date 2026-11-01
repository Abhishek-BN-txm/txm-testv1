"""Register digests written outside the in-desk agent (e.g. by an external
research session) into a study area: one source + one digest per document.

    python scripts/research_import.py APS digests.json --by "external research session"

digests.json: a JSON array; each object has the digest fields of
research.DIGEST_SCHEMA plus "file" (a name in the area's files folder, or "")
and "url". A trailing {"not_public": [...]} element is written to the area's
log as gaps. A source whose URL is already listed (e.g. proposed) is updated
and made active instead of duplicated. Digests are validated: topics must be
the area's pillars, and the file must exist when one is named.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from tara_automation import research as R

REQUIRED = ("title", "publisher", "date", "doc_type", "summary", "relevance", "findings", "projects", "open_questions")


def import_digests(area: R.Area, items: list[dict], by: str) -> dict:
    rows = area.sources()
    by_url = {r.get("url"): r for r in rows if r.get("url")}
    by_path = {r.get("path"): r for r in rows if r.get("path")}
    added, updated, gaps, problems = [], [], [], []
    for it in items:
        if "not_public" in it:
            gaps += it["not_public"]
            continue
        miss = [k for k in REQUIRED if k not in it]
        bad = [f["topic"] for f in it.get("findings", []) if f.get("topic") not in R.TOPICS]
        fname = (it.get("file") or "").strip()
        if fname and not (area.files / fname).exists():
            problems.append(f"{fname}: file not in {area.files}")
            continue
        if miss or bad:
            problems.append(f"{it.get('title', '?')}: missing {miss} / unknown topics {sorted(set(bad))}")
            continue
        r = by_url.get(it.get("url")) or (by_path.get(fname) if fname else None)
        if r is None:
            r = {"id": R._next_id(rows), "added": datetime.now().isoformat(timespec="seconds"), "added_by": by}
            rows.append(r)
            added.append(r["id"])
        else:
            updated.append(r["id"])
        r.update({"kind": "file" if fname else "url", "title": it["title"], "status": "active",
                  "publisher": it["publisher"], "date": it["date"], "doc_type": it["doc_type"],
                  "relevance": it["relevance"]})
        if it.get("url"):
            r["url"] = it["url"]
        if fname:
            r["path"] = fname
            r["sha"] = R._sha(area.files / fname)
        r["digested_sha"] = r.get("sha") or "url"
        r.pop("error", None)
        digest = {k: it[k] for k in REQUIRED}
        digest.update({"source_id": r["id"], "model": by, "digested_at": datetime.now().isoformat(timespec="seconds"),
                       "usage": {"input": 0, "output": 0, "cache_read": 0}, "cost_usd": 0.0})
        (area.root / "digests").mkdir(exist_ok=True)
        (area.root / "digests" / f"{r['id']}.json").write_text(json.dumps(digest, indent=2), encoding="utf-8")
    area.save_sources(rows)
    area.log(f"import by {by}: {len(added)} new, {len(updated)} updated source(s)"
             + (f"; {len(problems)} rejected" if problems else ""))
    for g in gaps:
        area.log(f"not public (gap): {g}")
    return {"added": added, "updated": updated, "gaps": gaps, "problems": problems}


def main() -> int:
    ap = argparse.ArgumentParser(description="Register externally written digests")
    ap.add_argument("area")
    ap.add_argument("digests", type=Path)
    ap.add_argument("--by", default="external research session")
    a = ap.parse_args()
    res = import_digests(R.get_area(a.area), json.loads(a.digests.read_text(encoding="utf-8")), a.by)
    print(json.dumps(res, indent=1))
    return 1 if res["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
