"""Parsers for TARA comm-engine native CSV reports (CaseSum, AreaSum, BusData).

Moved from the retired validate.py (comm-vs-pygem cross-engine validation was
removed 2026-09-24; these parsers are what compare.py still needs).
"""

from __future__ import annotations

import re
from pathlib import Path



def parse_case_sum(path: Path) -> dict:
    """CaseSum.csv -> {'converged': bool, 'iterations': int, counts...}"""
    out: dict = {}
    count_keys = {
        "Buses": "n_buses", "Loads": "n_loads", "Machines": "n_generators",
        "NonTranBrn": "n_branches", "Transformers": "n_transformer_legs",
        "Total Areas": "n_areas", "Total Zones": "n_zones",
    }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0]:
            continue
        key = parts[0]
        if key == "ConvStatus":
            out["converged"] = parts[1].lower() == "solved"
        elif key == "Iterations":
            out["iterations"] = int(parts[1])
        elif key in count_keys:
            try:
                out[count_keys[key]] = int(parts[1])
            except ValueError:
                pass
    return out


def parse_area_sum(path: Path) -> dict[int, dict]:
    """AreaSum.csv (tarea 1) -> {area: {'gen','load','losses'}} (MW)."""
    areas: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) > 13 and re.fullmatch(r"\d+", p[0]):
            areas[int(p[0])] = {
                "gen": float(p[4]), "load": float(p[7]), "losses": float(p[13]),
            }
    return areas


def parse_bus_data(path: Path) -> dict[int, tuple[float, float]]:
    """BusData.csv -> {bus: (vmag_pu, angle_deg)}."""
    buses: dict[int, tuple[float, float]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) > 9 and re.fullmatch(r"\d+", p[0]):
            buses[int(p[0])] = (float(p[7]), float(p[9]))
    return buses
