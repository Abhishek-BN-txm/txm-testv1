"""Tests: contingency comm generation, option overrides, violation parsing."""

import json
from pathlib import Path

import pytest

from tara_automation.commfile.contingency import ContingencyEngine
from tara_automation.commfile.options import apply_overrides, load_options, render_block
from tara_automation.run_manager import RunManager
from tara_automation.violations import parse_branch_viol, summarize

BRANCH_VIOL_FIXTURE = """X  N-1 AC Monitored Facility Loadings[TARA Ver test]

 Loadflow Case: C:\\case.raw
 Study Data File: C:\\s.sub
 Contingency File: C:\\c.con (total 63 contingencies)
 Monitor File: C:\\m.MON (70.0% loading cutoff)
 Exclude File: not provided
 Solution Options: x
 Contingency Solution Summary: y
 Monitored Facility ,Fr Bus,Fr Name,To Bus,To Name,CKT,kVs,Areas,Areas Name,Zones,Zones Name,Owners,Owners Name,Rate Base (MVA),Rate Cont (MVA), Cont Name ,Cont ID,Base Flow (MVA),Cont Flow (MVA),Final DC %Loading,Final AC %Loading,Severity Rank,Radial Index,Base MW Flow,Cont MW Flow,Base MVAR Flow,Cont MVAR Flow,Fr Base Volt,Fr Cont Volt,To Base Volt,To Cont Volt,
 A-B 1 ,1,A,2,B,1 ,115 ,73,WAPA,793,Z,925 ,O,75.0,79.0,Base Case ,0,136.0, - ,177.46,181.27,0,1.0,133.2, - ,1.0, - ,0.98, - ,0.98, - ,
 C-D 1 ,3,C,4,D,1 ,161 ,65,PACE,652,Z2,7 ,O2,112.0,9999.0,P2-3 EMERY CB342 ,5,90.0,117.6,84.98,104.99,0,0.9,95.5,101.0,-59.0,-50.0,0.97,0.96,0.99,0.98,
 E-F 1 ,5,E,6,F,1 ,230 ,14,APS,141,Z3,2 ,O3,437.8,544.6,P1-2 X LINE ,7,300.0,340.0,80.0,88.0,0,0.4,-290.0,-330.0,91.9,80.0,1.0,1.0,1.0,1.0,
"""

VOLT_VIOL_FIXTURE = """X N-1 Voltage[TARA Ver test]

 Contingency File: C:\\c.con (total 63 contingencies)
 Monitor File: C:\\m.MON (0.50% voltage change cutoff)
  Bus#,Bus Name,Volt,Area,AreaName, Cont Name ,Cont ID,Vmag,
  9,LOWBUS,115,65,PACE, P1-2 X LINE ,7,0.91,
"""


def test_options_load_and_render():
    blocks = load_options()
    names = [b["name"] for b in blocks["analysis_blocks"]]
    assert names == ["lfopt_solve", "lfopt_adj", "lfopt_accont", "opt_cont",
                     "rpt_n1thermal", "rpt_n1voltage"]
    # spot-check values against the proven run-7 comm
    accont = next(b for b in blocks["analysis_blocks"] if b["name"] == "lfopt_accont")
    assert accont["options"]["GenLoclACCont"] == 1
    assert accont["options"]["SWSHadjACCont"] == 0
    txt = render_block(accont)
    assert txt.startswith("lfopt  accont") and txt.endswith("0 0")
    assert "GenLoclACCont" in txt
    dims = blocks["dimension_block"]
    assert dims["options"]["maxBuses"] == 40000 and dims["terminator"] == "0 0 0"


def test_overrides_strict():
    blocks = load_options()
    out = apply_overrides(blocks, {"lfopt_accont": {"lockOscShuntACCont": 1}})
    accont = next(b for b in out["analysis_blocks"] if b["name"] == "lfopt_accont")
    assert accont["options"]["lockOscShuntACCont"] == 1
    # original untouched (deep copy)
    orig = next(b for b in blocks["analysis_blocks"] if b["name"] == "lfopt_accont")
    assert orig["options"]["lockOscShuntACCont"] == 0
    with pytest.raises(KeyError):
        apply_overrides(blocks, {"nope": {"x": 1}})
    with pytest.raises(KeyError):
        apply_overrides(blocks, {"lfopt_accont": {"typo_key": 1}})


def test_contingency_comm_sequence(tmp_path):
    rm = RunManager(tmp_path / "runs")
    run = rm.create_run(engine="comm", case="case.epc")
    for name in ("s.sub", "c.con", "m.mon"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "case.epc").write_text("x", encoding="utf-8")
    eng = ContingencyEngine(tara_exe=Path("C:/fake/tara.exe"))
    bat = eng.prepare(run, tmp_path / "case.epc", tmp_path / "s.sub",
                      tmp_path / "c.con", tmp_path / "m.mon",
                      fmt="EPC", version=21, loading_cutoff=70.0)
    body = (run.comm_dir / "contingency.txt").read_text(encoding="utf-8")
    # order matters: dims before READ; study files then 0 0 then solve;
    # options; select all; accont; saves
    idx = {k: body.index(k) for k in (
        "maxBuses", "READ EPC 21", 'READ SUBSYS', 'CONT "', 'MONIT "',
        "lfopt  solve", "lfopt  accont", "opt  cont", "RPTManager  N1Thermal",
        "cont select all", "cont accont 0", "%csv acvolt", "%csv acbranch 70")}
    order = sorted(idx, key=idx.get)
    assert order == ["maxBuses", "READ EPC 21", 'READ SUBSYS', 'CONT "',
                     'MONIT "', "lfopt  solve", "lfopt  accont", "opt  cont",
                     "RPTManager  N1Thermal", "cont select all",
                     "cont accont 0", "%csv acvolt", "%csv acbranch 70"]
    assert bat.exists()
    m = json.loads((run.root / "manifest.json").read_text())
    assert m["contingency"]["loading_cutoff"] == 70.0


def test_prepare_rejects_empty_inputs(tmp_path):
    rm = RunManager(tmp_path / "runs")
    run = rm.create_run(engine="comm", case="case.epc")
    (tmp_path / "case.epc").write_text("x", encoding="utf-8")
    (tmp_path / "empty.con").write_text("", encoding="utf-8")
    (tmp_path / "s.sub").write_text("x", encoding="utf-8")
    (tmp_path / "m.mon").write_text("x", encoding="utf-8")
    eng = ContingencyEngine(tara_exe=Path("C:/fake/tara.exe"))
    with pytest.raises(FileNotFoundError):
        eng.prepare(run, tmp_path / "case.epc", tmp_path / "s.sub",
                    tmp_path / "empty.con", tmp_path / "m.mon")


def test_violation_parsing_and_summary(tmp_path):
    bp = tmp_path / "BranchViol.csv"
    vp = tmp_path / "VoltViol.csv"
    bp.write_text(BRANCH_VIOL_FIXTURE, encoding="utf-8")
    vp.write_text(VOLT_VIOL_FIXTURE, encoding="utf-8")
    meta, rows = parse_branch_viol(bp)
    assert meta["n_contingencies"] == 63
    assert len(rows) == 3
    assert rows[0]["is_base_case"] and rows[0]["ac_loading"] == 181.27
    s = summarize(bp, vp)
    assert s["overloads_ge_100"] == 2  # 181.27 base + 104.99 post-cont
    assert s["base_case_overloads"] == 1
    assert s["contingency_overloads"] == 1
    assert s["worst_overloads"][0]["ac_loading"] == 181.27
    assert s["volt_records"] == 1
    # 9999 rating convention: flagged, never silent
    assert s["no_emergency_rating_facilities"] == ["C-D 1"]
    flagged = [w for w in s["worst_overloads"] if w["no_emergency_rating"]]
    assert len(flagged) == 1 and flagged[0]["facility"] == "C-D 1"
    from tara_automation.violations import summary_line
    assert "WARNING: 1 facilities with no emergency rating" in summary_line(s)
