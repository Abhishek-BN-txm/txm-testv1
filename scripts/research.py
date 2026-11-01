"""Study-area research from the command line (the desk's Research step uses the same code).

    python scripts/research.py list
    python scripts/research.py new APS "Arizona Public Service" --areas 14 --utilities APS
    python scripts/research.py scan APS            # register files dropped into research/areas/APS/files
    python scripts/research.py add-url APS https://... [--title "..."]
    python scripts/research.py ingest APS          # digest new / changed sources (Anthropic API)
    python scripts/research.py summarize APS       # rewrite the summary from the digests (Anthropic API)
    python scripts/research.py discover APS        # propose public sources found on the web (Anthropic API)
    python scripts/research.py approve APS S7      # or: reject APS S7

Ingest, summarize and discover call the Anthropic API (ANTHROPIC_API_KEY) and
log their token use and cost in research/areas/<AREA>/log.md.
"""

from __future__ import annotations

import argparse
import sys

from tara_automation import research as R


def main() -> int:
    ap = argparse.ArgumentParser(description="Study-area research")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    n = sub.add_parser("new")
    n.add_argument("id")
    n.add_argument("name")
    n.add_argument("--description", default="")
    n.add_argument("--areas", type=int, nargs="*", default=[])
    n.add_argument("--utilities", nargs="*", default=[])
    n.add_argument("--keywords", nargs="*", default=[])
    for c in ("scan", "ingest", "summarize", "discover"):
        sub.add_parser(c).add_argument("id")
    u = sub.add_parser("add-url")
    u.add_argument("id")
    u.add_argument("url")
    u.add_argument("--title", default="")
    for c in ("approve", "reject"):
        p = sub.add_parser(c)
        p.add_argument("id")
        p.add_argument("source")
    a = ap.parse_args()
    try:
        if a.cmd == "list":
            for ar in R.areas():
                rows = ar.sources()
                print(f"{ar.id:12} {ar.meta().get('name', '')}  sources {len(rows)}, pending {len(R.pending(ar))}")
        elif a.cmd == "new":
            R.create_area(a.id, a.name, a.description, {"areas": a.areas} if a.areas else {},
                          a.utilities, a.keywords)
            print(f"created research/areas/{a.id}/ - put files in its files/ folder")
        elif a.cmd == "scan":
            print("new:", R.scan(R.get_area(a.id)) or "none")
        elif a.cmd == "add-url":
            print(R.add_url(R.get_area(a.id), a.url, a.title)["id"])
        elif a.cmd in ("approve", "reject"):
            R.set_status(R.get_area(a.id), a.source, "active" if a.cmd == "approve" else "rejected")
        else:
            area = R.get_area(a.id)
            if a.cmd == "ingest":
                R.scan(area)
                print(R.ingest(area))
            elif a.cmd == "summarize":
                print(R.synthesize(area))
            else:
                print(R.discover(area))
    except R.ResearchError as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
