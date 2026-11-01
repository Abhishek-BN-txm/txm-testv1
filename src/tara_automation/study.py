"""Study project: scaffold + persistent context for a TARA study.

A *study* bundles a set of base cases and one contingency input set
(sub/con/mon) under `studies/<Name>/`, with a tracked `study.json` that is the
project's memory: what it is (objective, area), what cases it holds (with
roles/labels), the contingency inputs, the options (rating basis, cutoff), the
session notes, and — as stages run — the run numbers each stage produced. The
pipeline (`study_pipeline.py`) reads and updates this config, so the project
stays self-describing (a later session, or a skill-less reader, can reconstruct
exactly what happened).

Cases are large READ-ONLY inputs and stay at their source path; only the small
sub/con/mon files are copied into `03_TARA_Inputs/`.
"""

from __future__ import annotations

import getpass
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

NUMBERED_FOLDERS = [
    "01_References", "02_Base_Cases", "03_TARA_Inputs", "04_TARA_Outputs",
    "05_Analysis", "06_Reports", "07_Correspondence",
]

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"


class GateError(RuntimeError):
    """A stage asked for results that have not been signed off."""


def detect_format_version(case: Path) -> tuple[str, int]:
    """(fmt, read-keyword version). EPC → 21 (this TARA build rejects the
    header's 22); PSSE RAW → REV from the header (field 3), default 34."""
    fmt = "EPC" if case.suffix.lower() == ".epc" else "RAW"
    if fmt == "EPC":
        return "EPC", 21
    try:
        with open(case, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("@!") or not line.strip():
                    continue
                rev = int(float(line.split(",")[2].split("/")[0]))
                return "RAW", rev if 24 <= rev <= 36 else 34
    except (OSError, ValueError, IndexError):
        pass
    return "RAW", 34


@dataclass
class CaseSpec:
    path: str
    label: str
    role: str          # "baseline" | "post"
    fmt: str
    version: int


@dataclass
class StudyConfig:
    name: str
    created: str
    objective: str
    area: str
    software: str
    cases: list[CaseSpec]
    sub: str | None
    con: str | None
    mon: str | None
    rating_cont: int
    loading_cutoff: float
    # Case-change workbook (task 11). When set, the `casechange` stage builds
    # an INCH per scenario, applies it, and appends each modified case here as
    # a post case — so every later stage treats it as an ordinary base case.
    changes: str | None = None
    # ATC / transfer-capability stage (task 13): sending subsystem name(s) and
    # the sink, both defined in the study's .sub file, plus TrLim parameters.
    atc: dict | None = None
    # Footprint YAML (task 17). When set, the `footprint` stage compiles it
    # against the baseline's basecase run and points sub (and con/mon when
    # the YAML says `auto`) at the compiled files.
    footprint: str | None = None
    notes: list[str] = field(default_factory=list)
    # stage -> {label: run_number}; deliverables -> {name: path}
    runs: dict = field(default_factory=dict)
    deliverables: dict = field(default_factory=dict)
    # stage -> review gate: status, the verdict it rests on, and the run
    # fingerprint it was granted for (see Study.approve).
    gates: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StudyConfig":
        cases = [CaseSpec(**c) for c in d.get("cases", [])]
        d = {**d, "cases": cases}
        return cls(**d)


class Study:
    def __init__(self, root: Path, config: StudyConfig):
        self.root = Path(root)
        self.config = config

    # ---- paths ----
    @property
    def config_path(self) -> Path:
        return self.root / "study.json"

    @property
    def inputs_dir(self) -> Path:
        return self.root / "03_TARA_Inputs"

    @property
    def analysis_dir(self) -> Path:
        return self.root / "05_Analysis"

    @property
    def reports_dir(self) -> Path:
        return self.root / "06_Reports"

    # ---- case helpers ----
    def baseline(self) -> CaseSpec:
        return next(c for c in self.config.cases if c.role == "baseline")

    def posts(self) -> list[CaseSpec]:
        return [c for c in self.config.cases if c.role != "baseline"]

    def has_case(self, label: str) -> bool:
        return any(c.label == label for c in self.config.cases)

    def add_case(self, spec: CaseSpec) -> None:
        """Append (or replace) a post case — used by the casechange stage.

        Keyed on label so re-running the stage updates in place instead of
        stacking duplicates onto study.json."""
        self.config.cases = [c for c in self.config.cases
                             if c.label != spec.label] + [spec]

    def ordered(self) -> list[CaseSpec]:
        return [self.baseline()] + self.posts()

    # ---- review gates ----
    #
    # A gate is the user's sign-off on one stage's results. Two rules make it
    # worth having:
    #
    #   1. Approval is granted by a person, never by a stage or an automated
    #      reviewer. The only writer of `approved` is `scripts/study.py
    #      approve`, run by a person.
    #   2. Approval binds to the exact runs reviewed (the fingerprint). Re-run
    #      the stage and the approval lapses — you cannot ship a number that
    #      nobody looked at just because an older version of it passed.

    def fingerprint(self, stage: str) -> str:
        """Identity of what a stage currently holds: its run numbers."""
        runs = self.config.runs.get(stage, {})
        return ",".join(f"{k}={v}" for k, v in sorted(runs.items()))

    def mark_for_review(self, stage: str) -> dict:
        """Called by a stage once it succeeds: results exist, nobody has read
        them yet. Resets any earlier approval, because the runs just changed."""
        gate = {"status": PENDING, "fingerprint": self.fingerprint(stage),
                "updated_at": datetime.now().isoformat(timespec="seconds")}
        self.config.gates[stage] = gate
        return gate

    def gate(self, stage: str) -> dict:
        return self.config.gates.get(stage, {"status": "missing"})

    def gate_status(self, stage: str) -> str:
        """Effective status, which is not always the stored one: an approval
        granted for different runs is stale and reads as pending."""
        g = self.gate(stage)
        if g.get("status") == APPROVED and g.get("fingerprint") != self.fingerprint(stage):
            return "stale"
        return g.get("status", "missing")

    def require_approved(self, stage: str) -> None:
        """Guard for anything outward-facing. Raises with the way forward."""
        status = self.gate_status(stage)
        if status == APPROVED:
            return
        name = self.config.name
        how = (f"review the runs, then:  python scripts/study.py approve "
               f"--name {name} --stage {stage} --verdict <path to verdict>")
        if status == "missing":
            raise GateError(f"stage '{stage}' has produced nothing to approve "
                            f"in study '{name}'.")
        if status == "stale":
            g = self.gate(stage)
            raise GateError(
                f"stage '{stage}' was approved for runs [{g.get('fingerprint')}] "
                f"but now holds [{self.fingerprint(stage)}]. The results "
                f"changed after sign-off, so the approval has lapsed.\n{how}")
        if status == REJECTED:
            raise GateError(f"stage '{stage}' was rejected: "
                            f"{self.gate(stage).get('note', '(no note)')}\n{how}")
        raise GateError(f"stage '{stage}' results are awaiting your review in "
                        f"study '{name}'.\n{how}")

    def approve(self, stage: str, verdict: str | Path | None = None,
                note: str = "", by: str | None = None) -> dict:
        """Record a human sign-off. Only `scripts/study.py approve` calls this."""
        if not self.config.runs.get(stage):
            raise GateError(f"stage '{stage}' has no runs to approve")
        gate = {
            "status": APPROVED,
            "fingerprint": self.fingerprint(stage),
            "by": by or getpass.getuser(),
            "at": datetime.now().isoformat(timespec="seconds"),
            "verdict": str(verdict) if verdict else None,
            "note": note,
        }
        self.config.gates[stage] = gate
        return gate

    def reject(self, stage: str, note: str = "", by: str | None = None) -> dict:
        gate = {"status": REJECTED, "fingerprint": self.fingerprint(stage),
                "by": by or getpass.getuser(),
                "at": datetime.now().isoformat(timespec="seconds"), "note": note}
        self.config.gates[stage] = gate
        return gate

    # ---- persistence ----
    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self.config.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, studies_root: Path, name: str) -> "Study":
        root = Path(studies_root) / name
        cfg_path = root / "study.json"
        if not cfg_path.exists():
            raise FileNotFoundError(f"No study.json for '{name}' at {cfg_path}")
        cfg = StudyConfig.from_dict(json.loads(cfg_path.read_text(encoding="utf-8")))
        return cls(root, cfg)

    @classmethod
    def init(
        cls,
        studies_root: Path,
        name: str,
        case_paths: list[Path],
        sub: Path | None = None,
        con: Path | None = None,
        mon: Path | None = None,
        objective: str = "",
        area: str = "",
        software: str = "TARA (PowerGEM)",
        rating_cont: int = 2,
        loading_cutoff: float = 70.0,
        notes: list[str] | None = None,
        labels: list[str] | None = None,
        changes: Path | None = None,
        atc: dict | None = None,
        footprint: Path | None = None,
    ) -> "Study":
        """Scaffold studies/<name>/, copy sub/con/mon, write study.json + memo.

        The first case is the baseline; the rest are post cases. Labels default
        to the last underscore-token of each file stem (30HS_22-G7_pre → pre).
        """
        root = Path(studies_root) / name
        for d in NUMBERED_FOLDERS:
            (root / d).mkdir(parents=True, exist_ok=True)
            (root / d / ".gitkeep").touch()

        cases: list[CaseSpec] = []
        used: set[str] = set()
        for i, cp in enumerate(case_paths):
            cp = Path(cp)
            if not cp.exists() or cp.stat().st_size < 10_000:
                raise FileNotFoundError(
                    f"Case missing or implausibly small: {cp}")
            fmt, ver = detect_format_version(cp)
            lbl = (labels[i] if labels and i < len(labels)
                   else (cp.stem.split("_")[-1] if "_" in cp.stem else cp.stem))
            base = lbl
            k = 2
            while lbl in used:      # ensure unique labels
                lbl = f"{base}{k}"
                k += 1
            used.add(lbl)
            cases.append(CaseSpec(path=str(cp), label=lbl,
                                  role="baseline" if i == 0 else "post",
                                  fmt=fmt, version=ver))

        # Copy the small study-definition files into 03_TARA_Inputs (tracked).
        def _copy(p: Path | None) -> str | None:
            if p is None:
                return None
            p = Path(p)
            if not p.exists() or p.stat().st_size == 0:
                raise FileNotFoundError(f"Study file missing/empty: {p}")
            dest = root / "03_TARA_Inputs" / p.name
            dest.write_bytes(p.read_bytes())
            return str(dest)

        cfg = StudyConfig(
            name=name, created=date.today().isoformat(),
            objective=objective, area=area, software=software,
            cases=cases, sub=_copy(sub), con=_copy(con), mon=_copy(mon),
            rating_cont=rating_cont, loading_cutoff=loading_cutoff,
            changes=_copy(changes), atc=atc, footprint=_copy(footprint),
            notes=notes or [], runs={},
            deliverables={}, gates={},
        )
        study = cls(root, cfg)
        study.save()
        study._write_memo()
        study._write_cases_readme()
        return study

    def _write_cases_readme(self) -> None:
        """02_Base_Cases stays empty on purpose (cases are large, read-only,
        git-ignored) — leave a README saying where the cases actually live."""
        lines = [
            "# Base cases — source locations",
            "",
            "Case files are NOT copied into the study; the pipeline reads them",
            "from their source paths, recorded in `../study.json`. This folder",
            "exists for any study-specific case notes or derived snapshots.",
            "",
            "| Label | Role | Format | Source path |",
            "|---|---|---|---|",
        ]
        for cs in self.ordered():
            lines.append(f"| {cs.label} | {cs.role} | {cs.fmt} v{cs.version} "
                         f"| `{cs.path}` |")
        (self.root / "02_Base_Cases" / "README.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def _write_memo(self) -> None:
        c = self.config
        rating = {1: "Rate 1 (Rate A / normal)",
                  2: "Rate 2 (Rate B / emergency)"}.get(
                      c.rating_cont, f"slot {c.rating_cont}")
        lines = [
            f"# {c.name} — study guidance memo",
            "",
            f"**Created:** {c.created}  ·  **Software:** {c.software}  ·  "
            f"**Area:** {c.area or 'TBD'}",
            "",
            "## Objective", "", c.objective or "_(to be filled in)_", "",
            "## Base cases", "",
            "| Label | Role | Format | Read ver | File |",
            "|---|---|---|---|---|",
        ]
        for cs in self.ordered():
            lines.append(f"| {cs.label} | {cs.role} | {cs.fmt} | {cs.version} "
                         f"| `{Path(cs.path).name}` |")
        lines += [
            "", "## Contingency inputs", "",
            f"- sub: `{Path(c.sub).name if c.sub else '—'}`",
            f"- con: `{Path(c.con).name if c.con else '—'}`",
            f"- mon: `{Path(c.mon).name if c.mon else '—'}`",
            f"- post-contingency rating: **{rating}**  ·  "
            f"loading cutoff: **{c.loading_cutoff:g}%**",
            "",
        ]
        if c.notes:
            lines += ["## Session notes", ""] + [f"- {n}" for n in c.notes] + [""]
        lines += [
            "## Pipeline", "",
            "Run `python scripts/study.py run --name " + c.name + "` on the "
            "Windows machine to execute: base-case solve → base-case comparison "
            "→ N-1 contingency per case → contingency Excel + summary → review. "
            "Run numbers land back in `study.json`.",
            "",
        ]
        (self.root / f"00_{c.name}_Study_Memo.md").write_text(
            "\n".join(lines), encoding="utf-8")
