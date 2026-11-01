"""Ladder JSON (+ serial, 2026 requests, notes, contingent facilities) -> one self-contained HTML page."""

from __future__ import annotations

import json
from pathlib import Path

from tara_automation.ingest.aps import AREAS, QueueData

TEMPLATE = Path(__file__).with_name("templates") / "ladder_template.html"


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def page_data(qd: QueueData, key: str) -> dict:
    d = _load(qd.ladders / f"{key}.json")
    if d is None:
        raise FileNotFoundError(f"{qd.ladders / (key + '.json')} not found; run the build first")
    s = _load(qd.serial / f"{key}_serial_ladder.json")
    if s and s.get("projects"):
        d["serial"] = s
    d["unstudied"] = _load(qd.ladders / f"{key}_2026.json") or []
    notes = _load(qd.notes / f"{key}.json") or {}
    if "serial" in d:
        notes.setdefault("serial", d["serial"].get("view_note", ""))
        notes.setdefault("full", d["serial"].get("full_note", ""))
    d["notes"] = {a: b for a, b in notes.items() if b}
    c = _load(qd.contingent / f"{key}.json")
    if c:
        d["contingent"] = c
    d["queue"], d["geo"] = _map_data(qd, d)
    from tara_automation.ingest.aps import contracted
    cmap, problems = contracted.read(qd.contracted)
    d["contracted"] = cmap                        # {} without contracted.csv: no highlight
    d["contracted_meta"] = {"present": qd.contracted.exists(), "problems": problems, "classes": contracted.CLASSES,
                             "none": contracted.NONE_LABEL}
    return d


def _map_data(qd: QueueData, d: dict):
    """Queue status / ISD per request (for the colour modes) and the map layer.
    The map never stops a page: without the grid or the workbook it is simply emptier."""
    import csv
    import html
    queue = {}
    if qd.queue_clean.exists():
        area = {}
        if qd.queue_by_area.exists():
            with open(qd.queue_by_area, encoding="utf-8", newline="") as f:
                area = {r["FERC Q Number"]: r for r in csv.DictReader(f)}
        with open(qd.queue_clean, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                q = r["FERC Q Number"]
                queue[q] = {"status": html.unescape(r.get("Current Status", "")), "isd": r.get("LGP Projected In Service Date", ""),
                            "tech": r.get("Energy Source Type", ""), "county": r.get("County (normalized)", ""),
                            "state": r.get("Project Location State", ""), "process": area.get(q, {}).get("Process", ""),
                            "poi": r.get("Requested POI", "")}
    rows = [r for p in d.get("phases", []) for r in p.get("projects", [])] + list((d.get("serial") or {}).get("projects", [])) \
        + list(d.get("unstudied") or []) + list(d.get("prior") or [])
    pois = [x for r in rows for x in (r.get("poi"), r.get("poi_queue"), queue.get(r.get("q"), {}).get("poi"))]
    ups = [u["label"] for p in d.get("phases", []) for u in p.get("upgrades", [])] + \
        [u["label"] for u in (d.get("serial") or {}).get("upgrades", [])]
    try:
        from tara_automation.ingest.aps import geo
        layer = geo.page_layer(qd, pois, ups, {q: v["poi"] for q, v in queue.items()})
    except Exception as e:  # noqa: BLE001
        layer = {"error": f"{type(e).__name__}: {e}", "pois": {}, "upgrades": {}, "grid": None}
    return queue, layer


def render(qd: QueueData, key: str, out_dir: Path | None = None) -> Path:
    title = AREAS[key][2]
    d = page_data(qd, key)
    t = TEMPLATE.read_text(encoding="utf-8")
    t = t.replace("__TITLE__", f"{title} Ladder").replace("__AREA__", title)
    t = t.replace("__DATA__", json.dumps(d, default=str).replace("</", "<\\/"))
    out = Path(out_dir or qd.pages) / f"{key}_ladder.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(t, encoding="utf-8")
    return out
