"""Data ingestion adapters (task 23): raw queue / IRP / TYP drops -> project library.

One sub-package per source. Each maps its source to the projects.xlsx schema
(`tara_automation.projects`) and reports what it kept and what needs a person.
"""

from pathlib import Path

# research area id -> adapter package (each has QueueData + desk.view / desk.resolve)
ADAPTERS = {"APS": "tara_automation.ingest.aps"}


def queue_data(area_id: str, area_root: Path):
    """The QueueData of a research area's queue folder, or None when the area has no adapter or no data."""
    mod = ADAPTERS.get(str(area_id).upper())
    root = Path(area_root) / "data" / "queue"
    if not mod or not (root / "queue.yaml").exists():
        return None
    import importlib
    return importlib.import_module(mod).QueueData(root)


def desk_view(area_id: str, area_root: Path) -> dict | None:
    """What the desk's Interconnection queue tab shows; None when there is nothing to show."""
    qd = queue_data(area_id, area_root)
    if qd is None:
        return None
    import importlib
    try:
        return importlib.import_module(ADAPTERS[str(area_id).upper()] + ".desk").view(qd)
    except Exception as e:  # noqa: BLE001  - a broken queue folder must not break the area page
        return {"error": f"{type(e).__name__}: {e}"}


def update_research_source(area) -> dict | None:
    """Write the area's QUEUE digest + source row from its queue data (no model, $0)."""
    qd = queue_data(area.id, area.root)
    if qd is None:
        return None
    import importlib
    return importlib.import_module(ADAPTERS[str(area.id).upper()] + ".digest").write_area_source(area, qd)
