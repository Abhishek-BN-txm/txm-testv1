"""Tests for the study orchestrator (study.py + study_pipeline.py).

Everything here is file-only: no tara.exe, no machine.json. Engine stages are
exercised only for their guard/skip logic.
"""

from pathlib import Path

import pytest

from tara_automation.run_manager import RunManager
from tara_automation.study import Study, StudyConfig, detect_format_version
from tara_automation.study_pipeline import (
    ENGINE_STAGES, STAGES, _execute_batch, _runs_exist,
    stage_basecompare, stage_summary,
)

# A fake EPC big enough to pass the small-file guard (10 kB).
EPC_BYTES = (b"title\n" + b"x" * 11_000)


def _mk_case(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(EPC_BYTES)
    return p


def _mk_study(tmp_path: Path, with_cont: bool = True, **kw) -> Study:
    cases = [_mk_case(tmp_path, n)
             for n in ("30HS_22-G7_pre.epc", "30HS_22-G7_pst1.epc",
                       "30HS_22-G7_pst2.epc")]
    sub = con = mon = None
    if with_cont:
        for ext in ("sub", "con", "mon"):
            f = tmp_path / f"AZ.{ext}"
            f.write_text(f"dummy {ext}\n", encoding="utf-8")
        sub, con, mon = tmp_path / "AZ.sub", tmp_path / "AZ.con", tmp_path / "AZ.mon"
    return Study.init(
        tmp_path / "studies", "Test_Study", case_paths=cases,
        sub=sub, con=con, mon=mon,
        objective="test objective", area="WECC", **kw)


# ------------------------------------------------------- format detection ----

def test_detect_epc_forces_keyword_21(tmp_path):
    p = _mk_case(tmp_path, "case.epc")
    assert detect_format_version(p) == ("EPC", 21)


def test_detect_raw_reads_rev_from_header(tmp_path):
    p = tmp_path / "case.raw"
    p.write_text("0, 100.0, 33, 0, 1, 60.0\nbus data\n", encoding="utf-8")
    assert detect_format_version(p) == ("RAW", 33)


def test_detect_raw_bad_header_defaults_34(tmp_path):
    p = tmp_path / "case.raw"
    p.write_text("garbage header\n", encoding="utf-8")
    assert detect_format_version(p) == ("RAW", 34)


# ------------------------------------------------------------------- init ----

def test_init_scaffolds_and_persists(tmp_path):
    study = _mk_study(tmp_path)
    root = study.root
    for d in ("01_References", "03_TARA_Inputs", "05_Analysis", "06_Reports"):
        assert (root / d).is_dir()
    assert (root / "study.json").exists()
    assert (root / "00_Test_Study_Study_Memo.md").exists()
    readme = (root / "02_Base_Cases" / "README.md").read_text(encoding="utf-8")
    assert "NOT copied" in readme
    assert "30HS_22-G7_pre.epc" in readme

    c = study.config
    assert [cs.role for cs in study.ordered()] == ["baseline", "post", "post"]
    assert [cs.label for cs in study.ordered()] == ["pre", "pst1", "pst2"]
    assert all(cs.fmt == "EPC" and cs.version == 21 for cs in c.cases)
    # sub/con/mon copied into the study, not referenced at source
    assert Path(c.sub).parent == root / "03_TARA_Inputs"
    assert Path(c.sub).exists() and Path(c.con).exists() and Path(c.mon).exists()


def test_init_roundtrip_load(tmp_path):
    _mk_study(tmp_path, notes=["pst1=project only"])
    study = Study.load(tmp_path / "studies", "Test_Study")
    c = study.config
    assert c.objective == "test objective"
    assert c.notes == ["pst1=project only"]
    assert c.rating_cont == 2 and c.loading_cutoff == 70.0
    assert isinstance(c, StudyConfig)
    assert study.baseline().label == "pre"


def test_init_rejects_small_case(tmp_path):
    tiny = tmp_path / "empty.epc"
    tiny.write_bytes(b"")  # the 0-byte trap (run 6 RAWD precedent)
    with pytest.raises(FileNotFoundError):
        Study.init(tmp_path / "studies", "Bad", case_paths=[tiny])


def test_init_rejects_missing_study_file(tmp_path):
    case = _mk_case(tmp_path, "ok.epc")
    with pytest.raises(FileNotFoundError):
        Study.init(tmp_path / "studies", "Bad", case_paths=[case],
                   sub=tmp_path / "missing.sub")


def test_init_dedupes_labels(tmp_path):
    cases = []
    for d in ("a", "b"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
        cases.append(_mk_case(tmp_path / d, "same_pre.epc"))
    study = Study.init(tmp_path / "studies", "Dup", case_paths=cases)
    labels = [cs.label for cs in study.ordered()]
    assert len(set(labels)) == 2


def test_load_missing_study_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Study.load(tmp_path / "studies", "Nope")


# --------------------------------------------------------------- pipeline ----

def test_stage_lists_consistent():
    assert set(STAGES) >= ENGINE_STAGES
    assert STAGES.index("basecase") < STAGES.index("basecompare")
    assert STAGES.index("contingency") < STAGES.index("contcompare")
    assert STAGES.index("contcompare") < STAGES.index("summary")


def test_runs_exist(tmp_path):
    rm = RunManager(tmp_path / "runs")
    run = rm.create_run(engine="comm", case="x.epc", label="t")
    assert _runs_exist(rm, [run.number])
    assert not _runs_exist(rm, [run.number, 999])


def test_basecompare_requires_basecase(tmp_path):
    study = _mk_study(tmp_path)
    rm = RunManager(tmp_path / "runs")
    with pytest.raises(RuntimeError, match="basecase"):
        stage_basecompare(study, rm)


def test_summary_requires_contingency(tmp_path):
    study = _mk_study(tmp_path)
    rm = RunManager(tmp_path / "runs")
    with pytest.raises(RuntimeError, match="contingency"):
        stage_summary(study, rm)


def test_contingency_requires_study_files(tmp_path):
    from tara_automation.study_pipeline import stage_contingency
    study = _mk_study(tmp_path, with_cont=False)
    rm = RunManager(tmp_path / "runs")
    with pytest.raises(RuntimeError, match="sub/con/mon"):
        stage_contingency(study, rm, machine=None)


def test_run_all_file_only_stages_need_no_machine(tmp_path, monkeypatch):
    """File-only stages must not require machine.json (no tara.exe)."""
    import tara_automation.study_pipeline as sp
    study = _mk_study(tmp_path)

    def boom():
        raise AssertionError("load_machine called for file-only stages")
    monkeypatch.setattr(sp, "load_machine", boom)
    # summary without contingency runs → clean RuntimeError, NOT machine load
    with pytest.raises(RuntimeError, match="contingency"):
        sp.run_all(study, stages=["summary"])


class _FakeEngine:
    """Records execution; sleeps briefly so parallel overlap is observable."""

    def __init__(self, rc_by_number=None):
        self.rc_by_number = rc_by_number or {}
        self.max_concurrent = 0
        self._active = 0
        import threading
        self._lock = threading.Lock()

    def execute(self, run):
        import time
        with self._lock:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        time.sleep(0.05)
        with self._lock:
            self._active -= 1
        return self.rc_by_number.get(run.number, 0)


def _prepared(tmp_path, n=3):
    rm = RunManager(tmp_path / "runs")
    return [(None, rm.create_run(engine="comm", case=f"c{i}.epc"))
            for i in range(n)]


def test_execute_batch_sequential(tmp_path):
    prepared = _prepared(tmp_path)
    eng = _FakeEngine()
    rcs = _execute_batch(eng, prepared, jobs=1)
    assert rcs == {run.number: 0 for _, run in prepared}
    assert eng.max_concurrent == 1


def test_execute_batch_parallel_overlaps(tmp_path):
    prepared = _prepared(tmp_path)
    eng = _FakeEngine()
    rcs = _execute_batch(eng, prepared, jobs=3)
    assert set(rcs) == {run.number for _, run in prepared}
    assert eng.max_concurrent > 1          # actually ran concurrently


def test_execute_batch_collects_failures(tmp_path):
    prepared = _prepared(tmp_path)
    bad = prepared[1][1].number
    eng = _FakeEngine(rc_by_number={bad: 7})
    rcs = _execute_batch(eng, prepared, jobs=2)
    assert rcs[bad] == 7
    assert [rcs[r.number] for _, r in prepared].count(0) == 2


def test_run_numbers_assigned_before_execution(tmp_path):
    """Prepare-all-first means numbering is sequential and race-free."""
    prepared = _prepared(tmp_path, n=4)
    nums = [run.number for _, run in prepared]
    assert nums == sorted(nums) and len(set(nums)) == 4


def test_memo_contents(tmp_path):
    study = _mk_study(tmp_path, notes=["a note"])
    memo = (study.root / "00_Test_Study_Study_Memo.md").read_text(encoding="utf-8")
    assert "Rate 2 (Rate B / emergency)" in memo
    assert "| pre | baseline | EPC | 21" in memo
    assert "a note" in memo
    assert "scripts/study.py run --name Test_Study" in memo


# --------------------------------------------------- casechange stage --------
class _FakeCaseChangeEngine:
    """Stands in for CaseChangeEngine: records calls, writes a plausible case."""

    def __init__(self, ok=True, size=50_000):
        self.ok, self.size, self.prepared = ok, size, []

    def prepare(self, run, case, inch, out_case, fmt="EPC", version=21):
        run.manifest["casechange"] = {"out_case": str(out_case), "inch": str(inch)}
        run.save_manifest()
        self.prepared.append((run.number, Path(inch).name, Path(out_case).name))
        return run.comm_dir / "run.bat"

    def execute(self, run):
        oc = Path(run.manifest["casechange"]["out_case"])
        oc.parent.mkdir(parents=True, exist_ok=True)
        oc.write_bytes(b"x" * self.size)
        return 0

    def check_outputs(self, run):
        return {"ok": self.ok, "case_written": True, "case_bytes": self.size,
                "case_plausible": self.size >= 10_000,
                "inch_errors": [] if self.ok else [{"id": 1652, "count": 1,
                                                    "message": "m"}],
                "warnings_parsed": 0}

    @staticmethod
    def summary_line(checks):
        return "ok" if checks["ok"] else "INCH errors"


def _study_with_changes(tmp_path, case, scenarios=None, **sheets):
    from tara_automation.casechange import write_template
    from tara_automation.casechange.workbook import append_rows
    from tara_automation.study import Study

    wb = tmp_path / "changes.xlsx"
    write_template(wb)
    append_rows(wb, "Scenarios", scenarios or [
        {"ScenarioID": "S1", "Name": "add gen", "Description": "one generator",
         "OutputFormat": "EPC", "OutputVersion": 21, "Solve": "Y",
         "NewBusStart": 990001, "Enabled": "Y"}])
    for name, rows in sheets.items():
        append_rows(wb, name, rows)
    return Study.init(tmp_path / "studies", "S", [case], changes=wb), wb


def _patch_engine(monkeypatch, engine):
    import tara_automation.commfile.casechange as cc
    monkeypatch.setattr(cc, "CaseChangeEngine", lambda tara_exe: engine)


def test_casechange_stage_registers_the_modified_case(tmp_path, monkeypatch):
    from tara_automation.run_manager import RunManager
    from tara_automation.study_pipeline import stage_casechange

    case = tmp_path / "base.epc"
    case.write_bytes(b"x" * 50_000)
    study, wb = _study_with_changes(
        tmp_path, case,
        New_Generators=[{"Scenarios": "ALL", "BusNum": 14005, "GenID": "1",
                         "GenMW": 300, "GenMWMax": 300}])
    eng = _FakeCaseChangeEngine()
    _patch_engine(monkeypatch, eng)

    rm = RunManager(tmp_path / "runs")
    out = stage_casechange(study, rm, machine=type("M", (), {"tara_exe": "t"})())

    assert list(out) == ["S1"]
    assert study.has_case("S1")
    spec = next(c for c in study.config.cases if c.label == "S1")
    assert spec.role == "post" and spec.fmt == "EPC"
    assert Path(spec.path).exists()
    # the INCH really was written next to the study inputs
    assert (study.inputs_dir / "inch" / "S1.inch").exists()
    # and it survives a reload — study.json is the memory
    reloaded = type(study).load(tmp_path / "studies", "S")
    assert reloaded.has_case("S1")
    assert reloaded.config.runs["casechange"] == out


def test_casechange_stage_is_idempotent(tmp_path, monkeypatch):
    from tara_automation.run_manager import RunManager
    from tara_automation.study_pipeline import stage_casechange

    case = tmp_path / "base.epc"
    case.write_bytes(b"x" * 50_000)
    study, _ = _study_with_changes(
        tmp_path, case,
        New_Generators=[{"Scenarios": "ALL", "BusNum": 14005, "GenID": "1",
                         "GenMW": 300, "GenMWMax": 300}])
    eng = _FakeCaseChangeEngine()
    _patch_engine(monkeypatch, eng)
    rm = RunManager(tmp_path / "runs")
    m = type("M", (), {"tara_exe": "t"})()

    first = stage_casechange(study, rm, m)
    n_cases = len(study.config.cases)
    second = stage_casechange(study, rm, m)
    assert second == first                      # skipped, no new runs
    assert len(study.config.cases) == n_cases   # no duplicate case rows
    assert len(eng.prepared) == 1


def test_casechange_stage_rejects_a_case_with_inch_errors(tmp_path, monkeypatch):
    """TARA exits 0 and writes a full-size case even when records were
    dropped, so a failed scenario must not reach the pipeline as a case."""
    import pytest
    from tara_automation.run_manager import RunManager
    from tara_automation.study_pipeline import stage_casechange

    case = tmp_path / "base.epc"
    case.write_bytes(b"x" * 50_000)
    study, _ = _study_with_changes(
        tmp_path, case,
        New_Generators=[{"Scenarios": "ALL", "BusNum": 14005, "GenID": "1",
                         "GenMW": 300, "GenMWMax": 300}])
    _patch_engine(monkeypatch, _FakeCaseChangeEngine(ok=False))
    rm = RunManager(tmp_path / "runs")
    with pytest.raises(RuntimeError, match="case-change run"):
        stage_casechange(study, rm, machine=type("M", (), {"tara_exe": "t"})())
    assert not study.has_case("S1")


def test_casechange_stage_noops_without_a_workbook(tmp_path, monkeypatch):
    from tara_automation.run_manager import RunManager
    from tara_automation.study import Study
    from tara_automation.study_pipeline import stage_casechange

    case = tmp_path / "base.epc"
    case.write_bytes(b"x" * 50_000)
    study = Study.init(tmp_path / "studies", "S", [case])
    rm = RunManager(tmp_path / "runs")
    assert stage_casechange(study, rm, machine=None) == {}


def test_casechange_runs_before_basecase():
    from tara_automation.study_pipeline import ENGINE_STAGES, STAGES
    assert STAGES.index("casechange") < STAGES.index("basecase")
    assert "casechange" in ENGINE_STAGES
