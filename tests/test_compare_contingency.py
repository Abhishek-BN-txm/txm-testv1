"""Smoke tests for the N-1 contingency comparison + xlsx report."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tara_automation import compare_contingency as cc
from tara_automation.violations import parse_branch_viol

BRANCH_HEADER = (
    " Monitored Facility ,Fr Bus,Fr Name,To Bus,To Name,CKT,Section,kVs,Areas,"
    "Areas Name,Zones,Zones Name,Owners,Owners Name,Rate Base (MVA),"
    "Rate Cont (MVA), Cont Name ,Cont ID,Base Flow (MVA),Cont Flow (MVA),"
    "Final DC %Loading,Final AC %Loading,Severity Rank,Radial Index,Base MW Flow,"
    "Cont MW Flow,Base MVAR Flow,Cont MVAR Flow,Fr Base Volt,Fr Cont Volt,"
    "To Base Volt,To Cont Volt,"
)


def _branch_row(fac, owner, cont, ac, rate_cont="100.0"):
    """Build a data row; owner may contain a comma to exercise the shift fix."""
    return (f"{fac},1,FR,2,TO,1,1,230,19,AREA,191,ZONE,80,{owner},90.0,"
            f"{rate_cont},{cont},1,80,90,{ac},{ac},1,1,80,90,10,10,"
            f"1.0,1.0,1.0,1.0,")


VOLT_HEADER = (
    "Bus #,Bus Name,Base kV,Area,Area Name,Zone,Zone Name,Owner,Owner Name,"
    "Bus Type,Pgen,Pload,G Shunt,B Shunt,Min Volt Limit,Max Volt Limit, Cont Name ,"
)


def _write_case(tmp, num, rows, buses, override=None):
    rd = tmp / str(num) / "results"
    rd.mkdir(parents=True)
    branch = [" TARA N-1", "", " Contingency File: x.con (total 3 contingencies)",
              " Monitor: m.mon (70.0% loading cutoff)", BRANCH_HEADER] + rows
    (rd / "BranchViol.csv").write_text("\n".join(branch), encoding="utf-8")
    volt = [" TARA volt", VOLT_HEADER] + [
        f"{b},NAME,230,19,AREA,1,Z,80,OWN,Load,0,0,0,0,0.9,1.1,cont," for b in buses]
    (rd / "VoltViol.csv").write_text("\n".join(volt), encoding="utf-8")
    manifest = {"case": f"C:/x/case_{num}.epc", "options":
                {"analysis": "contingency"},
                "contingency": {"options_overrides": override or {}}}
    run = SimpleNamespace(number=num, results_dir=rd, manifest=manifest)
    return run


def test_comma_in_owner_name_does_not_shift_columns(tmp_path):
    """The classic 'Gila River Power, LP' shift: AC% must stay put."""
    row = _branch_row("GILARIVR 500", "Gila River Power, LP", "CONTX", "82.0")
    rd = tmp_path / "9" / "results"
    rd.mkdir(parents=True)
    (rd / "BranchViol.csv").write_text(
        "\n".join([" h", "", " Contingency File: x (total 1 contingencies)",
                   BRANCH_HEADER, row]), encoding="utf-8")
    _, rows = parse_branch_viol(rd / "BranchViol.csv")
    assert rows[0]["ac_loading"] == 82.0            # not a shifted column
    assert rows[0]["Owners Name"].startswith("Gila River Power")


def test_compare_classifies_new_and_rating_detect(tmp_path):
    pre = _write_case(tmp_path, 1, [
        _branch_row("LINE_A 230", "Owner A", "C1", "95.0"),   # under in base
        _branch_row("LINE_B 230", "Owner B", "C2", "150.0"),  # overloaded base
    ], buses=["100"])
    post = _write_case(tmp_path, 2, [
        _branch_row("LINE_A 230", "Owner A", "C1", "120.0"),  # NEW overload
        _branch_row("LINE_B 230", "Owner B", "C2", "150.0"),
    ], buses=["100", "101"], override={"opt_cont": {"monBranRatingCont": 1}})

    cmp = cc.compare([pre, post], labels=["pre", "post"])
    assert cmp.cases[1].rating.startswith("Rate 1")           # override detected
    new = [r for r in cmp.overload_rows if "NEW in post" in r["status"]]
    assert any("LINE_A" in r["facility"] for r in new)
    assert cmp.cases[0].n_overloads == 1 and cmp.cases[1].n_overloads == 2


def test_nconv_is_captured(tmp_path):
    pre = _write_case(tmp_path, 1, [
        _branch_row("LINE_A 230", "Own", "CC", "NConv"),
    ], buses=[])
    cmp = cc.compare([pre, pre], labels=["a", "b"])
    assert sum(cmp.cases[0].nconv.values()) == 1
    assert cmp.cases[0].n_records == 0                        # NConv not counted


def test_xlsx_written(tmp_path):
    pytest.importorskip("openpyxl")
    from tara_automation.xlsx_reports import contingency_workbook
    pre = _write_case(tmp_path, 1, [_branch_row("L 230", "O", "C", "150.0")],
                      buses=["1"])
    post = _write_case(tmp_path, 2, [_branch_row("L 230", "O", "C", "160.0")],
                       buses=["1"])
    cmp = cc.compare([pre, post], labels=["pre", "post"])
    out = contingency_workbook(cmp, tmp_path / "out.xlsx")
    assert out.exists() and out.stat().st_size > 0
