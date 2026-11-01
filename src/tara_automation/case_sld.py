"""Single-line-diagram data for a project: its buses in both cases (task 27).

The study desk draws the diagram; this module decides what is on it. Given
the two solved cases and a set of buses (a project's buses, plus any the user
expanded), it returns:

  buses      every shown bus, as it is in each case (None where absent), with
             its form: "bus", "star" (3-winding star point: only three
             transformer legs, nothing else) or "mid" (multi-section midpoint)
  elements   every element touching a shown bus, in either case, with both
             cases' values and the action (added / removed / changed / same).
             A branch whose far end is not shown is a stub: `far` names it.
  more       per shown bus, how many of its elements lead off the diagram

Element ids are case_diff.element_id, so a click on the diagram and a row in
the flag table name the same element.
"""

from __future__ import annotations

from collections import defaultdict

from tara_automation.case_diff import DISPATCH, PARAMS, _diff_fields, element_id
from tara_automation.case_reports import Case, Record

MAX_BUSES = 400


def _by_bus(case: Case) -> dict[int, list[Record]]:
    idx = getattr(case, "_by_bus", None)
    if idx is None:
        idx = defaultdict(list)
        for kind in ("branch", "transformer", "generator", "load", "shunt"):
            for r in case[kind].values():
                for b in r.buses:
                    idx[b].append(r)
        case._by_bus = idx
    return idx


def _form(study: Case, donor: Case, n: int) -> str:
    legs = {"branch": set(), "transformer": set(), "inj": 0}
    for case in (study, donor):
        for r in _by_bus(case).get(n, ()):
            if r.kind in ("branch", "transformer"):
                legs[r.kind].add(r.key)
            else:
                legs["inj"] += 1
    if legs["inj"]:
        return "bus"
    if not legs["branch"] and len(legs["transformer"]) == 3:
        return "star"
    if len(legs["branch"]) == 2 and not legs["transformer"]:
        return "mid"
    return "bus"


def _plain(f: dict | None) -> dict | None:
    return None if f is None else {k: v for k, v in f.items() if v is not None}


def neighbours(case: Case, n: int) -> set[int]:
    return {b for r in _by_bus(case).get(n, ()) if r.kind in ("branch", "transformer") for b in r.buses} - {n}


def view(study: Case, donor: Case, buses, around=()) -> dict:
    """`around`: buses shown with every neighbour (either case) as well, the
    TARA Studio bus view: a focus bus, its neighbours, and their other
    connections as stubs."""
    shown = {int(b) for b in buses if study.bus(int(b)) or donor.bus(int(b))}
    for a in around:
        a = int(a)
        if study.bus(a) or donor.bus(a):
            shown |= {a} | neighbours(study, a) | neighbours(donor, a)
    if len(shown) > MAX_BUSES:
        raise ValueError(f"{len(shown)} buses is too many for one diagram (max {MAX_BUSES})")
    elements: dict[str, dict] = {}
    for case_name, case in (("study", study), ("donor", donor)):
        idx = _by_bus(case)
        for b in shown:
            for r in idx.get(b, ()):
                eid = element_id(r)
                e = elements.setdefault(eid, {"id": eid, "kind": r.kind, "buses": list(r.buses),
                                              "study": None, "donor": None})
                e[case_name] = _plain(r.f)
    more: dict[int, int] = defaultdict(int)
    far: dict[int, dict] = {}
    for e in elements.values():
        s, d = e["study"], e["donor"]
        if s is None:
            e["action"] = "added"
        elif d is None:
            e["action"] = "removed"
        else:
            rs = Record(e["kind"], (), s)
            rd = Record(e["kind"], (), d)
            ch = _diff_fields(rs, rd, PARAMS[e["kind"]])
            disp = _diff_fields(rs, rd, DISPATCH.get(e["kind"], ()))
            e["action"] = "changed" if ch else "dispatch" if disp else "same"
            e["changes"] = ch + disp
        if e["kind"] in ("branch", "transformer"):
            out = [b for b in e["buses"] if b not in shown]
            if out:
                e["stub"] = True
                e["far"] = out[0]
                for b in e["buses"]:
                    if b in shown:
                        more[b] += 1
                r = donor.bus(out[0]) or study.bus(out[0])
                if r:
                    far[out[0]] = {"bus": out[0], "name": r.f["name"], "kv": r.f["kv"], "area": r.f["area"]}
    bus_out = {}
    for n in shown:
        s, d = study.bus(n), donor.bus(n)
        bus_out[n] = {"bus": n, "study": _plain(s.f) if s else None, "donor": _plain(d.f) if d else None,
                      "action": "added" if not s else "removed" if not d else "same",
                      "form": _form(study, donor, n)}
        if s and d:
            ch = _diff_fields(s, d, PARAMS["bus"])
            if ch:
                bus_out[n]["action"] = "changed"
                bus_out[n]["changes"] = ch
    return {"buses": list(bus_out.values()), "elements": list(elements.values()),
            "more": dict(more), "far": list(far.values())}
