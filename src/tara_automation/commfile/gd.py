"""TVA GD (generator deliverability) in TARA 26.02: decks, engine, outputs.

Two decks, both filled from TARA-generated comm files of the SRP C26 TVA GD
package (see docs/tasks/30_tva_gd_feasibility.md):

    step 1  templates/gd_screen_ui_recipe_DRAFT.txt   wdis flowgate screening -> .fgt
    step 2  templates/gd_tvagd_ui_recipe_DRAFT.txt    customApps TVA TVAGI Run_TVA_GI on that .fgt

The templates are still DRAFT: no replay of the SRP 7 Aug 2026 UI run has been
compared yet (memo section 7). `replay_params()` holds that run's settings and
`compare_outputs()` diffs a replay against the package's own CSVs, which is the
check that promotes the drafts.

The GI data workbook is not named in either deck. The .sub carries it on a line
``TVA_GI_DATA "<workbook>.xlsx"`` (SRP keeps the workbook next to the .sub).

Nothing here guesses TARA syntax: every keyword below appears in the package's
decks or in its taralog.txt, except the GDDispatchDetail %save keyword, which
is a tara.exe menu keyword not yet seen in a deck (the run still succeeds
without that file; check_outputs reports it as a note, not a failure).
"""

from __future__ import annotations

import csv
import re
import subprocess
from pathlib import Path

from tara_automation.commfile import syntax
from tara_automation.commfile.casechange import parse_warn_sum
from tara_automation.run_manager import Run

TEMPLATES = Path(__file__).parent / "templates"
SCREEN_TEMPLATE = TEMPLATES / "gd_screen_ui_recipe_DRAFT.txt"
GD_TEMPLATE = TEMPLATES / "gd_tvagd_ui_recipe_DRAFT.txt"
NOFILE = "nofile.nofile"

# Screening (step 1). Values of the package's _comm19.txt.
SCREEN_DEFAULTS = {
    "corr_switching": 0,        # 1 only when a RAS .aux is given
    "max_wd_test": 10000,
    "wd_scaling": 90,
    "wd_pump": 1,               # negative-Pmin units take part (BESS charging)
    "screen_sens_cutoff": 0.04,
    "epc_version": 21,
}

# GD (step 2). Values of the 7 Aug 2026 UI run (taralog lines 4273-4419),
# except the study-specific ones (queue name, StudySelection), which callers set.
GD_DEFAULTS = {
    "corr_switching": 0,
    "dc_loading_cutoff": 90,
    "ac_verify": 0,
    "study_selection": 1,       # 1 Summer NRIS (26.02.1 help text); SRP charging used 2
    "top20_limit": 26,
    "dfax_cutoff_base": 0.04,
    "dfax_cutoff_cont": 0.04,
    "mw_impact_cutoff": 0.04,
    "study_queue": "",
    "ignore_no_q_viol": 1,
    "study_mode": 2,            # 2 = queue projects
    "apply_helper": 0,
    "mw_dispatch_limit": 5000,
    "epc_version": 21,
}

# The five GD reports: (file name, %csv keyword)
GD_REPORTS = [
    ("TVAGDLoadingSummary.csv", "TVAGDLoadingSummary"),
    ("TVAGDCostAllocation.csv", "GDCUUAllocation"),
    ("TVAGDGenParticipationSummary.csv", "GDGenPartSummary"),
    ("TVAGDGenParticipationAllDetails.csv", "GDGenPartAll"),
    ("TVAGDDispatchDetails.csv", "GDDispatchDetail"),
]
UNSEEN_KEYWORD_REPORTS = {"TVAGDDispatchDetails.csv"}   # keyword from the exe menu only

FATAL_WARN_IDS = {
    206: "file could not be opened (sub/con/mon/fgt/case path)",
    4082: "study subsystem not found",
}
STOP_LINES = ("TARA TVA GI Tool stopped",)
TRACE_SCREEN = "gd_screen_trace.txt"
TRACE_GD = "gd_tvagd_trace.txt"
SCREEN_OUTPUTS = ("ViolationScreenSum.txt", "ScreeningDispatchDetails.csv")


def _fmt(v) -> str:
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _name(n: str) -> str:
    if not n or '"' in n or "\n" in n:
        raise ValueError(f"bad subsystem/queue name: {n!r}")
    return n


def _fill(template: Path, tokens: dict) -> str:
    # The template's // header is for humans: TARA decks it wrote itself never
    # start with comment lines, so they are dropped (inline // comments stay).
    lines = template.read_text(encoding="utf-8").splitlines(keepends=True)
    while lines and lines[0].lstrip().startswith("//"):
        lines.pop(0)
    # A token whose value is None removes its whole line (e.g. the corrective-
    # switching slot of `read alldef`, which TARA only prompts for when
    # correctiveSwitchingMode is 1 - run 113 shows the slots shift otherwise).
    lines = [l for l in lines
             if not any(v is None and f"@@{k}@@" in l for k, v in tokens.items())]
    text = "".join(lines)
    for k, v in tokens.items():
        if v is None:
            continue
        text = text.replace(f"@@{k}@@", _fmt(v))
    left = re.findall(r"@@\w+@@", text)
    if left:
        raise RuntimeError(f"{template.name}: unfilled tokens {sorted(set(left))}")
    return text


def _opt_file(p) -> str:
    """Optional file slot: quoted absolute path, or the bare nofile.nofile."""
    return f'"{p}"' if p else NOFILE


def render_screen_deck(*, results_dir, trace_log, case, sub, con, mon, fgt,
                       src: str, ref: str, scenario: str, ras=None, exc=None,
                       params: dict | None = None) -> str:
    p = {**SCREEN_DEFAULTS, **(params or {})}
    bad = set(p) - set(SCREEN_DEFAULTS)
    if bad:
        raise KeyError(f"unknown screening parameter(s) {sorted(bad)}")
    return _fill(SCREEN_TEMPLATE, {
        "RESULTS": results_dir, "TRACE_LOG": trace_log,
        "CORR_SWITCHING": 1 if ras else p["corr_switching"],
        "EPC_VERSION": p["epc_version"], "CASE": case, "SUB": sub, "CON": con,
        "RAS": _opt_file(ras) if (ras or p["corr_switching"]) else None,
        "MON": mon, "EXC": _opt_file(exc),
        "SCENARIO": _name(scenario), "MAX_WD_TEST": p["max_wd_test"],
        "WD_SCALING": p["wd_scaling"], "WD_PUMP": p["wd_pump"],
        "SCREEN_SRC": _name(src), "SCREEN_REF": _name(ref),
        "SCREEN_SENS_CUTOFF": p["screen_sens_cutoff"], "FGT": fgt,
    })


def render_gd_deck(*, results_dir, trace_log, case, sub, fgt, export: str,
                   ref: str, scenario: str, ras=None, exc=None,
                   params: dict | None = None) -> str:
    p = {**GD_DEFAULTS, **(params or {})}
    bad = set(p) - set(GD_DEFAULTS)
    if bad:
        raise KeyError(f"unknown GD parameter(s) {sorted(bad)}")
    if not p["study_queue"]:
        raise ValueError("study_queue is required (the queue name used in the GI workbook)")
    return _fill(GD_TEMPLATE, {
        "RESULTS": results_dir, "TRACE_LOG": trace_log,
        "CORR_SWITCHING": 1 if ras else p["corr_switching"],
        "EPC_VERSION": p["epc_version"], "CASE": case, "SUB": sub,
        "RAS": _opt_file(ras) if (ras or p["corr_switching"]) else None,
        "FGT": fgt, "EXC": _opt_file(exc),
        "SCENARIO": _name(scenario), "GD_EXPORT": _name(export), "GD_REF": _name(ref),
        "DC_LOADING_CUTOFF": p["dc_loading_cutoff"], "AC_VERIFY": p["ac_verify"],
        "STUDY_SELECTION": p["study_selection"], "TOP20_LIMIT": p["top20_limit"],
        "DFAX_CUTOFF_BASE": p["dfax_cutoff_base"], "DFAX_CUTOFF_CONT": p["dfax_cutoff_cont"],
        "MW_IMPACT_CUTOFF": p["mw_impact_cutoff"], "STUDY_QUEUE": _name(p["study_queue"]),
        "IGNORE_NO_Q_VIOL": p["ignore_no_q_viol"], "STUDY_MODE": p["study_mode"],
        "APPLY_HELPER": p["apply_helper"], "MW_DISPATCH_LIMIT": p["mw_dispatch_limit"],
    })


# ------------------------------------------------------------ .sub helpers ---
def gi_data_file(sub_file: Path) -> Path | None:
    """The workbook a .sub names on its TVA_GI_DATA line (resolved next to the .sub)."""
    for line in Path(sub_file).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'\s*TVA_GI_DATA\s+"([^"]+)"', line)
        if m:
            p = Path(m.group(1))
            return p if p.is_absolute() else Path(sub_file).parent / p
    return None


def defined_subsystems(sub_file: Path) -> set[str]:
    names = set()
    for line in Path(sub_file).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*SUBSYSTEM\s+'?([^'\s]+)'?", line, re.I)
        if m:
            names.add(m.group(1))
    return names


# ------------------------------------------------------------------ engine ---
class GDEngine:
    """One run folder per step. step = 'screen' or 'gd'."""

    def __init__(self, tara_exe: Path):
        self.tara_exe = Path(tara_exe)

    def prepare(self, run: Run, step: str, deck: str, meta: dict) -> Path:
        if step not in ("screen", "gd"):
            raise ValueError(step)
        comm = run.comm_dir / f"gd_{step}.txt"
        comm.write_text(deck, encoding="utf-8")
        master = run.comm_dir / "master.txt"
        master.write_text(syntax.master_comm([comm.resolve()]), encoding="utf-8")
        bat = run.comm_dir / "run.bat"
        bat.write_text(syntax.bat_launcher(self.tara_exe, master.resolve()), encoding="utf-8")
        run.manifest["gd"] = {"step": step, "bat": str(bat), "comm": str(comm),
                              "recipe": (SCREEN_TEMPLATE if step == "screen" else GD_TEMPLATE).name,
                              "recipe_status": "DRAFT (replay of SRP 7 Aug 2026 run not yet compared)",
                              **meta}
        run.save_manifest()
        return bat

    def execute(self, run: Run) -> int:
        bat = run.manifest.get("gd", {}).get("bat")
        if not bat:
            raise RuntimeError("call prepare() first")
        with open(run.logs_dir / "tara_console.txt", "wb") as console:
            rc = subprocess.call([str(bat)], shell=False, cwd=str(run.root),
                                 stdout=console, stderr=subprocess.STDOUT)
        run.manifest["tara_exit_code"] = rc
        run.save_manifest()
        return rc

    @staticmethod
    def check_outputs(run: Run) -> dict:
        g = run.manifest.get("gd", {})
        step = g.get("step")
        res = run.results_dir
        problems, notes = [], []
        if run.manifest.get("tara_exit_code") not in (0, None):
            problems.append(f"TARA exit code {run.manifest.get('tara_exit_code')}")
        for w in parse_warn_sum(res / "Warn_Sum_Term.csv") + parse_warn_sum(res / "Warn_Sum.csv"):
            if w["id"] in FATAL_WARN_IDS:
                problems.append(f"WarnID-{w['id']}: {FATAL_WARN_IDS[w['id']]}")
        trace = run.logs_dir / (TRACE_SCREEN if step == "screen" else TRACE_GD)
        text = trace.read_text(encoding="utf-8", errors="replace") if trace.exists() else ""
        if not text:
            problems.append(f"no trace log {trace.name}")
        for s in STOP_LINES:
            if s in text:
                problems.append(f"trace: '{s}'")
        m = re.findall(r"Total Data Errors\s*=\s*(\d+)", text)
        if m and int(m[-1]) != 0:
            problems.append(f"GI data errors: {m[-1]}")
        if (res / "GIDataError.csv").exists():
            problems.append("GIDataError.csv written (GI workbook does not match the case)")
        if step == "screen":
            fgt = Path(g.get("fgt", ""))
            if not fgt.exists() or fgt.stat().st_size == 0:
                problems.append(f"flowgate file missing or empty: {fgt}")
            for f in SCREEN_OUTPUTS:
                if not (res / f).exists():
                    notes.append(f"{f} not written")
        else:
            for f, _ in GD_REPORTS:
                p = res / f
                if not p.exists():
                    (notes if f in UNSEEN_KEYWORD_REPORTS else problems).append(f"{f} not written")
                elif not read_report(p)[0]:
                    problems.append(f"{f} has no header row after the preamble")
        return {"ok": not problems, "problems": problems, "notes": notes}


# ----------------------------------------------------------------- outputs ---
def read_report(path: Path) -> tuple[list[str], list[list[str]]]:
    """A TVA GD CSV -> (header, rows). The preamble (title, case, files) is
    skipped: the header is the first line with >= 5 comma-separated fields."""
    header: list[str] = []
    rows: list[list[str]] = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for cells in csv.reader(f):
            cells = [c.strip() for c in cells]
            if not header:
                if len([c for c in cells if c]) >= 5:
                    header = cells
                continue
            if any(cells):
                rows.append(cells)
    return header, rows


def _num(s: str):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def compare_reports(a: Path, b: Path, tol: float = 0.01) -> dict:
    """Row-by-row, cell-by-cell diff of two GD CSVs (numbers within tol)."""
    ha, ra = read_report(a)
    hb, rb = read_report(b)
    out = {"file": a.name, "header_equal": ha == hb, "rows": (len(ra), len(rb)),
           "cell_diffs": 0, "examples": []}
    for i, (x, y) in enumerate(zip(ra, rb)):
        for j, (u, v) in enumerate(zip(x, y)):
            nu, nv = _num(u), _num(v)
            same = (abs(nu - nv) <= tol) if (nu is not None and nv is not None) else (u == v)
            if not same:
                out["cell_diffs"] += 1
                if len(out["examples"]) < 5:
                    col = ha[j] if j < len(ha) else j
                    out["examples"].append(f"row {i + 1} [{col}]: {u!r} vs {v!r}")
    out["match"] = (out["header_equal"] and out["rows"][0] == out["rows"][1]
                    and out["cell_diffs"] == 0)
    return out


def compare_outputs(run_results: Path, reference_dir: Path, tol: float = 0.01) -> list[dict]:
    res = []
    for f, _ in GD_REPORTS:
        a, b = Path(run_results) / f, Path(reference_dir) / f
        if not a.exists() or not b.exists():
            res.append({"file": f, "match": False,
                        "missing": [str(p) for p in (a, b) if not p.exists()]})
            continue
        res.append(compare_reports(a, b, tol))
    return res


def replay_params() -> dict:
    """GD settings of the SRP package's 7 Aug 2026 UI run (taralog 4273-4419)."""
    return {**GD_DEFAULTS, "study_selection": 2, "study_queue": "SRP26",
            "dc_loading_cutoff": 90, "top20_limit": 26, "ignore_no_q_viol": 1,
            "study_mode": 2, "mw_dispatch_limit": 5000, "corr_switching": 1}
