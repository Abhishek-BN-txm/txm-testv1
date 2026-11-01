"""Case-vs-case comparison from comm-engine run results.

Compares two runs (each = one solved case exported via the comm engine) and
produces a concise exception/top-N digest:

  - case summary + system totals deltas
  - bus voltages: top movers, counts over threshold, buses added/removed
  - branches: topology (added/removed/status), parameter changes (R/X/B),
    rating changes (A-D), flow and loading movers
  - generators: units added/removed/status, dispatch movers, net redispatch
  - loads: added/removed/status, load change movers, net load delta

Keys: bus (Bus#), gen (Bus#, ID), load (Bus#, ID),
branch (FrBus, ToBus, CKT, Section).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tara_automation.comm_reports import parse_area_sum, parse_case_sum

# thresholds (report-level, not pass/fail — comparison reports differences)
TOP_N = 15
V_EPS = 0.002        # pu, ignore dV below this
MW_EPS = 1.0         # MW, ignore flow/dispatch/load deltas below this
PARAM_REL_EPS = 1e-4  # relative change in R/X/Charg considered "changed"
RATE_EPS = 0.5       # MVA


def _f(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _data_rows(path: Path, min_cols: int):
    """Yield split rows of a TARA CSV whose first field is a bus number."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= min_cols and re.fullmatch(r"\d+", p[0]):
            yield p


# ── parsers ────────────────────────────────────────────────────────────────

def read_buses(path: Path) -> dict[int, dict]:
    """BusData.csv -> {bus: {name, kv, area, v, ang}}"""
    out = {}
    for p in _data_rows(path, 10):
        out[int(p[0])] = {
            "name": p[1], "kv": p[2], "area": int(_f(p[3])),
            "v": _f(p[7]), "ang": _f(p[9]),
        }
    return out


def read_branches(path: Path) -> dict[tuple, dict]:
    """BranchData.csv -> {(min_bus, max_bus, CKT): {...}}

    Keys are orientation-normalized (unordered bus pair, uppercase circuit id)
    so EPC and RAW exports of the same branch match; flows are compared on
    magnitude for the same reason (metered-end sign conventions differ).
    Multi-section rows collapse to the section with the largest |MW|.
    """
    out = {}
    for p in _data_rows(path, 33):
        fr, to = int(p[0]), int(p[5])
        key = (min(fr, to), max(fr, to), p[10].upper())
        rec = {
            "fr_name": p[1], "to_name": p[6], "kv": p[2], "area": int(_f(p[3])),
            "st": p[13], "mw": _f(p[14]), "mva": _f(p[16]),
            "loss_mw": _f(p[17]),
            "r": _f(p[19]), "x": _f(p[20]), "charg": _f(p[21]),
            "rates": tuple(_f(p[i]) for i in (28, 29, 30, 31)),
            "is_tran": p[-2].strip() if len(p) > 41 else "",
        }
        if key not in out or abs(rec["mw"]) > abs(out[key]["mw"]):
            out[key] = rec
    return out


def read_gens(path: Path) -> dict[tuple, dict]:
    """GenData.csv -> {(bus, id): {...}}"""
    out = {}
    for p in _data_rows(path, 18):
        out[(int(p[0]), p[6].upper())] = {
            "name": p[1], "kv": p[2], "area": int(_f(p[3])),
            "st": p[7], "pgen": _f(p[8]), "qgen": _f(p[10]),
            "pmin": _f(p[11]), "pmax": _f(p[12]),
        }
    return out


def read_loads(path: Path) -> dict[tuple, dict]:
    """LoadData.csv -> {(bus, id): {...}}"""
    out = {}
    for p in _data_rows(path, 12):
        out[(int(p[0]), p[5].upper())] = {
            "name": p[1], "kv": p[2], "area": int(_f(p[3])),
            "st": p[6], "p": _f(p[10]),
        }
    return out


# ── comparison ─────────────────────────────────────────────────────────────

@dataclass
class CaseComparison:
    label_a: str
    label_b: str
    sections: dict = field(default_factory=dict)  # name -> list[str] lines

    def add(self, section: str, lines: list[str]) -> None:
        self.sections[section] = lines

    def to_markdown(self) -> str:
        out = [f"# Case comparison: {self.label_a} (A) vs {self.label_b} (B)", ""]
        for name, lines in self.sections.items():
            out.append(f"## {name}")
            out.append("")
            out.extend(lines if lines else ["- no differences above thresholds"])
            out.append("")
        return "\n".join(out)


def _loading(mva: float, rate: float) -> float | None:
    return 100.0 * mva / rate if rate > RATE_EPS else None


STAR_BUS_MIN = 990000  # RAW-read auto-generated 3W star buses (998xxx/999xxx)


def _is_format_artifact_branch(key: tuple) -> bool:
    """Branch records that exist only due to EPC-vs-RAW representation:
    series-cap bypass records (ckt BP) and 3W-transformer star-bus legs."""
    fr, to, ckt = key
    return ckt == "BP" or fr >= STAR_BUS_MIN or to >= STAR_BUS_MIN


def compare_cases(
    results_a: Path,
    results_b: Path,
    label_a: str = "A",
    label_b: str = "B",
    top_n: int = TOP_N,
    fmt_a: str | None = None,
    fmt_b: str | None = None,
) -> CaseComparison:
    cmp = CaseComparison(label_a, label_b)
    cross_format = bool(fmt_a and fmt_b and fmt_a != fmt_b)
    if fmt_a or fmt_b:
        note = [f"- A is {fmt_a or '?'}; B is {fmt_b or '?'}"]
        if cross_format:
            note.append(
                "- Cross-format comparison: differences from file representation "
                "are labeled as format artifacts below. Known artifact classes: "
                "3W-transformer star buses (RAW auto-numbers 99xxxx; EPC uses "
                "named tap buses), series-cap bypass records (ckt BP, EPC only), "
                "rating slots B-D (mapped differently; only RateA compared), "
                "flow signs (metered-end convention; magnitudes compared)."
            )
        cmp.add("Format context", note)

    # Summary / totals
    sa, sb = parse_case_sum(results_a / "CaseSum.csv"), parse_case_sum(results_b / "CaseSum.csv")
    aa, ab = parse_area_sum(results_a / "AreaSum.csv"), parse_area_sum(results_b / "AreaSum.csv")
    tots = []
    for nm, key in (("buses", "n_buses"), ("gens", "n_generators"), ("loads", "n_loads"),
                    ("branches", "n_branches"), ("areas", "n_areas"), ("zones", "n_zones")):
        va, vb = sa.get(key), sb.get(key)
        if va != vb:
            tots.append(f"- {nm}: {va} -> {vb} ({(vb or 0)-(va or 0):+d})")
    for q in ("gen", "load", "losses"):
        ta = sum(v[q] for v in aa.values())
        tb = sum(v[q] for v in ab.values())
        tots.append(f"- total {q}: {ta:,.1f} -> {tb:,.1f} MW ({tb-ta:+,.1f})")
    tots.append(f"- solved: A={sa.get('converged')} ({sa.get('iterations')} it), "
                f"B={sb.get('converged')} ({sb.get('iterations')} it)")
    cmp.add("Case summary", tots)

    # Area totals movers
    lines = []
    for area in sorted(set(aa) | set(ab)):
        if area not in aa:
            lines.append(f"- area {area}: only in B")
            continue
        if area not in ab:
            lines.append(f"- area {area}: only in A")
            continue
        dg = ab[area]["gen"] - aa[area]["gen"]
        dl = ab[area]["load"] - aa[area]["load"]
        if abs(dg) > MW_EPS or abs(dl) > MW_EPS:
            lines.append(f"- area {area}: gen {dg:+,.1f} MW, load {dl:+,.1f} MW")
    cmp.add("Area changes", lines)

    # Buses
    ba, bb = read_buses(results_a / "BusData.csv"), read_buses(results_b / "BusData.csv")
    only_a, only_b = sorted(set(ba) - set(bb)), sorted(set(bb) - set(ba))
    lines = []
    for tag, only in (("A", only_a), ("B", only_b)):
        if not only:
            continue
        if cross_format:
            star = [b for b in only if b >= STAR_BUS_MIN]
            real = [b for b in only if b < STAR_BUS_MIN]
            lines.append(f"- {len(real)} buses only in {tag} "
                         f"(first 10: {real[:10]}) + {len(star)} star buses "
                         f"[format artifact]")
        else:
            lines.append(f"- {len(only)} buses only in {tag} (first 10: {only[:10]})")
    movers = sorted(
        ((abs(bb[k]["v"] - ba[k]["v"]), k) for k in ba.keys() & bb.keys()),
        reverse=True,
    )
    n_over = sum(1 for d, _ in movers if d > V_EPS)
    lines.append(f"- {n_over} buses with |dV| > {V_EPS} pu")
    for d, k in movers[:top_n]:
        if d <= V_EPS:
            break
        lines.append(f"  - bus {k} {ba[k]['name']} {ba[k]['kv']}kV (area {ba[k]['area']}): "
                     f"{ba[k]['v']:.4f} -> {bb[k]['v']:.4f} ({bb[k]['v']-ba[k]['v']:+.4f})")
    cmp.add("Bus voltages", lines)

    # Branches
    ra, rb = read_branches(results_a / "BranchData.csv"), read_branches(results_b / "BranchData.csv")
    common = ra.keys() & rb.keys()
    lines = []
    add_b = sorted(rb.keys() - ra.keys())
    del_b = sorted(ra.keys() - rb.keys())
    for tag, only, src in (("A", del_b, ra), ("B", add_b, rb)):
        if not only:
            continue
        if cross_format:
            art = [k for k in only if _is_format_artifact_branch(k)]
            real = [k for k in only if not _is_format_artifact_branch(k)]
            lines.append(f"- {len(real)} branches only in {tag} "
                         f"(+ {len(art)} star-bus/bypass records [format artifact]):")
            show = real
        else:
            lines.append(f"- {len(only)} branches only in {tag}:")
            show = only
        lines += [f"  - {k} {src[k]['fr_name']}-{src[k]['to_name']} {src[k]['kv']}kV"
                  for k in show[:top_n]]
    st_chg = [k for k in common if ra[k]["st"] != rb[k]["st"]]
    if st_chg:
        lines.append(f"- {len(st_chg)} branch status changes:")
        lines += [f"  - {k} {ra[k]['fr_name']}-{ra[k]['to_name']}: st {ra[k]['st']} -> {rb[k]['st']}"
                  for k in st_chg[:top_n]]
    par_chg = []
    for k in common:
        for p_ in ("r", "x", "charg"):
            va, vb_ = ra[k][p_], rb[k][p_]
            base = max(abs(va), abs(vb_), 1e-9)
            if abs(va - vb_) / base > PARAM_REL_EPS:
                par_chg.append((k, p_, va, vb_))
    if par_chg:
        lines.append(f"- {len(par_chg)} impedance/charging changes:")
        lines += [f"  - {k} {p_}: {va:.6f} -> {vb_:.6f}" for k, p_, va, vb_ in par_chg[:top_n]]
    # RateA only: EPC vs RAW map the B/C/D rating slots differently, which
    # produces mass false positives if all four slots are compared.
    rate_chg = [k for k in common
                if abs(ra[k]["rates"][0] - rb[k]["rates"][0]) > RATE_EPS]
    if rate_chg:
        lines.append(f"- {len(rate_chg)} RateA changes:")
        lines += [f"  - {k} {ra[k]['fr_name']}-{ra[k]['to_name']}: "
                  f"{ra[k]['rates'][0]} -> {rb[k]['rates'][0]} MVA"
                  for k in rate_chg[:top_n]]
    cmp.add("Branch topology / ratings / parameters", lines)

    # Branch flow + loading movers
    lines = []
    # Compare |MW| (metered-end sign conventions differ between EPC and RAW)
    fl = sorted(((abs(abs(rb[k]["mw"]) - abs(ra[k]["mw"])), k) for k in common),
                reverse=True)
    fl_lines = [
        f"  - {k} {ra[k]['fr_name']}-{ra[k]['to_name']} {ra[k]['kv']}kV: "
        f"|{ra[k]['mw']:.1f}| -> |{rb[k]['mw']:.1f}| MW "
        f"({abs(rb[k]['mw'])-abs(ra[k]['mw']):+.1f})"
        for d, k in fl[:top_n] if d > MW_EPS
    ]
    if fl_lines:
        lines.append(f"- top flow movers (d|MW| > {MW_EPS}):")
        lines += fl_lines
    ld = []
    for k in common:
        la, lb_ = _loading(ra[k]["mva"], ra[k]["rates"][0]), _loading(rb[k]["mva"], rb[k]["rates"][0])
        if la is not None and lb_ is not None:
            ld.append((abs(lb_ - la), la, lb_, k))
    ld.sort(reverse=True)
    ld_lines = [
        f"  - {k} {ra[k]['fr_name']}-{ra[k]['to_name']} {ra[k]['kv']}kV: "
        f"{la:.1f}% -> {lb_:.1f}% ({lb_-la:+.1f}%)"
        for d, la, lb_, k in ld[:top_n] if d >= 1.0
    ]
    if ld_lines:
        lines.append("- top loading movers (% of RateA):")
        lines += ld_lines
    cmp.add("Branch flows / loadings", lines)

    # Generators
    ga, gb = read_gens(results_a / "GenData.csv"), read_gens(results_b / "GenData.csv")
    lines = []
    gadd, gdel = sorted(gb.keys() - ga.keys()), sorted(ga.keys() - gb.keys())
    if gdel:
        lines.append(f"- {len(gdel)} units only in A:")
        lines += [f"  - {k} {ga[k]['name']} (area {ga[k]['area']}, Pmax {ga[k]['pmax']:.0f})" for k in gdel[:top_n]]
    if gadd:
        lines.append(f"- {len(gadd)} units only in B:")
        lines += [f"  - {k} {gb[k]['name']} (area {gb[k]['area']}, Pmax {gb[k]['pmax']:.0f})" for k in gadd[:top_n]]
    gst = [k for k in ga.keys() & gb.keys() if ga[k]["st"] != gb[k]["st"]]
    if gst:
        lines.append(f"- {len(gst)} unit status changes:")
        lines += [f"  - {k} {ga[k]['name']}: st {ga[k]['st']} -> {gb[k]['st']} "
                  f"(P {ga[k]['pgen']:.1f} -> {gb[k]['pgen']:.1f})" for k in gst[:top_n]]
    gm = sorted(((abs(gb[k]["pgen"] - ga[k]["pgen"]), k) for k in ga.keys() & gb.keys()), reverse=True)
    net = sum(gb[k]["pgen"] - ga[k]["pgen"] for k in ga.keys() & gb.keys())
    gm_lines = [
        f"  - {k} {ga[k]['name']} (area {ga[k]['area']}): "
        f"{ga[k]['pgen']:.1f} -> {gb[k]['pgen']:.1f} MW ({gb[k]['pgen']-ga[k]['pgen']:+.1f})"
        for d, k in gm[:top_n] if d > MW_EPS
    ]
    if gm_lines or abs(net) > MW_EPS:
        lines.append(f"- net redispatch on common units: {net:+,.1f} MW; top movers:")
        lines += gm_lines
    cmp.add("Generation", lines)

    # Loads
    la_, lb2 = read_loads(results_a / "LoadData.csv"), read_loads(results_b / "LoadData.csv")
    lines = []
    ladd, ldel = sorted(lb2.keys() - la_.keys()), sorted(la_.keys() - lb2.keys())
    if ldel:
        lines.append(f"- {len(ldel)} loads only in A:")
        lines += [f"  - {k} {la_[k]['name']} ({la_[k]['p']:.1f} MW)" for k in ldel[:top_n]]
    if ladd:
        lines.append(f"- {len(ladd)} loads only in B:")
        lines += [f"  - {k} {lb2[k]['name']} ({lb2[k]['p']:.1f} MW)" for k in ladd[:top_n]]
    lst = [k for k in la_.keys() & lb2.keys() if la_[k]["st"] != lb2[k]["st"]]
    if lst:
        lines.append(f"- {len(lst)} load status changes (first {top_n}):")
        lines += [f"  - {k} {la_[k]['name']}: st {la_[k]['st']} -> {lb2[k]['st']}" for k in lst[:top_n]]
    net = sum(lb2[k]["p"] - la_[k]["p"] for k in la_.keys() & lb2.keys())
    lm = sorted(((abs(lb2[k]["p"] - la_[k]["p"]), k) for k in la_.keys() & lb2.keys()), reverse=True)
    lm_lines = [
        f"  - {k} {la_[k]['name']} (area {la_[k]['area']}): "
        f"{la_[k]['p']:.1f} -> {lb2[k]['p']:.1f} MW ({lb2[k]['p']-la_[k]['p']:+.1f})"
        for d, k in lm[:top_n] if d > MW_EPS
    ]
    if lm_lines or abs(net) > MW_EPS:
        lines.append(f"- net load change on common loads: {net:+,.1f} MW; top movers:")
        lines += lm_lines
    cmp.add("Loads", lines)

    return cmp
