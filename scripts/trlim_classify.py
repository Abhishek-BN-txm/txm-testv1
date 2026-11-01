"""Classify TrLim constraints as load-local, source-side or network, and find
each path's limit once constraints local to the sink are set aside.

    python scripts/trlim_classify.py --study APS_LargeLoad_Deliverability_C26 [--radius 2]

For every (hub bus -> sink) path in the study's ATC runs:
  - load-local : the monitored facility has an end within --radius branches
                 (lines or transformers, in service, in the study's basecase)
                 of a sink bus. These are the load's own service connection
                 and the substation next to it - what a new load's
                 interconnection would build anyway.
  - source-side: an end within --radius branches of the hub bus being tested
                 (getting the power out of the hub).
  - sink-area  : (area sinks, e.g. CAISO) an end inside a sink area.
  - network    : none of these.
The "without load-local" limit also sets sink-area constraints aside.
Only constraints with headroom before the transfer are ranked (as in the
atcsummary); already-overloaded facilities the transfer worsens are counted.

Writes 05_Analysis/<study>_trlim_classified.{md,csv}. Reads only TARA's CSVs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import deque
from pathlib import Path

from tara_automation.footprint import load_network
from tara_automation.machine import REPO_ROOT


def _dist(adj, src, maxd):
    d = {s: 0 for s in src}
    q = deque(src)
    while q:
        u = q.popleft()
        if d[u] >= maxd:
            continue
        for v in adj.get(u, ()):
            if v not in d:
                d[v] = d[u] + 1
                q.append(v)
    return d


def _rows(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = next(k for k, l in enumerate(lines) if l.startswith("Sending System"))
    rd = csv.reader(lines[i:])
    head = [h.strip() for h in next(rd)]
    for r in rd:
        if len(r) < len(head) - 1 or not r[0].strip():
            continue
        yield {h: (r[k].strip() if k < len(r) else "") for k, h in enumerate(head)}


def _f(x):
    try:
        return float(x)
    except ValueError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", required=True)
    ap.add_argument("--radius", type=int, default=1)
    a = ap.parse_args(argv)
    sdir = REPO_ROOT / "studies" / a.study
    cfg = json.loads((sdir / "study.json").read_text(encoding="utf-8"))
    runs = REPO_ROOT / "runs"
    base_run = next(iter(cfg["runs"]["basecase"].values()))
    net = load_network(runs / str(base_run) / "results")
    groups = _groups(sdir)
    out, md = [], []
    md += [f"# {a.study} - constraints classified (radius {a.radius} branches)", "",
           f"Network: basecase run {base_run}. *Load-local* = facility within "
           f"{a.radius} branches of a sink bus; *source-side* = within {a.radius} "
           f"of the hub bus tested. Limits rank only constraints with headroom "
           f"before the transfer.", ""]
    for key, n in cfg["runs"]["atc"].items():
        csvs = sorted((runs / str(n) / "results").glob("*_TrLim.csv"))
        recv = key.split(">", 1)[1] if ">" in key else cfg["atc"]["recv"]
        members = groups.get(recv, [])
        sink_areas = {-m for m in members if m < 0}
        sink_d = _dist(net.adjacency, [m for m in members if m > 0], a.radius)
        by_path: dict[str, list] = {}
        for r in _rows(csvs[0]):
            by_path.setdefault(r["Sending System"].rstrip("_"), []).append(r)
        md += [f"## {recv} (run {n})", "",
               "| Hub bus | All-in limit (MW) | Binding | Class | Limit without load-local (MW) | Binding | Class | Contingency | First source-side (MW) | Worsened |",
               "|---|---:|---|---|---:|---|---|---|---:|---:|"]
        for path, rows in by_path.items():
            hub = int(path.split("_", 1)[0])
            hub_d = _dist(net.adjacency, [hub], a.radius)
            ranked, worsened = [], 0
            for r in rows:
                tl, hr = _f(r["TrLim"]), _f(r["HeadRoom"])
                if tl is None:
                    continue
                if hr is not None and hr <= 0:
                    worsened += 1
                    continue
                if tl <= 0:
                    continue
                ends = [int(r["Fr Bus"]), int(r["To Bus"])]
                cls = []
                if any(e in sink_d for e in ends):
                    cls.append("load-local")
                elif sink_areas and any(net.buses.get(e) and net.buses[e].area in sink_areas
                                        for e in ends):
                    cls.append("sink-area")
                if any(e in hub_d for e in ends):
                    cls.append("source-side")
                ranked.append((tl, " ".join(r["Monitored Facility"].split()),
                               "+".join(cls) or "network",
                               " ".join(r["Cont Name"].split()) or "Base Case", r))
            ranked.sort(key=lambda t: t[0])
            allin = ranked[0] if ranked else None
            nonlocal_ = next((t for t in ranked if "load-local" not in t[2]
                              and "sink-area" not in t[2]), None)
            src = next((t for t in ranked if "source-side" in t[2]), None)
            fmt = lambda t: (f"{t[0]:.0f}", _short(t[1]), t[2]) if t else ("-", "-", "-")
            A, B = fmt(allin), fmt(nonlocal_)
            md.append(f"| {path} | {A[0]} | {A[1]} | {A[2]} | {B[0]} | {B[1]} | {B[2]} | "
                      f"{_short(nonlocal_[3]) if nonlocal_ else '-'} | "
                      f"{f'{src[0]:.0f} ({_short(src[1])})' if src else '-'} | {worsened} |")
            out.append({"run": n, "sink": recv, "hub": path,
                        "allin_mw": allin[0] if allin else None, "allin_facility": allin[1] if allin else "",
                        "allin_class": allin[2] if allin else "",
                        "nonlocal_mw": nonlocal_[0] if nonlocal_ else None,
                        "nonlocal_facility": nonlocal_[1] if nonlocal_ else "",
                        "nonlocal_class": nonlocal_[2] if nonlocal_ else "",
                        "nonlocal_contingency": nonlocal_[3] if nonlocal_ else "",
                        "first_source_side_mw": src[0] if src else None,
                        "first_source_side_facility": src[1] if src else "",
                        "worsened_preexisting": worsened})
        md.append("")
    ad = sdir / "05_Analysis"
    (ad / f"{a.study}_trlim_classified.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    with open(ad / f"{a.study}_trlim_classified.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"wrote {ad / (a.study + '_trlim_classified.md')} ({len(out)} paths)")
    return 0


def _short(fac: str) -> str:
    """'14266 BROADWAY 230 14237 RUNWAY S 230 1 1' -> 'BROADWAY-RUNWAY S 230'."""
    import re
    m = re.match(r"(\d+) (.+?) ([\d.]+) (\d+|3WXFMR) (.+?) ([\d.]+)? ?(\S+)? ?(\S+)?$", fac)
    if not m:
        return fac[:40]
    return f"{m.group(2)}-{m.group(5)} {m.group(3)}".replace("  ", " ")


def _groups(sdir: Path) -> dict[str, list[int]]:
    """Bus lists per subsystem from the compiled footprint .sub (BUS/AREA lines)."""
    sub = sdir / "03_TARA_Inputs" / "footprint" / "footprint.sub"
    g, cur = {}, None
    for line in sub.read_text(encoding="utf-8", errors="replace").splitlines():
        w = line.split("//")[0].split()
        if not w:
            continue
        if w[0] == "SUBSYSTEM" and len(w) > 1:
            cur = w[1]
            g[cur] = []
        elif w[0] == "END":
            cur = None
        elif cur and w[0] == "BUS" and len(w) > 1:
            g[cur].append(int(w[1]))
        elif cur and w[0] == "AREA" and len(w) > 1:
            g[cur].append(-int(w[1]))          # negative = whole area
    return g


if __name__ == "__main__":
    sys.exit(main())
