"""Agentic-flow tests: review gates, the ATC stage, and the gated report.

File-only — the ATC stage is exercised with a stub engine, so no tara.exe.
The gate behaviour is the point: approval is granted by a person, binds to the
runs reviewed, and lapses when those runs change.
"""

import types
from pathlib import Path

import pytest

from tara_automation.run_manager import RunManager
from tara_automation.study import GateError, Study
from tara_automation.study_pipeline import (GATED_STAGES, STAGES,
                                            stage_atc, stage_atcsummary,
                                            stage_report)

EPC_BYTES = b"title\n" + b"x" * 11_000
SUB = "SUBSYSTEM SRC\nBUS 19169\nEND\nSUBSYSTEM SINK\nAREA 30\nEND\n"
MACHINE = types.SimpleNamespace(tara_exe=Path("tara.exe"))


def _study(tmp_path, atc=True) -> Study:
    cases = []
    for n in ("case_pre.epc", "case_pst1.epc"):
        p = tmp_path / n
        p.write_bytes(EPC_BYTES)
        cases.append(p)
    (tmp_path / "a.sub").write_text(SUB)
    (tmp_path / "a.con").write_text("CONTINGENCY x\nEND\n")
    (tmp_path / "a.mon").write_text("MONITOR x\n")
    return Study.init(
        tmp_path / "studies", "Flow_Test", case_paths=cases,
        sub=tmp_path / "a.sub", con=tmp_path / "a.con", mon=tmp_path / "a.mon",
        objective="transfer capability", area="WECC",
        atc={"send": ["SRC"], "recv": "SINK"} if atc else None)


# ------------------------------------------------------------------ gates ----
def test_stage_completion_only_marks_pending(tmp_path):
    s = _study(tmp_path)
    s.config.runs["atc"] = {"pre|SRC": 1}
    s.mark_for_review("atc")
    assert s.gate_status("atc") == "pending"
    with pytest.raises(GateError, match="awaiting your review"):
        s.require_approved("atc")


def test_approval_binds_to_the_runs_reviewed(tmp_path):
    s = _study(tmp_path)
    s.config.runs["atc"] = {"pre|SRC": 1, "pst1|SRC": 2}
    s.mark_for_review("atc")
    s.approve("atc", verdict="v.md", by="engineer")
    s.require_approved("atc")                      # no raise

    # Re-running the stage produces different run numbers: sign-off lapses.
    s.config.runs["atc"] = {"pre|SRC": 7, "pst1|SRC": 8}
    assert s.gate_status("atc") == "stale"
    with pytest.raises(GateError, match="approval has lapsed"):
        s.require_approved("atc")


def test_rerunning_a_stage_clears_an_approval(tmp_path):
    s = _study(tmp_path)
    s.config.runs["atc"] = {"pre|SRC": 1}
    s.approve("atc")
    s.config.runs["atc"] = {"pre|SRC": 9}
    s.mark_for_review("atc")                        # what a stage does on success
    assert s.gate_status("atc") == "pending"


def test_rejection_and_empty_stage(tmp_path):
    s = _study(tmp_path)
    with pytest.raises(GateError, match="no runs to approve"):
        s.approve("atc")
    s.config.runs["atc"] = {"pre|SRC": 1}
    s.reject("atc", note="wrong sink")
    with pytest.raises(GateError, match="wrong sink"):
        s.require_approved("atc")


def test_gates_survive_a_save_load_round_trip(tmp_path):
    s = _study(tmp_path)
    s.config.runs["atc"] = {"pre|SRC": 3}
    s.approve("atc", verdict="v.md", note="looks good")
    s.save()
    back = Study.load(tmp_path / "studies", "Flow_Test")
    assert back.gate_status("atc") == "approved"
    assert back.gate("atc")["verdict"] == "v.md"


# ----------------------------------------------------------------- report ----
def test_report_is_blocked_until_every_gated_stage_is_approved(tmp_path):
    s = _study(tmp_path)
    rm = RunManager(tmp_path / "runs")
    s.config.runs["atc"] = {"pre|SRC": 1}
    s.config.runs["contingency"] = {"pre": 2}
    s.mark_for_review("atc")
    s.mark_for_review("contingency")

    with pytest.raises(GateError) as e:
        stage_report(s, rm)
    assert "atc=pending" in str(e.value) and "contingency=pending" in str(e.value)

    s.approve("atc")
    with pytest.raises(GateError, match="contingency=pending"):
        stage_report(s, rm)

    s.approve("contingency")
    out = stage_report(s, rm)
    assert out.exists()
    import json
    pack = json.loads(out.read_text(encoding="utf-8"))
    assert pack["approved"]["atc"]["status"] == "approved"
    assert pack["runs"]["atc"] == {"pre|SRC": 1}


def test_report_needs_something_to_report(tmp_path):
    s = _study(tmp_path)
    with pytest.raises(RuntimeError, match="nothing to report"):
        stage_report(s, RunManager(tmp_path / "runs"))


def test_default_run_stops_before_the_gated_report():
    from tara_automation.study_pipeline import DEFAULT_STAGES
    assert "report" in STAGES and "report" not in DEFAULT_STAGES
    assert GATED_STAGES == ["contingency", "atc"]


# -------------------------------------------------------------------- atc ----
TRLIM_SUMMARY = {
    "n_contingencies": 6565,
    "paths": {"19169_ED5-230_____": {
        "receiving": "SINK", "max_test_mw": 2000.0, "rows": 57,
        "first_limit_mw": 97.9,
        "first_limit": {"short": "COOLIDGE-ROGSWAPA 230", "contingency": "CONT A",
                        "trlim_mw": 97.9, "dfax": 0.038, "headroom_mw": 3.7},
        "worsened_initial_overloads": [{"short": "TESTTRAK-SNTAROSA 230",
                                        "trlim_mw": -769.2}],
        "relieved_initial_overloads": 3}},
}


class _StubEngine:
    """Stands in for TrLimEngine: writes no comm, runs no tara.exe."""

    def __init__(self, tara_exe=None, ok=True):
        self.ok, self.prepared = ok, []

    def prepare(self, run, case, sub, con, mon, send, recv, fmt="EPC", params=None):
        run.manifest["trlim"] = {"send": send, "recv": recv}
        self.prepared.append((run.number, send))
        return Path("run.bat")

    def execute(self, run):
        return 0

    def check_outputs(self, run):
        if not self.ok:
            return {"ok": False, "problems": ["contingency file not read "
                                              "(0 contingencies)"], "notes": []}
        return {"ok": True, "problems": [], "notes": [],
                "report": {"n_contingencies": 6565, "rows": [], "paths": []}}


@pytest.fixture
def stub_atc(monkeypatch):
    import tara_automation.commfile.trlim as trlim
    monkeypatch.setattr(trlim, "check_inputs", lambda *a, **k: {"SRC": [19169]})
    monkeypatch.setattr(trlim, "summarize_trlim", lambda rep: TRLIM_SUMMARY)
    return trlim


def test_atc_stage_runs_every_case_and_opens_a_gate(tmp_path, stub_atc, monkeypatch):
    monkeypatch.setattr(stub_atc, "TrLimEngine", _StubEngine)
    s = _study(tmp_path)
    rm = RunManager(tmp_path / "runs")
    out = stage_atc(s, rm, machine=MACHINE)
    assert sorted(out) == ["pre|SRC", "pst1|SRC"]
    assert s.gate_status("atc") == "pending"        # nobody has reviewed it yet
    assert rm.load_run(out["pre|SRC"]).manifest["trlim_summary"] == TRLIM_SUMMARY


def test_atc_stage_fails_loudly_on_a_bad_run(tmp_path, stub_atc, monkeypatch):
    monkeypatch.setattr(stub_atc, "TrLimEngine",
                        lambda tara_exe=None: _StubEngine(ok=False))
    s = _study(tmp_path)
    with pytest.raises(RuntimeError, match="0 contingencies"):
        stage_atc(s, RunManager(tmp_path / "runs"), machine=MACHINE)
    assert s.gate_status("atc") == "missing"        # no gate for failed results


def test_atc_stage_skipped_without_an_atc_block(tmp_path):
    s = _study(tmp_path, atc=False)
    assert stage_atc(s, RunManager(tmp_path / "runs"), machine=MACHINE) == {}


def test_atcsummary_table_keeps_the_caveat_column(tmp_path, stub_atc, monkeypatch):
    monkeypatch.setattr(stub_atc, "TrLimEngine", _StubEngine)
    s = _study(tmp_path)
    rm = RunManager(tmp_path / "runs")
    stage_atc(s, rm, machine=MACHINE)
    out = stage_atcsummary(s, rm)
    text = out.read_text(encoding="utf-8")
    assert "| pre | SRC | 19169_ED5-230 | 97.9 | COOLIDGE-ROGSWAPA 230" in text
    assert "| 1 |" in text                          # worsened-overload count
    assert "Pre-existing overloads" in text
    import json
    js = json.loads(Path(s.config.deliverables["atc_summary_json"])
                    .read_text(encoding="utf-8"))
    assert js["rows"][0]["limit_mw"] == 97.9
