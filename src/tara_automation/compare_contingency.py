"""Compare AC N-1 contingency results across two or more runs.

Given a set of contingency runs (each with BranchViol.csv / VoltViol.csv), this
builds a facility x contingency comparison of post-contingency loadings, the
voltage-violation bus sets, and the non-convergent-contingency sets, then emits
an Excel workbook (the standard deliverable) and a short markdown digest.

The first run is the baseline; deltas and new/worsened/relieved status are
measured against it. Parsing goes through `violations.parse_*` (comma-safe), so
the "Gila River Power, LP" column-shift that inflated raw loadings never
appears here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tara_automation.violations import parse_branch_viol, parse_volt_viol

OVERLOAD = 100.0
WORSEN_EPS = 2.0  # pp change to call a still->overloaded facility "worsened"


def _key(row: dict) -> tuple[str, str]:
    return (row.get("Monitored Facility", "").strip(),
            row.get("Cont Name", "").strip())


@dataclass
class CaseData:
    label: str
    run_number: int | None
    case_name: str
    rating: str                     # "Rate 2 (Rate B)" etc.
    branch: dict                    # (fac,cont) -> row dict (max ac kept)
    volt: dict                      # bus# -> (name, kv, area)
    nconv: dict                     # cont name -> record count
    n_records: int                  # solved branch records (numeric AC)
    n_overloads: int                # records >= 100%


def _rating_of(run) -> str:
    """Read the post-contingency rating slot from the run's option overrides."""
    ov = (run.manifest.get("contingency", {}) or {}).get("options_overrides", {})
    slot = (ov.get("opt_cont", {}) or {}).get("monBranRatingCont", 2)
    return {1: "Rate 1 (Rate A / normal)",
            2: "Rate 2 (Rate B / emergency)"}.get(slot, f"rating slot {slot}")


def load_case(run, label: str | None = None) -> CaseData:
    rd = run.results_dir
    _, brows = parse_branch_viol(rd / "BranchViol.csv")
    _, vrows = parse_volt_viol(rd / "VoltViol.csv")

    branch: dict = {}
    nconv: dict = {}
    n_records = n_over = 0
    for r in brows:
        if r.get("nconv"):
            cont = r.get("Cont Name", "").strip()
            nconv[cont] = nconv.get(cont, 0) + 1
            continue
        ac = r.get("ac_loading")
        if ac is None:
            continue
        n_records += 1
        if ac >= OVERLOAD:
            n_over += 1
        k = _key(r)
        if k not in branch or ac > branch[k]["ac_loading"]:
            branch[k] = r

    volt = {}
    for r in vrows:
        bus = (r.get("Bus #") or "").strip()
        if bus.isdigit():
            volt[bus] = (r.get("Bus Name", "").strip(),
                         r.get("Base kV", "").strip(),
                         r.get("Area Name", "").strip())

    return CaseData(
        label=label or f"run {run.number}",
        run_number=run.number,
        case_name=Path(str(run.manifest.get("case", ""))).name,
        rating=_rating_of(run),
        branch=branch, volt=volt, nconv=nconv,
        n_records=n_records, n_overloads=n_over,
    )


@dataclass
class ContingencyComparison:
    cases: list[CaseData]
    contingencies: int = 0
    overload_rows: list[dict] = field(default_factory=list)
    all_rows: list[dict] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        return [c.label for c in self.cases]

    def _status(self, loadings: list[float | None]) -> str:
        base = loadings[0]
        tags = []
        for c, val in zip(self.cases[1:], loadings[1:]):
            if val is None:
                continue
            if val >= OVERLOAD and (base is None or base < OVERLOAD):
                tags.append(f"NEW in {c.label}")
            elif (val >= OVERLOAD and base is not None
                  and val - base >= WORSEN_EPS):
                tags.append(f"worse in {c.label}")
            elif base is not None and base >= OVERLOAD and val < OVERLOAD:
                tags.append(f"relieved in {c.label}")
        if not tags and base is not None and base >= OVERLOAD:
            return "pre-existing"
        return "; ".join(tags) if tags else "pre-existing"

    def build(self) -> "ContingencyComparison":
        self.contingencies = max(
            (len(c.branch) for c in self.cases), default=0)
        keys = set()
        for c in self.cases:
            for k, r in c.branch.items():
                if r["ac_loading"] >= OVERLOAD:
                    keys.add(k)
        all_keys = {k for c in self.cases for k in c.branch}

        def make_row(k):
            fac, cont = k
            loadings = [c.branch.get(k, {}).get("ac_loading") for c in self.cases]
            meta = next((c.branch[k] for c in self.cases if k in c.branch), {})
            return {
                "facility": fac, "contingency": cont,
                "kv": meta.get("kVs", ""), "area": meta.get("Areas", ""),
                "rate_base": meta.get("Rate Base (MVA)", ""),
                "rate_cont": meta.get("Rate Cont (MVA)", ""),
                "loadings": loadings,
                "status": self._status(loadings),
            }

        def worst(r):
            return -max([v for v in r["loadings"] if v is not None] or [0])

        self.overload_rows = sorted((make_row(k) for k in keys), key=worst)
        self.all_rows = sorted((make_row(k) for k in all_keys), key=worst)
        return self


    def to_markdown(self, top_n: int = 20) -> str:
        base = self.cases[0]
        out = [f"# N-1 contingency comparison ({base.rating})", ""]
        out.append("Baseline = **" + base.label + "**. Loadings are worst "
                   "post-contingency AC % per monitored facility.")
        out.append("")
        out.append("| Metric | " + " | ".join(c.label for c in self.cases) + " |")
        out.append("|---" * (len(self.cases) + 1) + "|")

        def row(name, vals):
            return f"| {name} | " + " | ".join(str(v) for v in vals) + " |"

        out.append(row("Overloads ≥100%", [c.n_overloads for c in self.cases]))
        out.append(row("Solved branch records", [c.n_records for c in self.cases]))
        out.append(row("Non-convergent records",
                       [sum(c.nconv.values()) for c in self.cases]))
        out.append(row("Voltage violations (buses)",
                       [len(c.volt) for c in self.cases]))
        new_worse = [r for r in self.overload_rows
                     if "NEW" in r["status"] or "worse" in r["status"]]
        out += ["", f"## New / worsened overloads ({len(new_worse)})", ""]
        hdr = ["Facility", "kV", "Contingency"] + self.labels + ["Status"]
        out.append("| " + " | ".join(hdr) + " |")
        out.append("|---" * len(hdr) + "|")
        for r in new_worse[:top_n]:
            loads = ["" if v is None else f"{v:.1f}" for v in r["loadings"]]
            out.append("| " + " | ".join(
                [r["facility"][:30], r["kv"], r["contingency"][:30]]
                + loads + [r["status"]]) + " |")
        return "\n".join(out) + "\n"


def compare(runs, labels: list[str] | None = None) -> ContingencyComparison:
    labels = labels or [None] * len(runs)
    cases = [load_case(r, lbl) for r, lbl in zip(runs, labels)]
    return ContingencyComparison(cases=cases).build()
