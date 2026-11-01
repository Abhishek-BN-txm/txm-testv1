"""Differences between two cases, grouped into projects (task 27).

    study case  = the case the study runs on (receives projects)
    donor case  = the case projects are taken from

Both cases are solved by TARA first (a basecase run each, through the run
manager) and compared from TARA's own case reports (case_reports.py), so the
review sees each case as TARA models it.

Every element (bus, line section, transformer leg, generator, load, shunt /
SVD) is matched by its key and is one of:

  added    in the donor only            -> can be carried into the study case
  removed  in the study case only       -> e.g. the line a loop-in replaces
  changed  in both, different settings  -> status, impedance, ratings, limits

Differences in generator dispatch and load MW only are listed apart (they
follow the case's balancing, not a project); solved state (bus voltage and
angle, tap position, SVD output, Q) is ignored.

Grouping (task 27; the transmission rule is the user's, 2026-10-21):

 1. Equipment behind new buses. Added or removed elements that meet at a bus
    which exists in only one of the cases are one piece: a generator's
    collector system, GSUs, gen-tie and units. Except at a new NETWORK bus
    (>= 100 kV with lines to two or more other buses: a substation or switching
    station), which belongs to transmission and never glues projects together.
    Four queue projects on one new substation stay four projects + one
    substation (Marigold, SRP C26_33HS pre -> post).
 2. Generation projects. A piece with an added generator is one project, kept
    on its own even when another project lands at the same or a neighbouring
    POI, so queue projects can be flagged one by one (attrition).
 3. Transmission projects. Pieces without generation are merged when they
    touch the same bus or neighbouring buses (one branch or transformer
    apart, in either case). A transformer that creates a new voltage level at
    a bus touches that bus, so it joins the lines there.
 4. Setting changes (status, impedance, ratings) on elements in both cases
    join a project they share a bus with; the rest group by shared bus only,
    so reconductors do not chain across a whole region.
 5. A new bus with no connections joins the project that has a bus of the
    same name, when exactly one does (3-winding star buses, midpoints).

A group is a starting point: every element can be flagged on its own.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from tara_automation.case_reports import Case, Record, read_results

# fields compared for "changed". Solved state (flows, voltages, tap ratio,
# phase angle, switched-shunt output, Q) is never compared.
PARAMS = {
    "bus": ("name", "kv", "area", "zone"),
    "branch": ("st", "r", "x", "b", "b_from", "b_to", "rate1", "rate2", "rate3", "rate4"),
    "transformer": ("st", "r", "x", "b", "rate1", "rate2", "rate3", "rate4",
                    "rat_min", "rat_max", "step", "v_min", "v_max", "reg_bus"),
    "generator": ("st", "pmax", "pmin", "mbase", "reg_bus", "xsource"),
    "load": ("st",),
    "shunt": ("st", "mode", "b", "bmin", "bmax"),
}
DISPATCH = {
    # Q limits are read off each unit's capability curve at its dispatch
    "generator": ("pgen", "vsched", "qmax", "qmin"),
    "load": ("mw", "mvar", "mw_i", "mvar_i", "mw_z", "mvar_z"),
}
NETWORK_KV = 100.0       # a new bus at/above this with 2+ line neighbours is a substation
KIND_LABEL = {"bus": "Bus", "branch": "Line", "transformer": "Transformer", "generator": "Generator",
              "load": "Load", "shunt": "Shunt / SVD"}
_ORDER = {"bus": 0, "transformer": 1, "branch": 2, "generator": 3, "shunt": 4, "load": 5}


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return a == b
        # 0.1 %: below that it is rounding or a solved-state echo (SVD limits
        # move ~0.04 % between solutions of the same case), not a project
        return abs(a - b) <= 1e-6 + 1e-3 * max(abs(a), abs(b))
    return a == b


def element_id(r: Record) -> str:
    """Stable text id of an element; used for flags and by task 28."""
    return r.kind + ":" + ":".join(str(k) for k in r.key)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _bus_label(case_a: Case, case_b: Case, n: int) -> str:
    r = case_b.bus(n) or case_a.bus(n)
    return f"{n} {r.f['name']} {r.f['kv']:g}" if r else str(n)


def _describe(r: Record) -> str:
    f = r.f
    if r.kind == "branch":
        rate = f"{f['rate1']:g}/{f['rate2']:g} MVA" if f["rate1"] else "no rating"
        return (f"R {f['r']:.5f}  X {f['x']:.5f}  B {f['b']:.5f} pu · {rate}"
                + (f" · {f['length']:g} mi" if f["length"] else ""))
    if r.kind == "transformer":
        taps = f" · taps {f['rat_min']:g}-{f['rat_max']:g}" if f["rat_max"] else ""
        return f"{f['from_kv']:g}/{f['to_kv']:g} kV · X {f['x']:.4f} pu (100 MVA) · {f['rate1']:g} MVA{taps}"
    if r.kind == "generator":
        return f"Pmax {f['pmax']:g} MW · Pgen {f['pgen']:g} · Q {f['qmin']:g}..{f['qmax']:g} · {f['mbase']:g} MVA"
    if r.kind == "load":
        return f"{f['mw'] + f['mw_i'] + f['mw_z']:g} MW  {f['mvar'] + f['mvar_i'] + f['mvar_z']:g} Mvar"
    if r.kind == "shunt":
        if f["mode"] > 0:
            return f"{f['mode_name']} · {f['bmin']:g}..{f['bmax']:g} Mvar"
        return f"{f['mode_name'] or 'fixed'} · {f['b']:g} Mvar"
    if r.kind == "bus":
        return f"area {f['area']} zone {f['zone']}"
    return ""


def _kv(r: Record) -> float:
    f = r.f
    if r.kind in ("branch", "transformer"):
        return max(float(f["from_kv"] or 0), float(f["to_kv"] or 0))
    return float(f.get("kv") or 0)


def _item(r: Record, action: str, study: Case, donor: Case, changes=None, dispatch=False) -> dict:
    f = r.f
    if r.kind in ("branch", "transformer"):
        where = (f"{_bus_label(study, donor, f['from'])} – {_bus_label(study, donor, f['to'])}"
                 f" ckt {f['ck']}" + (f" sec {f['se']}" if f["se"] != 1 else ""))
    elif r.kind == "bus":
        where = _bus_label(study, donor, f["bus"])
    else:
        where = f"{_bus_label(study, donor, f['bus'])} id {f['id']}"
    return {
        "id": element_id(r), "kind": r.kind, "kind_label": KIND_LABEL[r.kind], "action": action,
        "where": where, "kv": _kv(r), "area": f.get("area"), "status": f.get("st"),
        "buses": list(r.buses), "detail": _describe(r),
        "mw": f["pmax"] if r.kind == "generator" else None,
        "changes": changes or [], "dispatch_only": dispatch, "note": "",
    }


def _diff_fields(a: Record, b: Record, names) -> list[dict]:
    return [{"field": n, "study": _fmt(a.f.get(n)), "donor": _fmt(b.f.get(n))}
            for n in names if not _same(a.f.get(n), b.f.get(n))]


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def _pass_through(study: Case, donor: Case) -> set[int]:
    """Buses that only join two line sections (multi-section midpoints) or
    three transformer legs (3-winding star points), with nothing connected:
    real but meaningless as a project's name."""
    inj = {r.f["bus"] for c in (study, donor) for k in ("generator", "load", "shunt") for r in c[k].values()}
    legs = defaultdict(lambda: defaultdict(set))
    for c in (study, donor):
        for kind in ("branch", "transformer"):
            for r in c[kind].values():
                a, b = r.buses
                legs[a][kind].add(b)
                legs[b][kind].add(a)
    out = set()
    for bus, k in legs.items():
        if bus in inj:
            continue
        nb, nt = len(k["branch"]), len(k["transformer"])
        if (nb == 2 and nt == 0) or (nb == 0 and nt == 3):
            out.add(bus)
    return out


def _summarize(items: list[dict], study: Case, donor: Case, n: int, skip: set[int] = frozenset()) -> dict:
    kinds = defaultdict(int)
    for it in items:
        kinds[f"{it['action']} {it['kind']}"] += 1
    added = [it for it in items if it["action"] == "added"]
    has_tx = any(it["kind"] in ("branch", "transformer", "bus") for it in items)
    has_gen = any(it["kind"] == "generator" and it["action"] == "added" for it in items)
    if all(it["kind"] == "bus" and it.get("note") for it in items):
        kind = "unconnected bus"
    elif all(it["action"] == "changed" for it in items):
        kind = "upgrade / setting change"
    elif all(it["action"] == "removed" for it in items):
        kind = "only in the study case"
    else:
        kind = "transmission + generation" if has_tx and has_gen else "generation" if has_gen \
            else "transmission" if has_tx else "load / shunt"
    # name it after the highest-voltage buses involved
    named = {b for it in items for b in it["buses"]}
    ranked = sorted(named - skip or named,
                    key=lambda b: -float(((donor.bus(b) or study.bus(b)).f["kv"]) if (donor.bus(b) or study.bus(b)) else 0))
    names = []
    for b in ranked:
        r = donor.bus(b) or study.bus(b)
        nm = r.f["name"] if r else str(b)
        if nm not in names:
            names.append(nm)
        if len(names) == 3:
            break
    kvs = sorted({round(it["kv"], 2) for it in items if it["kv"]}, reverse=True)
    areas = sorted({it["area"] for it in items if it["area"] is not None})
    return {
        "id": f"P{n}", "kind": kind, "label": " / ".join(names), "kv": kvs, "areas": areas,
        "mw_added": round(sum(it["mw"] or 0 for it in added if it["kind"] == "generator"), 1),
        "counts": dict(sorted(kinds.items())), "n": len(items),
        "elements": sorted(items, key=lambda it: (_ORDER[it["kind"]], -it["kv"], it["where"])),
    }


def _group(items: list[dict], study: Case, donor: Case) -> list[list[dict]]:
    """The grouping rules in the module docstring, in order."""
    n = len(items)
    in_study = {k[0] for k in study["bus"]}
    in_donor = {k[0] for k in donor["bus"]}
    one_sided = in_study ^ in_donor                    # new (or gone) buses
    adj = defaultdict(set)
    for case in (study, donor):
        for a, nbrs in case.adjacency(in_service_only=False).items():
            adj[a] |= nbrs
    uf = _UF(n)
    structural = [i for i, it in enumerate(items) if it["action"] != "changed"]

    line_nbrs = defaultdict(set)
    for case in (study, donor):
        for r in case["branch"].values():
            a, b = r.f["from"], r.f["to"]
            line_nbrs[a].add(b)
            line_nbrs[b].add(a)

    def kv_of(b):
        r = donor.bus(b) or study.bus(b)
        return float(r.f["kv"]) if r else 0.0
    network = {b for b in one_sided if kv_of(b) >= NETWORK_KV and len(line_nbrs[b]) >= 2}

    # 1. pieces joined through one-sided buses
    glue = defaultdict(list)
    for i in structural:
        for b in items[i]["buses"]:
            if b in one_sided and b not in network:
                glue[b].append(i)
    for members in glue.values():
        for j in members[1:]:
            uf.union(members[0], j)

    # 5. unconnected new buses: join the one piece with a same-named bus
    def piece_names():
        names = defaultdict(set)
        for i in structural:
            for b in items[i]["buses"]:
                r = donor.bus(b) or study.bus(b)
                if r:
                    names[(r.f["name"], r.f["area"])].add(uf.find(i))
        return names
    names = piece_names()
    for i in structural:
        it = items[i]
        if it["kind"] == "bus" and not adj.get(it["buses"][0]):
            r = donor.bus(it["buses"][0]) or study.bus(it["buses"][0])
            roots = names.get((r.f["name"], r.f["area"]), set()) - {uf.find(i)} if r else set()
            if len(roots) == 1:
                uf.union(i, roots.pop())
                it["note"] = "no connections; grouped by station name"
            else:
                it["note"] = "no connections in the donor case"

    # 2./3. generation pieces stay apart; transmission pieces merge by the rule
    pieces = defaultdict(list)
    for i in structural:
        pieces[uf.find(i)].append(i)
    is_gen = {root: any(items[i]["kind"] == "generator" and items[i]["action"] == "added" for i in ms)
              for root, ms in pieces.items()}
    tx_at = defaultdict(set)
    for root, ms in pieces.items():
        if not is_gen[root]:
            for i in ms:
                for b in items[i]["buses"]:
                    tx_at[b].add(root)
    for root, ms in pieces.items():
        if is_gen[root]:
            continue
        for i in ms:
            for b in items[i]["buses"]:
                for nb in {b} | adj.get(b, set()):
                    for other in tx_at.get(nb, ()):
                        uf.union(root, other)

    # 4. setting changes: attach where they share a bus, else group by shared bus
    at_bus = defaultdict(set)
    for i in structural:
        for b in items[i]["buses"]:
            at_bus[b].add(uf.find(i))
    for i, it in enumerate(items):
        if it["action"] != "changed":
            continue
        hits = sorted({r for b in it["buses"] for r in at_bus.get(b, ())},
                      key=lambda r: is_gen.get(r, False))  # prefer transmission
        if hits:
            uf.union(i, hits[0])
    changed_at = defaultdict(list)
    for i, it in enumerate(items):
        if it["action"] == "changed":
            for b in it["buses"]:
                changed_at[b].append(i)
    for members in changed_at.values():
        for j in members[1:]:
            uf.union(members[0], j)

    comps = defaultdict(list)
    for i, it in enumerate(items):
        comps[uf.find(i)].append(it)
    return list(comps.values())


def compare(study_results: str | Path, donor_results: str | Path, *, study_run=None, donor_run=None,
            study_name: str = "", donor_name: str = "") -> dict:
    """Both arguments are `runs/N/results` folders of basecase runs."""
    study = read_results(study_results, study_run, study_name)
    donor = read_results(donor_results, donor_run, donor_name)
    return compare_cases(study, donor)


def compare_cases(study: Case, donor: Case) -> dict:
    items: list[dict] = []
    dispatch: list[dict] = []
    for kind in _ORDER:
        s, d = study[kind], donor[kind]
        for key in d.keys() - s.keys():
            items.append(_item(d[key], "added", study, donor))
        for key in s.keys() - d.keys():
            items.append(_item(s[key], "removed", study, donor))
        for key in s.keys() & d.keys():
            a, b = s[key], d[key]
            ch = _diff_fields(a, b, PARAMS[kind])
            if ch:
                items.append(_item(b, "changed", study, donor, ch))
                continue
            dch = _diff_fields(a, b, DISPATCH.get(kind, ()))
            if dch and kind != "bus":
                dispatch.append(_item(b, "changed", study, donor, dch, dispatch=True))

    comps = _group(items, study, donor)
    loose = [c for c in comps if all(it["kind"] == "bus" and it.get("note") and "no connections in" in it["note"] for it in c)]
    comps = [c for c in comps if not any(c is x for x in loose)]
    if loose:
        comps.append([it for c in loose for it in c])
    skip = _pass_through(study, donor)
    groups = [_summarize(c, study, donor, 0, skip) for c in comps]
    # transmission first, highest voltage first
    order = {"transmission + generation": 0, "transmission": 1, "generation": 2, "upgrade / setting change": 3,
             "load / shunt": 4, "only in the study case": 5, "unconnected bus": 6}
    groups.sort(key=lambda g: (order[g["kind"]], -(g["kv"][0] if g["kv"] else 0), -g["mw_added"], g["label"]))
    for n, g in enumerate(groups, 1):
        g["id"] = f"P{n}"
        for it in g["elements"]:
            it["group"] = g["id"]

    def totals(c: Case) -> dict:
        gens = [r.f for r in c["generator"].values() if r.f["st"] == 1]
        loads = [r.f for r in c["load"].values() if r.f["st"] == 1]
        return {"case": c.case_name, "run": c.run, "results": str(c.results),
                "counts": {k: len(v) for k, v in c.records.items()}, "skipped": c.skipped,
                "gen_mw": round(sum(g["pgen"] for g in gens), 1),
                "load_mw": round(sum(lf["mw"] + lf["mw_i"] + lf["mw_z"] for lf in loads), 1)}

    return {
        "study": totals(study), "donor": totals(donor),
        "groups": groups,
        "dispatch": sorted(dispatch, key=lambda it: (_ORDER[it["kind"]], it["area"] or 0, it["where"])),
        "summary": {
            "groups": len(groups),
            "elements": len(items),
            "added": sum(it["action"] == "added" for it in items),
            "removed": sum(it["action"] == "removed" for it in items),
            "changed": sum(it["action"] == "changed" for it in items),
            "dispatch_only": len(dispatch),
        },
    }
