"""Comm-file engine: build and (on Windows) execute a load-solve-extract run.

Produces, inside a numbered run folder:
    comm/basecase.txt      READ -> solve -> lfreview CSV exports
    comm/master.txt        %include basecase.txt ; stop ; %exit
    comm/run.bat           "tara.exe" %run "master.txt"
    results/*.csv          written by TARA when the bat executes
    logs/tara_trace.log    TARA trace output
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tara_automation.commfile import syntax
from tara_automation.extract import COMM_REPORTS
from tara_automation.run_manager import Run


class CommEngine:
    def __init__(self, tara_exe: Path):
        self.tara_exe = Path(tara_exe)

    def prepare(
        self,
        run: Run,
        case_file: Path,
        fmt: str = "EPC",
        version: int = 21,
        reports: list[tuple[str, str, str]] | None = None,
    ) -> Path:
        """Write comm files + bat into the run folder. Returns the bat path.

        reports: (filename_stem, prefix, spec) tuples; defaults to the full
        base-case report set (extract.COMM_REPORTS, mirrors the TARA UI export).
        """
        reports = reports or COMM_REPORTS
        trace_log = run.logs_dir / "tara_trace.log"

        lines = [
            syntax.trace(trace_log),
            "",
            syntax.read_case(case_file, fmt=fmt, version=version),
            syntax.solve_fdec(),
            "",
            syntax.sarea_all(),
        ]
        for name, prefix, spec in reports:
            lines.append(
                syntax.report_save(prefix, run.results_dir / f"{name}.csv", spec)
            )
        lines.append(syntax.sarea_all())
        body = "\n".join(lines) + "\n"

        basecase_comm = run.comm_dir / "basecase.txt"
        basecase_comm.write_text(body, encoding="utf-8")

        master = run.comm_dir / "master.txt"
        master.write_text(syntax.master_comm([basecase_comm]), encoding="utf-8")

        bat = run.comm_dir / "run.bat"
        bat.write_text(syntax.bat_launcher(self.tara_exe, master), encoding="utf-8")

        run.manifest["comm"] = {
            "basecase": str(basecase_comm),
            "master": str(master),
            "bat": str(bat),
            "reports": [name for name, _, _ in reports],
            "format": fmt,
            "version": version,
        }
        run.save_manifest()
        return bat

    def execute(self, run: Run) -> int:
        """Run the bat (Windows only — requires tara.exe)."""
        bat = run.manifest.get("comm", {}).get("bat")
        if not bat:
            raise RuntimeError("Run has no prepared bat file; call prepare() first.")
        # cwd = the run's own folder: tara.exe drops scratch files (e.g.
        # callstack logs) in its working directory, so parallel instances
        # must not share one. All comm input/output paths are absolute.
        rc = subprocess.call([str(bat)], shell=False, cwd=str(run.root))
        run.manifest["tara_exit_code"] = rc
        run.save_manifest()
        return rc

    @staticmethod
    def check_outputs(run: Run) -> dict[str, bool]:
        """Which expected result CSVs exist (post-execution sanity check)."""
        expected = run.manifest.get("comm", {}).get("reports", [])
        return {t: (run.results_dir / f"{t}.csv").exists() for t in expected}
