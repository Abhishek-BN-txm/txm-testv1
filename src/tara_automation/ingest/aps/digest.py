"""The APS queue data as a research source (`QUEUE`), like `eia.py` does for EIA.

The research summary cites digests. This module turns the queue pillar's own
files (the APS Active Queue posting and the extracted cluster / facilities
study reports) into a digest computed on this machine, at no model cost, so
the interconnection-queue pillar cites the utility's primary posting [QUEUE]
instead of secondary compilations.

    findings(qd)                 -> digest findings (topic interconnection_queue)
    write_area_source(area, qd)  -> digests/QUEUE.json + the QUEUE row in sources.yaml
"""

from __future__ import annotations

import collections
import csv
import hashlib
import html
import json
import re
from datetime import datetime

from tara_automation.ingest.aps import AREAS, QueueData, contracted
from tara_automation.ingest.aps.library import poi_key, poi_kv

NOT_PENDING = ("In service", "Surplus")
LIVE_STATUSES = ("In Construction", "Customer Test Energy", "In Design")


def _rows(p):
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _mw(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _gw(mw: float) -> str:
    return f"{mw / 1000:,.1f} GW" if mw >= 1000 else f"{mw:,.0f} MW"


def _tech(t: str) -> str:
    t = html.unescape(t or "").strip()
    m = re.match(r"Combined Energy Source \((.+)\)", t)
    return (m.group(1).replace("Energy Storage", "storage") + " hybrid") if m else (t.replace("Energy Storage", "Storage") or "not stated")


def _status(s: str) -> str:
    return html.unescape(s or "").strip()


def _date(posting: str) -> str:
    return f"{posting[:4]}-{posting[4:6]}-{posting[6:]}" if re.fullmatch(r"\d{8}", posting or "") else posting


def _isd_year(s: str):
    m = re.search(r"/(\d{2,4})\s*$", s or "")
    if not m:
        return None
    y = m.group(1)
    return int("20" + y) if len(y) == 2 else int(y)


def _requests(qd: QueueData) -> list[dict]:
    clean = {r["FERC Q Number"]: r for r in _rows(qd.queue_clean)}
    out = []
    for r in _rows(qd.queue_by_area):
        c = clean.get(r["FERC Q Number"], {})
        out.append({**r, "mw": _mw(r.get("Electrical Output MW")), "tech": _tech(r.get("Energy Source Type", "")),
                    "status": _status(r.get("Current Status", "")), "isd": c.get("LGP Projected In Service Date", ""),
                    "pending": r.get("Process") not in NOT_PENDING})
    return out


def findings(qd: QueueData, today: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """(findings, projects) from the posting and the extracted reports."""
    today = today or datetime.now()
    R = _requests(qd)
    if not R:
        return [], []
    P = [r for r in R if r["pending"]]
    post = _date(qd.posting)
    loc = f"APS Active Queue posting {post}"
    F = []

    def add(statement, location=loc):
        F.append({"topic": "interconnection_queue", "statement": statement, "location": location})

    tot = sum(r["mw"] for r in R)
    ins = [r for r in R if r["Process"] == "In service"]
    sur = [r for r in R if r["Process"] == "Surplus"]
    add(f"APS's Active Queue posted {post} lists {len(R)} requests totalling {tot:,.0f} MW: {len(P)} not yet in "
        f"service ({sum(r['mw'] for r in P):,.0f} MW), {len(ins)} in service ({sum(r['mw'] for r in ins):,.0f} MW) "
        f"and {len(sur)} surplus requests ({sum(r['mw'] for r in sur):,.0f} MW).")

    by = collections.defaultdict(lambda: [0, 0.0])
    for r in P:
        by[r["Process"]][0] += 1
        by[r["Process"]][1] += r["mw"]
    order = ["2026 Cluster", "2025 Cluster", "2024 Cluster", "Transition Cluster", "Pre-2024 (Transition or serial)"]
    add("Pending requests by study cycle: " + "; ".join(f"{p} {by[p][0]} requests / {by[p][1]:,.0f} MW"
                                                     for p in order if p in by) + ".")

    def top(key, n, rows=P):
        c = collections.Counter()
        for r in rows:
            c[key(r)] += r["mw"]
        return c.most_common(n)
    pend_mw = sum(r["mw"] for r in P)
    add("Pending MW by technology: " + ", ".join(f"{k} {_gw(v)} ({v / pend_mw:.0%})" for k, v in top(lambda r: r["tech"], 7))
        + ".")
    svc = top(lambda r: "NRIS" if "Network" in r.get("Interconnection Request Type", "") and "Energy and" not in
              r.get("Interconnection Request Type", "") else "ERIS" if "Energy Resource" in
              r.get("Interconnection Request Type", "") else "NRIS+ERIS" if "Energy and" in
              r.get("Interconnection Request Type", "") else "other", 4)
    add("Pending MW by service: " + ", ".join(f"{k} {_gw(v)}" for k, v in svc) + ".")
    add("Pending MW by queue status: " + ", ".join(f"{k} {_gw(v)}" for k, v in top(lambda r: r["status"], 8)) + ".")

    areas = top(lambda r: r["Study Area"], 10)
    add("Pending MW by APS study area: " + ", ".join(f"{k} {_gw(v)}" for k, v in areas) + ".",
        f"{loc}; study area from report rosters, POI rules and county")

    cmap = contracted.load(qd)
    hit = [r for r in P if r["FERC Q Number"] in cmap]
    ann = [r for r in hit if cmap[r["FERC Q Number"]].get("announced")]
    if hit:
        by_c = contracted.summarize(hit, cmap, mw_of=lambda r: r["mw"])
        by_a = collections.defaultdict(lambda: collections.defaultdict(float))
        for r in hit:
            by_a[r["Study Area"]][cmap[r["FERC Q Number"]]["class"]] += r["mw"]
        add(f"{len(hit)} pending requests ({sum(r['mw'] for r in hit):,.0f} MW) have known or likely offtake: "
            + "; ".join(f"{contracted.CLASSES[k]} {v['n']} / {v['mw']:,.0f} MW" for k, v in by_c.items())
            + ". By study area: " + "; ".join(
                f"{a} " + ", ".join(f"{contracted.CLASSES[k]} {by_a[a][k]:,.0f} MW" for k in contracted.CLASSES if by_a[a].get(k))
                for a in sorted(by_a, key=lambda a: -sum(by_a[a].values()))) + "."
            + (f" {len(ann)} of them ({sum(r['mw'] for r in ann):,.0f} MW) are announced, not yet approved or executed: "
               + ", ".join(r["FERC Q Number"] for r in ann) + "." if ann else ""),
            "contracted.csv (curated from public offtake evidence: APS RFP results, ACC filings, PPA announcements)")
    ins_hit = [r for r in R if r["Process"] == "In service" and r["FERC Q Number"] in cmap]
    if ins_hit:
        by_i = contracted.summarize(ins_hit, cmap, mw_of=lambda r: r["mw"])
        add(f"Separately, {len(ins_hit)} queue requests already in service ({sum(r['mw'] for r in ins_hit):,.0f} MW) have known "
            "offtake: " + "; ".join(f"{contracted.CLASSES[k]} {v['n']} / {v['mw']:,.0f} MW" for k, v in by_i.items()) + ".",
            "contracted.csv (curated from public offtake evidence)")

    names, by_poi = {}, collections.Counter()
    nreq = collections.Counter()
    for r in P:
        k = poi_key(r["Requested POI"])
        names.setdefault(k, r["Requested POI"])
        by_poi[k] += r["mw"]
        nreq[k] += 1
    add("Largest pending POIs: " + "; ".join(f"{names[k]} {_gw(v)} ({nreq[k]} requests)"
                                            for k, v in by_poi.most_common(8)) + ".")

    sus = [r for r in P if "Suspension" in r["status"]]
    if sus:
        add(f"{len(sus)} pending requests ({sum(r['mw'] for r in sus):,.0f} MW) are in customer-initiated suspension: "
            + ", ".join(f"{r['FERC Q Number']} {r['mw']:,.0f} MW {r['tech']} at {r['Requested POI']}"
                        for r in sorted(sus, key=lambda r: -r["mw"])[:4]) + ".")
    stale = [r for r in P if (y := _isd_year(r["isd"])) and y < today.year
             and not r["status"].startswith(LIVE_STATUSES)]
    if stale:
        add(f"Attrition candidates: {len(stale)} pending requests ({sum(r['mw'] for r in stale):,.0f} MW) show a "
            f"projected in-service date before {today.year} and are not in design or construction, e.g. "
            + ", ".join(f"{r['FERC Q Number']} {r['mw']:,.0f} MW at {r['Requested POI']} (ISD {r['isd']}, {r['status']})"
                        for r in sorted(stale, key=lambda r: -r["mw"])[:4]) + ".")

    years = collections.defaultdict(lambda: [0, 0.0])
    for r in R:
        if r.get("Submission Year"):
            years[r["Submission Year"]][0] += 1
            years[r["Submission Year"]][1] += r["mw"]
    recent = [y for y in sorted(years) if y >= str(today.year - 3)]
    add("Requests still in the queue by submission year: " + "; ".join(f"{y} {years[y][0]} / {years[y][1]:,.0f} MW"
                                                                     for y in recent) + ".")

    # cluster / facilities study reports
    studies = _rows(qd.deliver / "aps_studies.csv")
    U = [u for u in _rows(qd.export / "network_upgrades.csv")
         if u.get("is_latest_for_cycle") == "True" and u.get("cycle") != "Serial"]
    if studies:
        dates = sorted(s.get("report_date", "") for s in studies if s.get("report_date"))
        add(f"{len(studies)} APS cluster system impact and facilities study reports are extracted "
            f"({dates[0] if dates else ''} to {dates[-1] if dates else ''}), covering the Transition, 2024 and 2025 "
            "clusters in eight study areas; the 2026 cluster is not yet studied.", "APS OASIS cluster study reports")
    if U:
        cost = collections.defaultdict(float)
        for u in U:
            cost[u["study_area"]] += _mw(u.get("cost_usd"))
        add("Network and station upgrade cost in the latest report of each cycle, by study area: "
            + ", ".join(f"{a} ${v / 1e6:,.0f}M" for a, v in sorted(cost.items(), key=lambda kv: -kv[1]) if v) + ".",
            "APS cluster study reports, latest version per cycle")
        big = sorted(U, key=lambda u: -_mw(u.get("cost_usd")))[:5]
        add("Largest upgrades in the latest reports: " + "; ".join(
            f"{u['upgrade']} (${_mw(u['cost_usd']) / 1e6:,.0f}M, {u['study_area']} {u['phase']}"
            + (f", first triggered by {u['first_q']} at {_mw(u['trigger_cum_mw']):,.0f} MW cumulative" if u.get("first_q") else "")
            + ")" for u in big) + ".", "APS cluster study reports, latest version per cycle")

    projects = [{"name": r["FERC Q Number"], "type": r["tech"],
                 "kv": (f"{poi_kv(r['Requested POI']):g}" if poi_kv(r["Requested POI"]) else ""),
                 "mw": f"{r['mw']:g}", "poi": r["Requested POI"], "status": f"{r['status']} ({r['Process']})",
                 "date": r["isd"]} for r in P]
    return F, projects


def write_area_source(area, qd: QueueData) -> dict:
    """digests/QUEUE.json + the QUEUE source row, so the summary cites it."""
    F, projects = findings(qd)
    meta = qd.meta
    inputs = [qd.queue_by_area, qd.queue_clean, qd.deliver / "aps_studies.csv", qd.export / "network_upgrades.csv",
              qd.contracted]
    h = hashlib.sha256()
    for p in inputs:
        if p.exists():
            h.update(p.read_bytes())
    sha = h.hexdigest()[:16]
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    post = _date(qd.posting)
    digest = {"title": f"APS interconnection queue: Active Queue posting {post} and cluster study reports",
              "publisher": "Arizona Public Service (OASIS), extracted on this machine",
              "date": post, "doc_type": "data (computed locally)",
              "summary": (f"The APS Active Queue posting of {post} and the cluster system impact / facilities study "
                          f"reports posted through {meta.get('reports_through', '')}, parsed and summarised on this "
                          "machine by scripts/aps_queue.py (task 23)."),
              "relevance": "high", "findings": F, "projects": projects, "open_questions": [],
              "source_id": "QUEUE", "model": "computed (no language model)", "digested_at": now,
              "usage": {"input": 0, "output": 0, "cache_read": 0}, "cost_usd": 0.0}
    (area.root / "digests").mkdir(exist_ok=True)
    (area.root / "digests" / "QUEUE.json").write_text(json.dumps(digest, indent=2), encoding="utf-8")
    rows = [r for r in area.sources() if r["id"] != "QUEUE"]
    at = 1 if rows and rows[0]["id"] == "EIA" else 0
    rows.insert(at, {"id": "QUEUE", "kind": "data", "title": digest["title"], "added_by": "aps_queue",
                     "added": now, "status": "active", "sha": sha, "digested_sha": sha, "url": meta.get("source", ""),
                     "publisher": digest["publisher"], "date": post, "doc_type": "data", "relevance": "high"})
    area.save_sources(rows)
    area.log(f"queue data ({post}) updated: {len(F)} findings, {len(projects)} pending requests (computed, $0)")
    return digest
