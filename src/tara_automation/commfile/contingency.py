"""AC contingency (N-1) engine: case + sub/con/mon -> accont -> violations.

Comm sequence replicated from the proven RedButte study comm
(reference Commfiles/7): dimension pre-sizing, read+solve case, read study
files, re-solve, tuned option blocks, report configs, `cont select all`,
loading cutoff, `cont accont`, then BranchViol/VoltViol CSV exports.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tara_automation.commfile import syntax
from tara_automation.commfile.options import (
    apply_overrides, load_options, render_analysis_blocks, render_block,
)
from tara_automation.run_manager import Run


class ContingencyEngine:
    def __init__(self, tara_exe: Path):
        self.tara_exe = Path(tara_exe)

    def prepare(
        self,
        run: Run,
        case_file: Path,
        sub_file: Path,
        con_file: Path,
        mon_file: Path,
        fmt: str = "EPC",
        version: int = 21,
        loading_cutoff: float = 70.0,
        options_overrides: dict | None = None,
    ) -> Path:
        """Write the contingency comm + bat into the run folder."""
        for f, tag in ((case_file, "case"), (sub_file, "sub"),
                       (con_file, "con"), (mon_file, "mon")):
            if not Path(f).exists() or Path(f).stat().st_size == 0:
                raise FileNotFoundError(f"{tag} file missing or empty: {f}")

        # The comm chdir's to the results dir before reading these files, so
        # any relative path would resolve against results/ and fail to open
        # (TARA WarnID-206). Absolutize every input path so the comm is
        # independent of how the caller passed them (run 17 diagnosis).
        case_file = Path(case_file).resolve()
        sub_file = Path(sub_file).resolve()
        con_file = Path(con_file).resolve()
        mon_file = Path(mon_file).resolve()

        blocks = apply_overrides(load_options(), options_overrides)
        results = run.results_dir
        trace_log = run.logs_dir / "contingency_log.txt"

        parts = [
            f'chdir "{results}"',
            f'%trace "{trace_log}" %txt',
            render_block(blocks["dimension_block"]),
            syntax.read_case(case_file, fmt=fmt, version=version),
            "lfopt misc\n busOutputMode 2 0 0 0",
            "solve\n fdec 0 0 ",
            f'READ SUBSYS "{sub_file}"',
            f'CONT "{con_file}"',
            f'MONIT "{mon_file}"',
            "0 0",
            "solve\n fdec 0 0 ",
            render_analysis_blocks(blocks),
            "cont select all 0 0 ",
            f"opt cont LoadingCutoff {loading_cutoff:g}\n 0 0",
            " cont accont 0",
            f'cont %save "{results / "VoltViol.csv"}" %csv acvolt 0 0',
            f'cont %save "{results / "BranchViol.csv"}" %csv acbranch '
            f"{loading_cutoff:g} 0 0",
        ]
        body = "\n".join(parts) + "\n"

        comm = run.comm_dir / "contingency.txt"
        comm.write_text(body, encoding="utf-8")
        master = run.comm_dir / "master.txt"
        master.write_text(syntax.master_comm([comm]), encoding="utf-8")
        bat = run.comm_dir / "run.bat"
        bat.write_text(syntax.bat_launcher(self.tara_exe, master), encoding="utf-8")

        run.manifest["contingency"] = {
            "sub": str(sub_file), "con": str(con_file), "mon": str(mon_file),
            "format": fmt, "version": version,
            "loading_cutoff": loading_cutoff,
            "options_overrides": options_overrides or {},
            "bat": str(bat),
        }
        run.save_manifest()
        return bat

    def execute(self, run: Run) -> int:
        bat = run.manifest.get("contingency", {}).get("bat")
        if not bat:
            raise RuntimeError("call prepare() first")
        # cwd = the run's own folder (see engine.py) — parallel-safe scratch.
        rc = subprocess.call([str(bat)], shell=False, cwd=str(run.root))
        run.manifest["tara_exit_code"] = rc
        run.save_manifest()
        return rc

    @staticmethod
    def check_outputs(run: Run) -> dict[str, bool]:
        return {name: (run.results_dir / f"{name}.csv").exists()
                for name in ("BranchViol", "VoltViol")}
