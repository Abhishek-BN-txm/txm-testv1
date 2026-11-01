"""Run management: sequential numbered runs, manifests, and a daily run log.

Design (Task 2):
- Every run gets the next sequential number N under ``runs/``:

    runs/
    ├── RUN_LOG.md          human-readable summary, grouped by day (tracked in git)
    └── N/
        ├── manifest.json   what ran: engine, case, options, status (tracked)
        ├── comm/           comm files + bat (comm engine only; tracked)
        ├── results/        extracted CSVs (untracked, regenerable)
        └── logs/           TARA trace / API logs (untracked)

- A run is created with ``RunManager.create_run`` and closed with
  ``Run.finalize``, which updates the manifest and appends one summary line
  to RUN_LOG.md under today's date heading.
"""

from __future__ import annotations

import getpass
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MANIFEST_NAME = "manifest.json"
RUN_LOG_NAME = "RUN_LOG.md"

RUN_LOG_HEADER = (
    "# Run log\n\n"
    "One line per run, grouped by day. Manifests in `runs/N/manifest.json` "
    "carry full detail.\n"
)


@dataclass
class Run:
    """A single numbered run. Created via RunManager.create_run."""

    number: int
    root: Path  # runs/N
    manifest: dict = field(default_factory=dict)

    @property
    def comm_dir(self) -> Path:
        return self.root / "comm"

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def save_manifest(self) -> None:
        (self.root / MANIFEST_NAME).write_text(
            json.dumps(self.manifest, indent=2, default=str), encoding="utf-8"
        )

    def finalize(self, status: str, summary: str = "") -> None:
        """Close out the run: update manifest and append to RUN_LOG.md.

        status: e.g. 'success', 'failed', 'prepared' (bat built, not yet run)
        summary: one line for the run log, e.g. 'solved in 7 iters, losses 812 MW'
        """
        self.manifest["status"] = status
        self.manifest["finalized_at"] = datetime.now().isoformat(timespec="seconds")
        if summary:
            self.manifest["summary"] = summary
        self.save_manifest()
        _append_run_log(
            runs_root=self.root.parent,
            number=self.number,
            engine=self.manifest.get("engine", "?"),
            label=self.manifest.get("label", ""),
            case=Path(str(self.manifest.get("case", "?"))).name,
            status=status,
            summary=summary,
        )


class RunManager:
    """Creates sequentially numbered runs under a runs root."""

    def __init__(self, runs_root: Path):
        self.runs_root = Path(runs_root)
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def next_number(self) -> int:
        nums = [
            int(p.name)
            for p in self.runs_root.iterdir()
            if p.is_dir() and re.fullmatch(r"\d+", p.name)
        ]
        return max(nums, default=0) + 1

    def create_run(
        self,
        engine: str,
        case: Path | str,
        label: str = "",
        options: dict | None = None,
    ) -> Run:
        n = self.next_number()
        root = self.runs_root / str(n)
        run = Run(number=n, root=root)
        for d in (run.comm_dir, run.results_dir, run.logs_dir):
            d.mkdir(parents=True, exist_ok=True)
        run.manifest = {
            "run": n,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "user": getpass.getuser(),
            "engine": engine,  # 'comm' (historic runs 2/4 were 'pygem')
            "case": str(case),
            "label": label,
            "options": options or {},
            "status": "created",
        }
        run.save_manifest()
        return run

    def load_run(self, number: int) -> Run:
        root = self.runs_root / str(number)
        manifest_path = root / MANIFEST_NAME
        if not manifest_path.exists():
            raise FileNotFoundError(f"No manifest for run {number} at {manifest_path}")
        run = Run(number=number, root=root)
        run.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return run


def _append_run_log(
    runs_root: Path,
    number: int,
    engine: str,
    label: str,
    case: str,
    status: str,
    summary: str,
) -> None:
    log_path = runs_root / RUN_LOG_NAME
    today = datetime.now().strftime("%Y-%m-%d")
    text = log_path.read_text(encoding="utf-8") if log_path.exists() else RUN_LOG_HEADER
    day_heading = f"## {today}"
    line = f"- **Run {number}** [{engine}] {case}"
    if label:
        line += f" — {label}"
    line += f" — {status}"
    if summary:
        line += f": {summary}"
    if day_heading in text:
        text += f"{line}\n"
    else:
        text += f"\n{day_heading}\n\n{line}\n"
    log_path.write_text(text, encoding="utf-8")
