"""Tests for run management and comm-file generation (no TARA engine needed)."""

import json
from pathlib import Path

from tara_automation.commfile import CommEngine
from tara_automation.commfile import syntax
from tara_automation.extract import COMM_REPORT_NAMES, COMM_REPORTS
from tara_automation.run_manager import RunManager


def test_sequential_run_numbers(tmp_path):
    rm = RunManager(tmp_path / "runs")
    r1 = rm.create_run(engine="comm", case="a.epc")
    r2 = rm.create_run(engine="pygem", case="b.epc")
    assert (r1.number, r2.number) == (1, 2)
    assert r2.root.name == "2"
    # numbering survives gaps/reload
    r3 = rm.create_run(engine="comm", case="c.epc")
    assert r3.number == 3


def test_manifest_and_run_log(tmp_path):
    rm = RunManager(tmp_path / "runs")
    run = rm.create_run(engine="comm", case="x.epc", label="smoke")
    run.finalize("success", "solved fine")
    m = json.loads((run.root / "manifest.json").read_text())
    assert m["status"] == "success" and m["summary"] == "solved fine"
    log = (tmp_path / "runs" / "RUN_LOG.md").read_text()
    assert "Run 1" in log and "x.epc" in log and "solved fine" in log
    # second run appends under the same day heading (only one heading)
    run2 = rm.create_run(engine="pygem", case="y.epc")
    run2.finalize("failed", "did not converge")
    log = (tmp_path / "runs" / "RUN_LOG.md").read_text()
    assert log.count("## 2") == 1  # one day heading
    assert "Run 2" in log


def test_comm_syntax():
    assert syntax.read_case("c.epc", "EPC", 21) == 'READ EPC 21 "c.epc" 0'
    raw = syntax.read_case("c.raw", "RAW", 34)
    # filename on own line; standalone 0 exits READ menu back to L1
    assert raw == 'READ PSSE 34\n "c.raw"\n0'
    assert "RAWD" not in raw  # RAWD = export function, crashes with no case
    assert syntax.solve_fdec() == "solve fdec 0 0"
    assert syntax.sarea_all() == "lfreview sarea -1 0 0"
    line = syntax.report_save("lfreview", "o.csv", "bus 0 0")
    assert line == 'lfreview %save "o.csv" %csv bus 0 0'
    line = syntax.report_save("lfreview 2term", "d.csv", "termflow 0 0 0")
    assert line == 'lfreview 2term %save "d.csv" %csv termflow 0 0 0'
    master = syntax.master_comm(["a.txt"])
    assert master.startswith('%include "a.txt"') and "%exit" in master


def test_comm_engine_prepare(tmp_path):
    rm = RunManager(tmp_path / "runs")
    run = rm.create_run(engine="comm", case="case.epc")
    eng = CommEngine(tara_exe=Path("C:/fake/tara.exe"))
    bat = eng.prepare(run, Path("case.epc"), fmt="EPC", version=21)
    assert bat.exists()
    body = (run.comm_dir / "basecase.txt").read_text()
    assert 'READ EPC 21 "case.epc" 0' in body
    assert "solve fdec 0 0" in body
    # full UI report set present, incl. special forms
    assert len(COMM_REPORTS) == 31
    for name in COMM_REPORT_NAMES:
        assert f"{name}.csv" in body
    assert "%csv tarea 1 0 0" in body
    assert "lfreview 2term" in body
    assert body.count("lfreview sarea -1 0 0") == 2
    assert "tara.exe" in bat.read_text()
    # manifest records the comm artifacts
    m = json.loads((run.root / "manifest.json").read_text())
    assert m["comm"]["reports"] == COMM_REPORT_NAMES
