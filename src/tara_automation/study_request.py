"""Create a study from a *study request*: the one path both the Footprint
Builder's study desk (scripts/study_desk.py) and the command line
(`study.py create --request`) use.

A request is JSON:

    {"kind": "tara-study-request", "version": 1,
     "study": {"name", "objective", "area", "software", "rating_cont",
               "loading_cutoff", "notes": [...]},
     "cases": [{"path": "...\\pre.epc", "label": "pre"}, ...],   # first = baseline
     "base_files": {"sub": "...", "con": "...", "mon": "..."},   # optional, any subset
     "footprint_yaml": "...",                                     # optional
     "atc": {"send": [...], "recv": "..."},                       # optional
     "carry": {"study_case": "C:\\...", "donor_case": "C:\\...",  # optional (task 27)
               "elements": ["branch:158922:15071:2:1", ...]}}

Rules:
- objective and area are required, as for every study (docs/tara_engine_notes.md).
- An existing study folder is never overwritten.
- Base cases are copied into 03_TARA_Inputs/cases/ (git-ignored) and the
  study runs on the copies; base files are copied into 03_TARA_Inputs/base/.
  Originals are only read, copies are never modified. The
  footprint's output is appended to them by the `footprint` stage.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from tara_automation.study import Study

KIND = "tara-study-request"
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,31}$")
CASE_EXT = {".epc", ".raw", ".sav"}
MIN_CASE_BYTES = 10_000


class StudyRequestError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def validate(req: dict, studies_root: Path) -> dict:
    """Check a request and return it normalised. Raises StudyRequestError with
    every problem found, so the page can show them all at once."""
    P: list[str] = []
    if not isinstance(req, dict) or req.get("kind") != KIND:
        raise StudyRequestError([f"not a study request (expected kind '{KIND}')"])
    st = req.get("study") or {}
    name = str(st.get("name", "")).strip()
    if not NAME_RE.match(name):
        P.append("study name: letters, digits, _ or -, starting with a letter (max 64)")
    elif (Path(studies_root) / name).exists():
        P.append(f"study '{name}' already exists; choose another name (an existing "
                 f"study is never overwritten)")
    objective = str(st.get("objective", "")).strip()
    area = str(st.get("area", "")).strip()
    if not objective:
        P.append("objective is required: what question does the study answer?")
    if not area:
        P.append("area is required: the system / region studied")
    try:
        rating = int(st.get("rating_cont", 2))
        if rating not in (1, 2):
            raise ValueError
    except (TypeError, ValueError):
        P.append("rating_cont must be 1 (Rate A) or 2 (Rate B)")
        rating = 2
    try:
        cutoff = float(st.get("loading_cutoff", 70.0))
        if not 0 < cutoff <= 200:
            raise ValueError
    except (TypeError, ValueError):
        P.append("loading_cutoff must be a percentage between 0 and 200")
        cutoff = 70.0

    cases = []
    labels: set[str] = set()
    for i, c in enumerate(req.get("cases") or []):
        path = Path(str((c or {}).get("path", "")).strip().strip('"'))
        label = str((c or {}).get("label", "") or "").strip()
        where = f"case {i + 1}"
        if not str(path) or str(path) == ".":
            P.append(f"{where}: no path")
            continue
        if not path.is_absolute():
            P.append(f"{where}: give the full path ({path})")
        elif not path.exists():
            P.append(f"{where}: not found: {path}")
        elif path.stat().st_size < MIN_CASE_BYTES:
            P.append(f"{where}: {path.name} is implausibly small ({path.stat().st_size} "
                     f"bytes); a 0-byte case usually means a failed export")
        elif path.suffix.lower() not in CASE_EXT:
            P.append(f"{where}: {path.name} is not a power-flow case "
                     f"({', '.join(sorted(CASE_EXT))})")
        if label:
            if not LABEL_RE.match(label):
                P.append(f"{where}: label '{label}' must be letters, digits, _ or -")
            elif label in labels:
                P.append(f"{where}: label '{label}' is used twice")
            labels.add(label)
        cases.append({"path": str(path), "label": label or None})
    if not cases:
        P.append("select at least one base case (the first one is the baseline)")

    base = {}
    for key, val in (req.get("base_files") or {}).items():
        if key not in ("sub", "con", "mon"):
            P.append(f"base_files: unknown key '{key}'")
            continue
        if not val:
            continue
        f = Path(str(val).strip().strip('"'))
        if not f.is_absolute() or not f.exists() or f.stat().st_size == 0:
            P.append(f"base .{key}: missing, empty or not a full path: {f}")
        else:
            base[key] = str(f)

    fp_text = req.get("footprint_yaml")
    fp = None
    if fp_text:
        try:
            fp = yaml.safe_load(fp_text)
        except yaml.YAMLError as e:
            P.append(f"footprint: not valid YAML ({str(e).splitlines()[0]})")
        if fp is not None and (not isinstance(fp, dict) or "study_area" not in fp):
            P.append("footprint: no study_area")
            fp = None
    if not fp and not base.get("sub"):
        P.append("give a footprint (study area) or a base .sub, or both")

    # Projects flagged on the page to carry from a donor case into a study
    # case (task 27); task 28 turns them into change files.
    carry = req.get("carry")
    if carry:
        case_paths = {str(Path(c["path"]).resolve()).lower() for c in cases}
        if not isinstance(carry, dict) or not isinstance(carry.get("elements"), list)                 or not carry["elements"]:
            P.append("carry: no elements flagged")
            carry = None
        else:
            for k in ("study_case", "donor_case"):
                f = Path(str(carry.get(k) or ""))
                if not f.is_absolute() or not f.is_file():
                    P.append(f"carry: {k} not found: {f}")
            if str(Path(str(carry.get("study_case") or "")).resolve()).lower() not in case_paths:
                P.append("carry: the case receiving the projects must be one of the study's cases")
            bad = [e for e in carry["elements"] if not isinstance(e, str) or ":" not in e]
            if bad:
                P.append(f"carry: {len(bad)} element ids are not valid")

    atc = req.get("atc")
    if atc and not (isinstance(atc, dict) and atc.get("send") and atc.get("recv")):
        P.append("atc: needs both send (list) and recv")
    if P:
        raise StudyRequestError(P)
    return {"name": name, "objective": objective, "area": area,
            "software": str(st.get("software") or "TARA (PowerGEM)"),
            "rating_cont": rating, "loading_cutoff": cutoff,
            "notes": [str(n) for n in (st.get("notes") or []) if str(n).strip()],
            "cases": cases, "base": base, "footprint": fp, "atc": atc or None,
            "carry": carry or None}


def _write_cases_readme(study: Study, sources: dict[str, str]) -> None:
    """02_Base_Cases/README.md: where each case copy came from."""
    lines = [
        "# Base cases",
        "",
        "The study's cases are copies in `03_TARA_Inputs/cases/`, made when the",
        "study was created. Every stage runs on these copies; the originals were",
        "only read. Case files are git-ignored (large).",
        "",
        "| Label | Role | Format | Copy | Copied from |",
        "|---|---|---|---|---|",
    ]
    for cs in study.ordered():
        lines.append(f"| {cs.label} | {cs.role} | {cs.fmt} v{cs.version} "
                     f"| `{Path(cs.path).name}` | `{sources.get(cs.path, '?')}` |")
    (study.root / "02_Base_Cases" / "README.md").write_text("\n".join(lines) + "\n",
                                                            encoding="utf-8")


def _label(case: dict) -> str:
    """The given label, else Study.init's default (30HS_22-G7_pre -> pre)."""
    if case["label"]:
        return case["label"]
    stem = Path(case["path"]).stem
    return stem.split("_")[-1] if "_" in stem else stem


def create(req: dict, studies_root: Path) -> dict:
    """Validate, then build studies/<name>/. Returns what was created."""
    v = validate(req, studies_root)
    study = Study.init(
        studies_root, v["name"], case_paths=[Path(c["path"]) for c in v["cases"]],
        objective=v["objective"], area=v["area"], software=v["software"],
        rating_cont=v["rating_cont"], loading_cutoff=v["loading_cutoff"],
        notes=v["notes"], labels=[_label(cs) for cs in v["cases"]],
        atc=v["atc"])
    c = study.config
    inputs = study.inputs_dir
    created = []

    # The base cases: copied into the study (03_TARA_Inputs/cases), so the
    # study holds everything it runs on. The originals are only read; every
    # stage from here on uses the copies. Same-name cases get a suffix.
    sources = {}
    (inputs / "cases").mkdir(parents=True, exist_ok=True)
    for cs in c.cases:
        src = Path(cs.path)
        dest = inputs / "cases" / src.name
        k = 2
        while dest.exists():
            dest = inputs / "cases" / f"{src.stem}_{k}{src.suffix}"
            k += 1
        shutil.copy2(src, dest)            # keeps the original's timestamp
        if dest.stat().st_size != src.stat().st_size:
            raise StudyRequestError([f"copying {src.name} into the study failed "
                                     f"(size differs); nothing was changed at the source"])
        sources[str(dest)] = str(src)
        cs.path = str(dest)
        created.append(str(dest))
    _write_cases_readme(study, sources)

    # The user's own sub/con/mon: copied, never edited. They are the study's
    # inputs until the footprint stage appends to them.
    base_rel = {}
    if v["base"]:
        (inputs / "base").mkdir(parents=True, exist_ok=True)
        for key, src in v["base"].items():
            dest = inputs / "base" / Path(src).name
            shutil.copy2(src, dest)
            base_rel[key] = f"base/{dest.name}"
            setattr(c, key, str(dest))
            created.append(str(dest))

    if v["footprint"]:
        fp = dict(v["footprint"])
        if base_rel:
            fp["base"] = base_rel           # point at the copies, not the sources
        else:
            fp.pop("base", None)
        dest = inputs / "footprint.yaml"
        header = (f"# Footprint for study {v['name']}, created "
                  f"{datetime.now():%Y-%m-%d %H:%M} from a study request.\n"
                  f"# Compiled by the `footprint` stage after `basecase`.\n")
        dest.write_text(header + yaml.safe_dump(fp, sort_keys=False, width=100,
                                                default_flow_style=None),
                        encoding="utf-8")
        c.footprint = str(dest)
        created.append(str(dest))

    if v["carry"]:
        # point at the study's own copies where the cases were copied in
        copy_of = {str(Path(src).resolve()).lower(): dst for dst, src in sources.items()}
        cv = v["carry"]
        carry = {
            "kind": "tara-carry-projects", "version": 1,
            "study_case": copy_of.get(str(Path(cv["study_case"]).resolve()).lower(), cv["study_case"]),
            "donor_case": copy_of.get(str(Path(cv["donor_case"]).resolve()).lower(), cv["donor_case"]),
            "elements": sorted(set(cv["elements"])),
            "groups": cv.get("groups") or {},
            "flagged_at": datetime.now().isoformat(timespec="seconds"),
        }
        dest = inputs / "changes" / "carry_projects.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(carry, indent=2), encoding="utf-8")
        created.append(str(dest))

    # Keep the request itself with the study, as provenance.
    ref = study.root / "01_References" / "study_request.json"
    ref.write_text(json.dumps({**req, "created_at": datetime.now().isoformat(
        timespec="seconds")}, indent=2), encoding="utf-8")
    created.append(str(ref))
    study.save()
    study._write_memo()
    stages = "basecase footprint contingency" if c.footprint else "basecase contingency"
    return {
        "ok": True, "name": v["name"], "path": str(study.root),
        "cases": [f"{cs.label} ({cs.role}, {cs.fmt} v{cs.version})" for cs in study.ordered()],
        "files": created,
        "next": [f"python scripts/study.py status --name {v['name']}",
                 f"python scripts/study.py run --name {v['name']} --stage {stages}"],
    }
