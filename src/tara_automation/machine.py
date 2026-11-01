"""Per-machine settings.

Machine-specific paths (tara.exe location, scratch directories, reference repo)
live in ``settings/machine.json`` at the repo root. That file is git-ignored so
each machine keeps its own copy; ``settings/machine.example.json`` is the
tracked template.

Usage:
    from tara_automation.machine import load_machine
    m = load_machine()
    m.tara_exe          # Path to tara.exe
    m.reference_root    # Path to the read-only prior-work folder (may be None)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# repo_root/src/tara_automation/machine.py -> repo_root
REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_DIR = REPO_ROOT / "settings"
MACHINE_FILE = SETTINGS_DIR / "machine.json"
EXAMPLE_FILE = SETTINGS_DIR / "machine.example.json"


@dataclass(frozen=True)
class MachineSettings:
    repo_root: Path
    tara_exe: Path
    scratch_dir: Path
    reference_root: Path | None = None

    def validate(self, require_tara: bool = False) -> list[str]:
        """Return a list of problems (empty list = OK)."""
        problems: list[str] = []
        if require_tara and not self.tara_exe.exists():
            problems.append(f"tara.exe not found at: {self.tara_exe}")
        if self.reference_root is not None and not self.reference_root.exists():
            problems.append(f"reference_root not found at: {self.reference_root}")
        return problems


def load_machine(path: Path | None = None) -> MachineSettings:
    """Load settings/machine.json (or an explicit path).

    Raises FileNotFoundError with a helpful message if the file is missing.
    """
    src = Path(path) if path else MACHINE_FILE
    if not src.exists():
        raise FileNotFoundError(
            f"Machine settings not found: {src}\n"
            f"Copy {EXAMPLE_FILE.name} to machine.json in {SETTINGS_DIR} "
            f"and edit the paths for this machine."
        )
    raw = json.loads(src.read_text(encoding="utf-8"))
    ref = raw.get("reference_root")
    return MachineSettings(
        repo_root=REPO_ROOT,
        tara_exe=Path(raw["tara_exe"]),
        scratch_dir=Path(raw.get("scratch_dir", str(REPO_ROOT / "_scratch"))),
        reference_root=Path(ref) if ref else None,
    )
