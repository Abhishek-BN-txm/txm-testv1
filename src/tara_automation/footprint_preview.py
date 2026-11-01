"""Bus lists for the Footprint Builder: compile a footprint against a solved
case with the real compiler and say, bus by bus, why each one was selected.

Used by the study desk (POST /api/resolve). The page's own in-browser preview
mirrors the compiler; this is the compiler itself, so it is the authority.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from tara_automation.footprint import FootprintError, compile_footprint, load_network

COLUMNS = ["bus", "name", "kv", "area", "zone", "owner", "how", "located_by",
           "lat", "lon"]
REQUIRED = ("BusData.csv", "BranchData.csv", "TransfmrData.csv")


def basecase_runs(runs_root: Path) -> list[dict]:
    """Successful runs that carry the bus/branch/transformer tables, newest
    first: the networks a footprint can be compiled against."""
    out = []
    for d in Path(runs_root).iterdir():
        if not (d.is_dir() and d.name.isdigit()):
            continue
        res = d / "results"
        if not all((res / f).exists() for f in REQUIRED):
            continue
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("status") != "success":
            continue
        out.append({"run": int(d.name), "case": m.get("case", ""),
                    "case_name": Path(str(m.get("case", ""))).name,
                    "label": m.get("label", ""), "created_at": m.get("created_at", "")})
    return sorted(out, key=lambda r: -r["run"])


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a).lower() == str(b).lower()


def find_basecase(case: Path, runs_root: Path) -> dict | None:
    """The newest solved basecase run of `case`, made AFTER the case file was
    last modified (an older run would describe an older version of the case)."""
    from datetime import datetime
    case = Path(case)
    # manifests record whole seconds, so compare at that resolution
    mtime = datetime.fromtimestamp(case.stat().st_mtime).replace(microsecond=0)
    for r in basecase_runs(runs_root):
        if not _same_path(r["case"], str(case)):
            continue
        try:
            created = datetime.fromisoformat(r["created_at"])
        except ValueError:
            continue
        if created >= mtime:
            return r
    return None


def solve_basecase(case: Path, runs_root: Path, tara_exe: Path,
                   label: str = "study desk basecase") -> int:
    """One TARA basecase run of `case`, through the run manager (manifest +
    RUN_LOG line), producing the bus / branch / transformer tables the
    footprint compiler reads. Returns the run number; raises on failure."""
    from tara_automation.commfile import CommEngine
    from tara_automation.run_manager import RunManager
    from tara_automation.study import detect_format_version
    fmt, ver = detect_format_version(Path(case))
    rm = RunManager(runs_root)
    run = rm.create_run(engine="comm", case=Path(case), label=label,
                        options={"format": fmt, "version": ver,
                                 "purpose": "Footprint Builder bus list"})
    eng = CommEngine(tara_exe=tara_exe)
    eng.prepare(run, Path(case), fmt=fmt, version=ver)
    rc = eng.execute(run)
    checks = eng.check_outputs(run)
    missing = [t for t, ok in checks.items() if not ok]
    if rc != 0 or missing:
        run.finalize("failed", f"exit={rc}; missing {len(missing)}")
        raise RuntimeError(f"TARA basecase run {run.number} failed (exit {rc}, "
                           f"{len(missing)} tables missing); see runs/{run.number}/logs")
    run.finalize("success", f"all {len(checks)} tables")
    return run.number


def preview(yaml_text: str, results_dir: Path) -> dict:
    """Compile `yaml_text` against the network in `results_dir`.

    Returns every group's size and, per group, the bus rows (COLUMNS). `how`
    is "inside" (located inside a polygon), "flooded" (no location, reached
    through the network from inside) or "selector" (areas, buses, near ...)."""
    with tempfile.TemporaryDirectory() as tmp:
        spec = Path(tmp) / "footprint.yaml"
        spec.write_text(yaml_text, encoding="utf-8")
        net = load_network(Path(results_dir))
        fp = compile_footprint(spec, net)
    groups = {}
    for g in fp.groups:
        d = fp.polygon_detail.get(g.name, {})
        inside, flooded = d.get("inside", set()), d.get("flooded", set())
        rows = []
        for n in sorted(g.buses):
            b = net.buses[n]
            how = "inside" if n in inside else "flooded" if n in flooded else "selector"
            rows.append([n, b.name, b.kv, b.area, b.zone, b.owner, how, b.located_by or "",
                         None if b.lat is None else round(b.lat, 5),
                         None if b.lon is None else round(b.lon, 5)])
        kv = Counter(net.buses[n].kv for n in g.buses)
        groups[g.name] = {
            "role": g.role, "form": g.form, "total": len(g.buses),
            "inside": sum(1 for r in rows if r[6] == "inside"),
            "flooded": sum(1 for r in rows if r[6] == "flooded"),
            "selector": sum(1 for r in rows if r[6] == "selector"),
            "stops": len(d.get("stops", ())), "capped": len(d.get("capped", ())),
            "polygon": bool(d),
            "kv": sorted(([k, v] for k, v in kv.items()), key=lambda kv_: -kv_[0]),
            "areas": sorted({net.buses[n].area for n in g.buses}),
            "rows": rows,
        }
    return {"columns": COLUMNS, "groups": groups, "warnings": fp.warnings,
            "notes": fp.polygon_notes, "network": net.source,
            "located": (net.located or {}).get("by_source")}


__all__ = ["COLUMNS", "FootprintError", "basecase_runs", "preview"]
