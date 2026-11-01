"""Tests for case-vs-case comparison using synthetic comm-format CSVs."""

from pathlib import Path

from tara_automation.compare import compare_cases

PREAMBLE = "Report . Client-Test\n   Created - now\n"

CASE_SUM = PREAMBLE + " ConvStatus, Solved, \n Iterations, {it} \n Buses, {nb} \n"
AREA_SUM = (PREAMBLE + "hdr\nArea,Name,Buses,NTies,TotGen,x,x,LoadAct,x,x,x,x,shunt,TotLosses,\n"
            "{rows}")
BUS = (PREAMBLE + "hdr\n{rows}")


def _write_case(d: Path, gen=100.0, v2=1.02, branch_mw=50.0, branch_x=0.01,
                rate_a=100.0, gen_p=100.0, load_p=90.0, extra_branch=False):
    d.mkdir(parents=True, exist_ok=True)
    (d / "CaseSum.csv").write_text(CASE_SUM.format(it=10, nb=2), encoding="utf-8")
    (d / "AreaSum.csv").write_text(
        AREA_SUM.format(rows=f"  1, A1, 2, 0, {gen}, 0,0, {load_p}, 0,0,0,0, 0.0, 2.0,\n"),
        encoding="utf-8")
    (d / "BusData.csv").write_text(BUS.format(rows=(
        "  1,BUS1        ,345,  1, 10,Load,1,  1.00000,   345.0, -10.0, 0,0,0,0,0,1,0,0,0,\n"
        f"  2,BUS2        ,345,  1, 10,Load,1,  {v2:.5f},   345.0, -12.0, 0,0,0,0,0,1,0,0,0,\n")),
        encoding="utf-8")
    branches = (f"  1,BUS1        ,345,  1, 10,  2,BUS2        ,345,  1, 10, 1, 0, 1, 1,"
                f" {branch_mw}, 10.0, 51.0, 0.5, 1.0, 0.001, {branch_x}, 0.02, 0, 0, 0,0,0,0,"
                f" {rate_a}, 110.0, 120.0, 0.0, 10.0,1, 100.0,0,0,0,0,0,0, 0,\n")
    if extra_branch:
        branches += ("  2,BUS2        ,345,  1, 10,  3,BUS3        ,345,  1, 10, 1, 0, 1, 1,"
                     " 5.0, 1.0, 5.1, 0.1, 0.2, 0.002, 0.02, 0.01, 0, 0, 0,0,0,0,"
                     " 50.0, 55.0, 60.0, 0.0, 5.0,1, 100.0,0,0,0,0,0,0, 0,\n")
    (d / "BranchData.csv").write_text(BUS.format(rows=branches), encoding="utf-8")
    (d / "GenData.csv").write_text(BUS.format(rows=(
        f"  1,BUS1        ,345,  1, 10,Genr, 1,  1,{gen_p}, {gen_p},20.0,0.0,150.0,"
        "150.0,150.0,-50.0,50.0,100,\n")), encoding="utf-8")
    (d / "LoadData.csv").write_text(BUS.format(rows=(
        f"  2,BUS2        ,345,  1, 10, 1,  1, 1, 1.0, 1, {load_p}, {load_p}, 10.0,"
        " 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0,1,A1,10,C,\n")), encoding="utf-8")


def test_self_compare_clean(tmp_path):
    a = tmp_path / "a"
    _write_case(a)
    cmp = compare_cases(a, a)
    md = cmp.to_markdown()
    assert "0 buses with |dV|" in md
    assert "only in" not in md
    assert "RateA changes" not in md


def test_differences_detected(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _write_case(a)
    _write_case(b, v2=1.05, branch_mw=80.0, branch_x=0.02, rate_a=120.0,
                gen_p=130.0, load_p=120.0, extra_branch=True)
    md = compare_cases(a, b, "base", "change").to_markdown()
    assert "1 buses with |dV| > 0.002" in md          # voltage mover
    assert "1 branches only in B" in md               # topology diff
    assert "impedance/charging changes" in md         # x changed
    assert "RateA changes" in md                      # rate A changed
    assert "top flow movers" in md and "+30.0" in md  # flow delta
    assert "net redispatch" in md and "+30.0" in md   # gen dispatch
    assert "net load change" in md                    # load delta
