"""ATC by TrLim: case + sub/con/mon -> by-bus DC transfer limits to a sink.

The methodology is the PowerGEM UI's own, not a reconstruction:

    TrLimMode 1 (Parallel / by-bus)  ->  trlim  ->  %save %csv busDCMaxTransfer

`templates/trlim_ui_recipe.txt` is the comm TARA generated in the UI
(`studies/ATC_ED4_ED5/03_TARA_Inputs/reference_ui/`, load + TrLim exports
concatenated) with only paths, case, subsystem names and a handful of study
parameters turned into @@TOKENS@@. Run 54 replayed it and matched the UI's
TrLim.csv on all 66 data rows; runs 55-57 used it for the ED-5 study and were
signed off by the user.

What the recipe gets right that earlier attempts did not:

- By-bus parallel mode injects at each bus of the sending subsystem directly,
  one transfer path per bus. No test generators are needed (runs 51/52 added
  some; they perturbed the case and never participated).
- `scaleTrLim` + `Binding` (runs 49-53) is the proportional subsystem-scale
  path and is the wrong tool; `sumTrLim` after it segfaults TARA.
- Every option block is set BEFORE the case and the CONT/MONIT reads. Run 58
  showed that setting `opt cont` after the reads leaves `mulSecPrintMode` at
  its default when the .con is parsed (6596 contingencies instead of 6565,
  multi-section entries triple-counted). Do not reorder the template.

The result CSV is TARA's raw output. `summarize_trlim` reads it but never
rewrites it. It sorts rows into three kinds, because the report mixes them:

- relieved initial overloads: already over the limit, and the transfer
  REDUCES the flow (TARA's "Increasing Transfer relieves initial overload!").
  Informational only, never the limit.
- worsened initial overloads: already over the limit (HeadRoom < 0) and the
  transfer INCREASES the flow (Dfax > 0), so TrLim is negative. On a strict
  reading these make the incremental capability zero; they are reported
  separately so that call stays with the engineer.
- limiting constraints: HeadRoom >= 0. The smallest TrLim is the first limit.
"""

from __future__ import annotations

import csv
import re
import subprocess
from pathlib import Path

from tara_automation.commfile import syntax
from tara_automation.commfile.casechange import parse_warn_sum
from tara_automation.run_manager import Run

TEMPLATE = Path(__file__).parent / "templates" / "trlim_ui_recipe.txt"
TRACE_NAME = "load_trlim.txt"
RELIEVES_MSG = "relieves initial overload"

# Study parameters exposed by the template, with the values of the validated
# UI run (run 54). Anything not listed here is fixed by the recipe.
DEFAULT_PARAMS = {
    "rating_base": 1,          # opt cont monBranRatingBase (1-A..4-D)
    "rating_cont": 2,          # opt cont monBranRatingCont
    "sens_cutoff": 0.03,       # opt trlim SensCutOff (Dfax cutoff)
    "max_test_mw": 2000,       # opt trlim maxTransferTestMW
    "max_viol_per_cons": 5,    # opt trlim maxViolPerCons
    "max_cons_per_path": 200,  # opt trlim maxConstraintsPerPath
    "epc_version": 21,         # READ EPC <ver>
}

# Warnings that mean an input never took effect. Exit code 0 is not proof of
# anything (see docs/tara_engine_notes.md), so these fail the run.
FATAL_WARN_IDS = {
    206: "file could not be opened (sub/con/mon/case path)",
    4082: "study subsystem not found",
}


# ------------------------------------------------------------------ deck ----
def render_deck(
    *,
    results_dir: Path | str,
    trace_log: Path | str,
    trlim_csv: Path | str,
    case: Path | str,
    sub: Path | str,
    con: Path | str,
    mon: Path | str,
    send: str,
    recv: str,
    params: dict | None = None,
) -> str:
    """Fill the UI recipe. Pure text in, text out; no file checks."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    unknown = set(p) - set(DEFAULT_PARAMS)
    if unknown:
        raise KeyError(f"Unknown TrLim parameter(s): {sorted(unknown)}. "
                       f"Allowed: {sorted(DEFAULT_PARAMS)}")
    for name in (send, recv):
        if not name or '"' in name or " " in name:
            raise ValueError(f"Bad subsystem name: {name!r}")
    tokens = {
        "RESULTS": results_dir, "TRACE_LOG": trace_log, "TRLIM_CSV": trlim_csv,
        "CASE": case, "SUB": sub, "CON": con, "MON": mon,
        "SEND": send, "RECV": recv,
        "RATING_BASE": p["rating_base"], "RATING_CONT": p["rating_cont"],
        "SENS_CUTOFF": p["sens_cutoff"], "MAX_TEST_MW": p["max_test_mw"],
        "MAX_VIOL_PER_CONS": p["max_viol_per_cons"],
        "MAX_CONS_PER_PATH": p["max_cons_per_path"],
        "EPC_VERSION": p["epc_version"],
    }
    text = TEMPLATE.read_text(encoding="utf-8")
    for key, value in tokens.items():
        text = text.replace(f"@@{key}@@", _fmt(value))
    left = re.findall(r"@@\w+@@", text)
    if left:
        raise RuntimeError(f"Unfilled template tokens: {left}")
    return text


def _fmt(value) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def subsystem_names(sub_file: Path) -> dict[str, list[int]]:
    """SUBSYSTEM names in a .sub file -> the BUS numbers listed under each.

    Only used to fail fast on a mistyped --send/--recv and to know which
    by-bus paths to expect; TARA remains the authority on the file.
    """
    out: dict[str, list[int]] = {}
    current = None
    for raw in Path(sub_file).read_text(encoding="utf-8",
                                        errors="replace").splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        head = line.split()
        kw = head[0].upper()
        if kw == "SUBSYSTEM" and len(head) > 1:
            current = head[1].strip('"')
            out.setdefault(current, [])
        elif kw == "END":
            current = None
        elif kw == "BUS" and current and len(head) > 1 and head[1].isdigit():
            out[current].append(int(head[1]))
    return out


def check_inputs(case_file, sub, con, mon, sends: list[str], recv: str,
                 fmt: str = "EPC") -> dict[str, list[int]]:
    """Fail before a run is created. Returns the .sub's subsystem names."""
    if fmt.upper() != "EPC":
        # The recipe's READ line is the UI's EPC form. A PSSE read uses a
        # different menu sequence that has not been proven in this deck.
        raise ValueError("TrLim recipe is validated for EPC cases only")
    for tag, f in (("case", case_file), ("sub", sub), ("con", con), ("mon", mon)):
        f = Path(f)
        if not f.exists() or f.stat().st_size == 0:
            raise FileNotFoundError(f"{tag} file missing or empty: {f}")
    if Path(case_file).stat().st_size < 10_000:
        raise ValueError(f"Case is implausibly small: {case_file}")
    names = subsystem_names(Path(sub))
    for role, name in [("send", s) for s in sends] + [("recv", recv)]:
        if name not in names:
            raise KeyError(f"{role} subsystem '{name}' is not defined in "
                           f"{Path(sub).name}; found {sorted(names)}")
    return names


# ---------------------------------------------------------------- engine ----
class TrLimEngine:
    def __init__(self, tara_exe: Path):
        self.tara_exe = Path(tara_exe)

    def prepare(
        self,
        run: Run,
        case_file: Path,
        sub: Path,
        con: Path,
        mon: Path,
        send: str,
        recv: str,
        fmt: str = "EPC",
        params: dict | None = None,
    ) -> Path:
        names = check_inputs(case_file, sub, con, mon, [send], recv, fmt)
        # The deck chdir's into results/, so every path must be absolute.
        files = {k: Path(v).resolve() for k, v in
                 {"case": case_file, "sub": sub, "con": con, "mon": mon}.items()}
        trlim_csv = run.results_dir / f"{send}_TrLim.csv"
        deck = render_deck(
            results_dir=run.results_dir.resolve(),
            trace_log=(run.logs_dir / TRACE_NAME).resolve(),
            trlim_csv=trlim_csv.resolve(),
            case=files["case"], sub=files["sub"], con=files["con"],
            mon=files["mon"], send=send, recv=recv, params=params,
        )
        comm = run.comm_dir / "trlim.txt"
        comm.write_text(deck, encoding="utf-8")
        master = run.comm_dir / "master.txt"
        master.write_text(syntax.master_comm([comm.resolve()]), encoding="utf-8")
        bat = run.comm_dir / "run.bat"
        bat.write_text(syntax.bat_launcher(self.tara_exe, master.resolve()),
                       encoding="utf-8")

        run.manifest["trlim"] = {
            "send": send,
            "recv": recv,
            "send_buses": names[send],
            "sub": str(files["sub"]),
            "con": str(files["con"]),
            "mon": str(files["mon"]),
            "params": {**DEFAULT_PARAMS, **(params or {})},
            "trlim_csv": str(trlim_csv),
            "bat": str(bat),
            "recipe": "UI by-bus: TrLimMode 1 + trlim + busDCMaxTransfer",
            "recipe_template": TEMPLATE.name,
            "recipe_validated_by": "run 54 == PowerGEM UI TrLim.csv",
        }
        run.save_manifest()
        return bat

    def execute(self, run: Run) -> int:
        bat = run.manifest.get("trlim", {}).get("bat")
        if not bat:
            raise RuntimeError("call prepare() first")
        # cwd = the run's own folder so parallel tara.exe scratch files never
        # collide (see engine.py). The console echo (~800 kB) goes to a log,
        # not the terminal; the %trace log holds the same session.
        with open(run.logs_dir / "tara_console.txt", "wb") as console:
            rc = subprocess.call([str(bat)], shell=False, cwd=str(run.root),
                                 stdout=console, stderr=subprocess.STDOUT)
        run.manifest["tara_exit_code"] = rc
        run.save_manifest()
        return rc

    @staticmethod
    def check_outputs(run: Run) -> dict:
        """Did TrLim run on the inputs we asked for? Never trusts exit code."""
        t = run.manifest.get("trlim", {})
        csv_path = Path(t.get("trlim_csv", ""))
        report = parse_trlim_csv(csv_path) if csv_path.exists() else None
        warnings = parse_warn_sum(run.results_dir / "Warn_Sum.csv")
        fatal = [w for w in warnings if w["id"] in FATAL_WARN_IDS]
        problems: list[str] = []
        if run.manifest.get("tara_exit_code") != 0:
            problems.append(f"TARA exit code {run.manifest.get('tara_exit_code')}")
        if report is None:
            problems.append(f"no TrLim CSV at {csv_path.name}")
        else:
            if not report["n_contingencies"]:
                problems.append("contingency file not read (0 contingencies)")
            if not report["rows"]:
                problems.append("TrLim CSV has no data rows")
            if report["ragged_rows"]:
                problems.append(f"{report['ragged_rows']} rows with a column "
                                f"count different from the header")
            wrong_recv = {r["Receiving System"] for r in report["rows"]} - {t.get("recv")}
            if wrong_recv:
                problems.append(f"unexpected receiving system(s) {sorted(wrong_recv)}")
        for w in fatal:
            problems.append(f"WarnID-{w['id']}: {FATAL_WARN_IDS[w['id']]}")

        notes: list[str] = []
        if report is not None:
            reported = {_path_bus(p) for p in report["paths"]}
            missing = [b for b in t.get("send_buses", []) if b not in reported]
            if missing:
                # Not a failure: a bus whose every Dfax is below the cutoff
                # legitimately reports no rows. Worth a look all the same.
                notes.append(f"no rows for sending bus(es) {missing}")
        return {"ok": not problems, "problems": problems, "notes": notes,
                "report": report}


def _path_bus(path_name: str) -> int | None:
    m = re.match(r"(\d+)_", path_name)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- parse ----
_NUMERIC = ("Max Test MW", "TrLim", "Dfax", "Rate Base", "Rate Cont",
            "Base Flow MW (No Transfer)", "Cont Flow MW (No Transfer)",
            "DC %Loading (No Transfer)", "HeadRoom")


def parse_trlim_csv(path: Path) -> dict:
    """Read a busDCMaxTransfer report. Rows keep TARA's order and all columns
    (stripped); the numeric columns above are added as floats."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\(total (\d+) contingencies\)", text)
    n_cont = int(m.group(1)) if m else 0
    lines = text.splitlines()
    header_idx = next((i for i, l in enumerate(lines)
                       if l.startswith("Sending System")), None)
    if header_idx is None:
        return {"n_contingencies": n_cont, "header": [], "rows": [],
                "paths": [], "ragged_rows": 0}
    reader = csv.reader(lines[header_idx:])
    header = [h.strip() for h in next(reader)]
    rows, ragged = [], 0
    for rec in reader:
        if not any(f.strip() for f in rec):
            continue
        if len(rec) != len(header):
            ragged += 1
            continue
        row = {h: v.strip() for h, v in zip(header, rec) if h}
        for col in _NUMERIC:
            row[col] = _num(row.get(col, ""))
        rows.append(row)
    paths = list(dict.fromkeys(r["Sending System"] for r in rows))
    return {"n_contingencies": n_cont, "header": [h for h in header if h],
            "rows": rows, "paths": paths, "ragged_rows": ragged}


def _num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None  # e.g. "-" for Cont Flow on base-case rows


def summarize_trlim(report: dict) -> dict:
    """Per-path limit, keeping pre-existing overloads visible (module doc)."""
    out = {"n_contingencies": report["n_contingencies"], "paths": {}}
    for path in report["paths"]:
        rows = [r for r in report["rows"] if r["Sending System"] == path]
        relieved = [r for r in rows if RELIEVES_MSG in r["Special Message"]]
        worsened = [r for r in rows if r not in relieved
                    and (r["HeadRoom"] or 0) < 0 and (r["Dfax"] or 0) > 0]
        limiting = [r for r in rows if r not in relieved
                    and (r["HeadRoom"] or 0) >= 0 and r["TrLim"] is not None]
        first = min(limiting, key=lambda r: r["TrLim"], default=None)
        out["paths"][path] = {
            "receiving": rows[0]["Receiving System"],
            "max_test_mw": rows[0]["Max Test MW"],
            "rows": len(rows),
            "first_limit_mw": first["TrLim"] if first else None,
            "first_limit": _brief(first) if first else None,
            "worsened_initial_overloads": [_brief(r) for r in worsened],
            "relieved_initial_overloads": len(relieved),
        }
    return out


def _brief(r: dict) -> dict:
    return {
        "facility": " ".join(r["Monitored Facility"].split()),
        "short": f"{r['Fr Name']}-{r['To Name']} {r['kVs']}",
        "contingency": " ".join(r["Cont Name"].split()),
        "trlim_mw": r["TrLim"],
        "dfax": r["Dfax"],
        "headroom_mw": r["HeadRoom"],
        "rate_cont": r["Rate Cont"],
        "loading_pct_no_transfer": r["DC %Loading (No Transfer)"],
    }


def summary_line(send: str, recv: str, summary: dict) -> str:
    parts = []
    for path, s in summary["paths"].items():
        name = path.rstrip("_")
        if s["first_limit"]:
            lim = f"{s['first_limit_mw']:.1f} MW on {s['first_limit']['short']}"
        else:
            lim = f">= {s['max_test_mw']:.0f} MW (no limit found)"
        worse = len(s["worsened_initial_overloads"])
        if worse:
            lim += f" [{worse} pre-existing overload(s) worsened]"
        parts.append(f"{name}: {lim}")
    return (f"TrLim {send} -> {recv}, {summary['n_contingencies']} conts; "
            + "; ".join(parts))
