"""Local rebuild: posting markdown -> queue CSVs -> study areas -> ladders -> pages -> export."""

from __future__ import annotations

import json

from tara_automation.ingest.aps import AREAS, QueueData, areas, export, ladder, pages, queue


def build_queue(qd: QueueData, log=print) -> None:
    rows, odd = queue.parse_markdown(queue.read_posting(qd.queue_raw))
    queue.write_csv(rows, qd.queue_csv, queue.HEADER)
    log(f"queue: {len(rows)} requests from {qd.queue_raw.name}" + (f"; non-numeric MW: {odd}" if odd else ""))
    clean = queue.clean(rows)
    queue.write_csv(clean, qd.queue_clean)
    rows, summary = areas.assign(qd)
    queue.write_csv(rows, qd.queue_by_area, areas.COLUMNS)
    log(summary)


def build_ladders(qd: QueueData, keys=None, log=print) -> None:
    qd.ladders.mkdir(parents=True, exist_ok=True)
    for k in keys or AREAS:
        area, rx, _ = AREAS[k]
        d = ladder.build(qd, area, rx)
        (qd.ladders / f"{k}.json").write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
        un = ladder.build_unstudied(qd, area)
        (qd.ladders / f"{k}_2026.json").write_text(json.dumps(un, indent=1), encoding="utf-8")
        log(f"ladder {k}: {len(d['phases'])} reports, {len(un)} unstudied 2026 requests")


def build_pages(qd: QueueData, keys=None, log=print) -> None:
    from tara_automation.ingest.aps import contracted
    cmap, problems = contracted.read(qd.contracted)
    if qd.contracted.exists():
        log(f"contracted.csv: {len(cmap)} requests with known / likely offtake" + (f"; {len(problems)} row(s) skipped" if problems else ""))
        for p in problems:
            log("  " + p)
    for k in keys or AREAS:
        p = pages.render(qd, k)
        log(f"page {p.name}: {p.stat().st_size // 1024} KB")


def build_all(qd: QueueData, pages_only: bool = False, log=print) -> None:
    if not pages_only:
        build_queue(qd, log)
        build_ladders(qd, log=log)
    build_pages(qd, log=log)
    if not pages_only:
        log("export: " + ", ".join(f"{k} {v}" for k, v in export.export(qd).items()))
