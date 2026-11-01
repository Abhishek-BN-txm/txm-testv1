"""Case-change engine: case + INCH file -> solved, modified case.

Comm sequence, taken from the `_comm.txt` TARA generated when a case was
loaded with an INCH attached (probe 1, 2026-09-04):

    <dimension block>
    READ EPC 21 "case.epc" 0
    READ CIMINCH "changes.inch" 0 0
    warn %save "Warn_Sum.csv"  %csv sum  0 0

`READ CIMINCH` is TARA menu L2 function 12; it is in no manual. It must come
after the case read and before the solve. The solve and the `#SAVE` that
writes the modified case both live *inside* the INCH file, so this comm does
not repeat them — it only exports the warning tables afterwards.

Those warning tables are the point. Probe 1 showed an INCH keyword TARA does
not recognise is rejected with a warning and then *silently defaulted*: a
mistyped `SCALE_LOAD` option set area 14's entire load to 2 MW instead of
raising it by 250 MW, and the run still finished with exit code 0 and wrote a
case. Exit codes cannot be trusted here, so `check_outputs` fails a run whose
warning summary carries any of the INCH error IDs below.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tara_automation.commfile import syntax
from tara_automation.commfile.options import load_options, render_block
from tara_automation.run_manager import Run

# INCH warning IDs that mean "your change did not happen the way you wrote
# it" — every one observed in probe 1, with what it actually cost.
INCH_ERROR_IDS = {
    1001: "keyword not in the INCH dictionary — the option was ignored and "
          "TARA silently used its default",
    1607: "duplicate transformer between the same buses and circuit ID — "
          "the record was not added",
    1644: "INCH template format error — the record was not applied as written",
    1652: "transformer regulated bus not found — the record was not added",
}

# Warnings TARA raises against the *case*, not the INCH; these are pre-existing
# and appear identically on an unmodified run, so they must not fail a run.
_CASE_NOISE_IDS = {1200, 1209, 1210, 1414, 1531, 1532, 1533, 1534, 1778,
                   1800, 1826, 1100}


class CaseChangeEngine:
    def __init__(self, tara_exe: Path):
        self.tara_exe = Path(tara_exe)

    def prepare(
        self,
        run: Run,
        case_file: Path,
        inch_file: Path,
        out_case: Path,
        fmt: str = "EPC",
        version: int = 21,
    ) -> Path:
        """Write the case-change comm + bat into the run folder."""
        for f, tag in ((case_file, "case"), (inch_file, "inch")):
            f = Path(f)
            if not f.exists() or f.stat().st_size == 0:
                raise FileNotFoundError(f"{tag} file missing or empty: {f}")
        if Path(case_file).stat().st_size < 10_000:
            raise ValueError(
                f"Case is implausibly small ({Path(case_file).stat().st_size} "
                f"bytes): {case_file}")

        # The comm chdir's to results/, so every path must be absolute or it
        # resolves against the wrong directory (run 17: TARA WarnID-206).
        case_file = Path(case_file).resolve()
        inch_file = Path(inch_file).resolve()
        out_case = Path(out_case).resolve()
        out_case.parent.mkdir(parents=True, exist_ok=True)

        blocks = load_options()
        results = run.results_dir
        trace_log = run.logs_dir / "casechange_log.txt"

        parts = [
            f'chdir "{results}"',
            f'%trace "{trace_log}" %txt',
            render_block(blocks["dimension_block"]),
            syntax.read_case(case_file, fmt=fmt, version=version),
            syntax.read_inch(inch_file),
            # The INCH's own #SOLVE_FDEC and #SAVE have already run by here.
            f'warn %save "{results / "Warn_Sum.csv"}"  %csv sum 0 0',
            f'warn %save "{results / "Warn_List.csv"}"  %csv list 0 0',
        ]
        body = "\n".join(parts) + "\n"

        comm = run.comm_dir / "casechange.txt"
        comm.write_text(body, encoding="utf-8")
        master = run.comm_dir / "master.txt"
        master.write_text(syntax.master_comm([comm]), encoding="utf-8")
        bat = run.comm_dir / "run.bat"
        bat.write_text(syntax.bat_launcher(self.tara_exe, master),
                       encoding="utf-8")

        run.manifest["casechange"] = {
            "inch": str(inch_file),
            "out_case": str(out_case),
            "format": fmt,
            "version": version,
            "bat": str(bat),
        }
        run.save_manifest()
        return bat

    def execute(self, run: Run) -> int:
        bat = run.manifest.get("casechange", {}).get("bat")
        if not bat:
            raise RuntimeError("call prepare() first")
        # cwd = the run's own folder, so tara.exe scratch files stay put and
        # parallel instances cannot collide (see engine.py).
        rc = subprocess.call([str(bat)], shell=False, cwd=str(run.root))
        run.manifest["tara_exit_code"] = rc
        run.save_manifest()
        return rc

    @staticmethod
    def check_outputs(run: Run) -> dict:
        """Did the run produce a usable case, and did the INCH apply cleanly?

        Returns a dict with `ok` plus the evidence behind it. Never trusts the
        exit code alone — see the module docstring.
        """
        cc = run.manifest.get("casechange", {})
        out_case = Path(cc.get("out_case", ""))
        size = out_case.stat().st_size if out_case.exists() else 0
        warnings = parse_warn_sum(run.results_dir / "Warn_Sum.csv")
        errors = [w for w in warnings if w["id"] in INCH_ERROR_IDS]
        result = {
            "case_written": out_case.exists(),
            "case_bytes": size,
            "case_plausible": size >= 10_000,
            "inch_errors": errors,
            "warnings_parsed": len(warnings),
        }
        result["ok"] = (result["case_plausible"] and not errors
                        and run.manifest.get("tara_exit_code") == 0)
        return result

    @staticmethod
    def summary_line(checks: dict) -> str:
        if checks["ok"]:
            return (f"case written, {checks['case_bytes'] / 1e6:.1f} MB, "
                    f"no INCH errors")
        if not checks["case_written"]:
            return "no case written"
        if not checks["case_plausible"]:
            return f"case only {checks['case_bytes']} bytes"
        ids = ", ".join(str(e["id"]) for e in checks["inch_errors"])
        return f"INCH errors {ids} — changes did not apply as written"


def parse_warn_sum(path: Path) -> list[dict]:
    """Parse TARA's Warn_Sum.csv into {id, count, severity, message, file}.

    Format (unquoted, message may itself contain commas, so the message is
    reconstructed from the middle rather than trusted to a naive split):

        WarnID, #Warn, Sever,Message,File,
          1652,     1,2-error !  , Can't find regulated bus..., File x.inch
    """
    path = Path(path)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 4 or not fields[0].isdigit():
            continue
        try:
            wid, count = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        severity = fields[2]
        rest = [f for f in fields[3:] if f]
        file_part = ""
        if rest and rest[-1].lower().startswith("file "):
            file_part = rest.pop()[5:].strip()
        out.append({"id": wid, "count": count, "severity": severity,
                    "message": ", ".join(rest), "file": file_part,
                    "case_noise": wid in _CASE_NOISE_IDS})
    return out


def describe_inch_errors(warnings: list[dict]) -> str:
    """Human-readable account of INCH errors, for a run log or a review."""
    errs = [w for w in warnings if w["id"] in INCH_ERROR_IDS]
    if not errs:
        return "No INCH errors."
    lines = []
    for w in errs:
        lines.append(f"  WarnID-{w['id']} x{w['count']}: {w['message']}")
        lines.append(f"      meaning: {INCH_ERROR_IDS[w['id']]}")
    return "\n".join(lines)
