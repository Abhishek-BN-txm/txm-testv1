"""Parsers for TARA contingency violation reports (BranchViol / VoltViol).

TARA native CSVs: ~10 preamble lines (case, study files, cutoffs), then a
header row, then data. Parsers return lists of dicts; `summarize` produces
the digest-style rollup used by run finalization and reviews.
"""

from __future__ import annotations

import re
from pathlib import Path

# TARA writes these CSVs UNQUOTED, but some text fields (notably the owner-name
# column, e.g. "Gila River Power, LP") contain literal commas. csv.DictReader
# therefore mis-maps those rows, shifting every column after the owner name —
# which is what produced phantom loadings like GILARIVR 500 kV at 459% (really
# ~82%). We reconstruct the over-split field instead of trusting the split.
_OWNER_NAME_COLS = ("Owners Name", "Owner Name")


def _split_fields(line: str) -> list[str]:
    fields = [x.strip() for x in line.split(",")]
    if fields and fields[-1] == "":   # drop the trailing-comma artifact only
        fields.pop()
    return fields


def _read_table(path: Path) -> tuple[dict, list[dict]]:
    """Split preamble metadata from the data table (comma-safe)."""
    meta: dict = {}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_i = None
    for i, line in enumerate(lines):
        m = re.search(r"Contingency File: .*\(total (\d+) contingencies\)", line)
        if m:
            meta["n_contingencies"] = int(m.group(1))
        if re.search(r"cutoff", line, re.IGNORECASE):
            meta.setdefault("cutoff_note", line.strip())
        if "," in line and ("Cont Name" in line or "Cont ID" in line):
            header_i = i
            break
    if header_i is None:
        return meta, []

    header = _split_fields(lines[header_i])
    ncols = len(header)
    owner_idx = next((k for k, h in enumerate(header)
                      if h in _OWNER_NAME_COLS), None)

    rows = []
    for line in lines[header_i + 1:]:
        if not line.strip():
            continue
        f = _split_fields(line)
        # Merge any over-split (owner-name comma) back into the owner column so
        # every trailing numeric column lands under the right header.
        if owner_idx is not None and len(f) > ncols:
            extra = len(f) - ncols
            f[owner_idx:owner_idx + 1 + extra] = [
                "; ".join(f[owner_idx:owner_idx + 1 + extra])]
        if len(f) < ncols:
            f += [""] * (ncols - len(f))
        rows.append({header[k].strip(): f[k] for k in range(ncols)
                     if header[k].strip()})
    return meta, rows


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_branch_viol(path: Path) -> tuple[dict, list[dict]]:
    """BranchViol.csv -> (meta, records). Adds parsed float loading fields.

    Rating convention (user ruling 2026-07-30): Rate Cont = 9999 means no
    emergency rating is defined — loadings are judged against the normal
    rating, and the record is flagged `no_emergency_rating` so summaries
    carry a warning rather than silently trusting the number.
    """
    meta, rows = _read_table(path)
    for r in rows:
        ac_raw = (r.get("Final AC %Loading") or "").strip()
        r["ac_loading"] = _f(ac_raw)
        r["dc_loading"] = _f(r.get("Final DC %Loading"))
        r["nconv"] = ac_raw.upper() == "NCONV"
        r["is_base_case"] = "base case" in r.get("Cont Name", "").lower()
        rate_cont = _f(r.get("Rate Cont (MVA)"))
        r["no_emergency_rating"] = (rate_cont is not None and rate_cont >= 9998)
    return meta, rows


def parse_volt_viol(path: Path) -> tuple[dict, list[dict]]:
    meta, rows = _read_table(path)
    for r in rows:
        r["is_base_case"] = "base case" in r.get("Cont Name", "").lower()
    return meta, rows


def summarize(branch_path: Path, volt_path: Path,
              overload_threshold: float = 100.0, top_n: int = 10) -> dict:
    """Roll both reports up into headline numbers + top overloads."""
    bmeta, brows = parse_branch_viol(branch_path)
    vmeta, vrows = parse_volt_viol(volt_path)

    over = [r for r in brows
            if (r["ac_loading"] or 0) >= overload_threshold]
    base_over = [r for r in over if r["is_base_case"]]
    cont_over = [r for r in over if not r["is_base_case"]]
    worst = sorted(over, key=lambda r: -(r["ac_loading"] or 0))[:top_n]
    no_emerg = sorted({r.get("Monitored Facility", "").strip()
                       for r in brows if r["no_emergency_rating"]})
    nconv_conts = sorted({r.get("Cont Name", "").strip()
                          for r in brows if r.get("nconv")})

    return {
        "n_contingencies": bmeta.get("n_contingencies"),
        "branch_records": len(brows),
        "overloads_ge_100": len(over),
        "base_case_overloads": len(base_over),
        "contingency_overloads": len(cont_over),
        "volt_records": len(vrows),
        "non_convergent_records": sum(1 for r in brows if r.get("nconv")),
        "non_convergent_contingencies": nconv_conts,
        "no_emergency_rating_facilities": no_emerg,
        "worst_overloads": [
            {
                "facility": r.get("Monitored Facility", "").strip(),
                "kv": r.get("kVs"), "area": r.get("Areas Name"),
                "contingency": r.get("Cont Name", "").strip(),
                "ac_loading": r["ac_loading"],
                "rate_mva": r.get("Rate Cont (MVA)") if not r["is_base_case"]
                            else r.get("Rate Base (MVA)"),
                "no_emergency_rating": r["no_emergency_rating"],
            }
            for r in worst
        ],
    }


def summary_line(s: dict) -> str:
    """One-line form for RUN_LOG / manifest."""
    line = (f"{s['n_contingencies']} cons; {s['branch_records']} branch records, "
            f"{s['overloads_ge_100']} >=100% ({s['base_case_overloads']} base, "
            f"{s['contingency_overloads']} post-cont); "
            f"{s['volt_records']} voltage records")
    nc = s.get("non_convergent_records", 0)
    if nc:
        line += (f"; {nc} non-convergent records "
                 f"({len(s.get('non_convergent_contingencies', []))} conts)")
    n = len(s.get("no_emergency_rating_facilities", []))
    if n:
        line += f"; WARNING: {n} facilities with no emergency rating (9999)"
    return line
