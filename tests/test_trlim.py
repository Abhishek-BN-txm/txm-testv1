"""TrLim (ATC) recipe tests: deck fidelity to the UI export, parsing, summary."""

from __future__ import annotations

from pathlib import Path

import pytest

from tara_automation.commfile.trlim import (TrLimEngine, check_inputs,
                                            parse_trlim_csv, render_deck,
                                            subsystem_names, summarize_trlim,
                                            summary_line)
from tara_automation.machine import REPO_ROOT
from tara_automation.run_manager import RunManager

UI_DIR = REPO_ROOT / "studies" / "ATC_ED4_ED5" / "03_TARA_Inputs" / "reference_ui"
EAGL = Path(r"C:\Projects\tara-inputfls\EAGL")
ATC_SUB = UI_DIR.parent / "ED5_ATC.sub"


def _norm(text: str) -> str:
    # The UI writes a cp1252 apostrophe inside one option comment; it decodes
    # as U+FFFD. Comments are ignored by TARA (run 54 proved it), so compare
    # without it.
    return text.replace("\ufffd", "'")


# ------------------------------------------------------ fidelity to the UI --
@pytest.mark.skipif(not (UI_DIR / "UI_runtrlim_comm.txt").exists(),
                    reason="UI reference exports not present")
def test_deck_reproduces_ui_export():
    """With the UI run's own inputs, the rendered deck IS the UI's comm
    (load + TrLim exports), minus only the UI's Excel-launcher tails."""
    tail = 'chdir "C:\\DECH analysis\\temp"\n_excel\n'
    load = (UI_DIR / "UI_loadcase_comm.txt").read_text(encoding="utf-8", errors="replace")
    run = (UI_DIR / "UI_runtrlim_comm.txt").read_text(encoding="utf-8", errors="replace")
    expected = load.replace(tail, "") + "\n" + run.replace(tail, "")

    tmp = Path(r"C:\DECH analysis\temp")
    deck = render_deck(
        results_dir=tmp, trace_log=tmp / "taralog.txt", trlim_csv=tmp / "TrLim.csv",
        case=EAGL / "basecase" / "30HS_22-G7_pst1.epc", sub=ATC_SUB,
        con=EAGL / "control files" / "AZ.con", mon=EAGL / "control files" / "AZ.mon",
        send="ED5_ONLY", recv="CAISO")
    assert _norm(deck) == _norm(expected)


@pytest.mark.skipif(not (REPO_ROOT / "runs" / "55" / "comm" / "trlim.txt").exists(),
                    reason="run 55 not present")
def test_deck_reproduces_signed_off_run_55():
    r = REPO_ROOT / "runs" / "55"
    deck = render_deck(
        results_dir=r / "results", trace_log=r / "logs" / "load_trlim.txt",
        trlim_csv=r / "results" / "ED5_STATION_TrLim.csv",
        case=EAGL / "basecase" / "30HS_22-G7_pre.epc", sub=ATC_SUB,
        con=EAGL / "control files" / "AZ.con", mon=EAGL / "control files" / "AZ.mon",
        send="ED5_STATION", recv="CAISO")
    assert _norm(deck) == _norm((r / "comm" / "trlim.txt").read_text(encoding="utf-8"))


def _render(**kw):
    base = dict(results_dir="R", trace_log="T", trlim_csv="C", case="x.epc",
                sub="s.sub", con="c.con", mon="m.mon", send="SRC", recv="SINK")
    return render_deck(**{**base, **kw})


def test_options_precede_every_read():
    """Run 58: opt cont set after CONT left mulSecPrintMode at its default and
    changed the contingency count. All option blocks must come first."""
    lines = _render().splitlines()
    idx = lambda s: next(i for i, l in enumerate(lines) if s in l)
    assert idx("mulSecPrintMode") < idx("READ EPC") < idx("CONT \"") < idx("trlim")
    assert idx("TrLimMode") < idx(" %save \"C\"")


def test_params_substitute_and_unknown_param_rejected():
    deck = _render(params={"rating_cont": 3, "sens_cutoff": 0.05,
                           "max_test_mw": 1500.0})
    assert "monBranRatingCont      3 //" in deck
    assert "SensCutOff           0.05 //" in deck
    assert "maxTransferTestMW    1500 //" in deck
    assert "@@" not in deck
    with pytest.raises(KeyError, match="Unknown TrLim parameter"):
        _render(params={"SensCutoff": 0.05})


def test_bad_subsystem_name_rejected():
    with pytest.raises(ValueError):
        _render(send="TWO WORDS")


# ------------------------------------------------------------------ inputs --
SUB = """// comment
SUBSYSTEM ED5_STATION
BUS 19169   // 230 kV
BUS 19067
END
SUBSYSTEM CAISO
AREA 30
END
"""


def _files(tmp_path):
    case = tmp_path / "case.epc"
    case.write_bytes(b"x" * 20_000)
    sub = tmp_path / "a.sub"
    sub.write_text(SUB)
    con = tmp_path / "a.con"
    con.write_text("CONTINGENCY x\nEND\n")
    mon = tmp_path / "a.mon"
    mon.write_text("MONITOR x\n")
    return case, sub, con, mon


def test_subsystem_names(tmp_path):
    _, sub, _, _ = _files(tmp_path)
    assert subsystem_names(sub) == {"ED5_STATION": [19169, 19067], "CAISO": []}


def test_check_inputs_rejects_unknown_subsystem_and_raw(tmp_path):
    case, sub, con, mon = _files(tmp_path)
    with pytest.raises(KeyError, match="ED5_ONLY"):
        check_inputs(case, sub, con, mon, ["ED5_ONLY"], "CAISO")
    with pytest.raises(ValueError, match="EPC"):
        check_inputs(case, sub, con, mon, ["ED5_STATION"], "CAISO", fmt="RAW")


def test_prepare_writes_run_files(tmp_path):
    case, sub, con, mon = _files(tmp_path)
    rm = RunManager(tmp_path / "runs")
    run = rm.create_run(engine="comm", case=case, label="t")
    bat = TrLimEngine(tara_exe=Path("tara.exe")).prepare(
        run, case, sub, con, mon, send="ED5_STATION", recv="CAISO")
    deck = (run.comm_dir / "trlim.txt").read_text(encoding="utf-8")
    assert f'READ EPC 21 "{case.resolve()}" 0 0' in deck
    assert 'sendSysNameTrLim     "ED5_STATION"' in deck
    assert str(run.results_dir.resolve() / "ED5_STATION_TrLim.csv") in deck
    assert bat.exists()
    assert run.manifest["trlim"]["send_buses"] == [19169, 19067]


# ------------------------------------------------------------ parse/summary --
HDR = ("Sending System,Receiving System,Max Test MW, Monitored Facility ,Fr Bus,"
       "Fr Name,To Bus,To Name,CKT,Section,kVs,Areas,Areas Name,Zones,Zones Name,"
       "TrLim,Dfax,Rate Base , Rate Cont , Cont Name , Cont ID,"
       "Base Flow MW (No Transfer),Cont Flow MW (No Transfer),"
       "DC %Loading (No Transfer),HeadRoom,Special Message,")


def _row(path, fac, fr, to, trlim, dfax, head, msg=""):
    return (f"{path},SINK,  2000.0, {fac} ,1,{fr},2,{to},1 ,1,230 ,19 ,W ,191 ,Z ,"
            f"  {trlim},  {dfax},  282.0,   282.0,CONT A,   185,  241.4,  290.3,"
            f"   102.95,   {head},{msg},")


TRLIM_CSV = "\n".join([
    "X Proportional Transfer Report [TARA Ver test]",
    "",
    " Contingency File: C:\\a.con (total 6565 contingencies)",
    "",
    HDR,
    _row("100_A_____", "A-B", "A", "B", 1037.1, -0.17, -180.7,
         "!!!WARNING!!! Increasing Transfer relieves initial overload!"),
    _row("100_A_____", "C-D", "C", "D", -111.3, 0.07471, -8.3),
    _row("100_A_____", "C-D", "C", "D", 46.9, 0.07925, 3.7),
    _row("100_A_____", "E-F", "E", "F", 160.1, 0.19, 30.6),
    _row("200_B_____", "E-F", "E", "F", 900.0, 0.05, 45.0),
    "200_B_____,SINK,short,row",
]) + "\n"


def test_parse_and_summarize(tmp_path):
    p = tmp_path / "S_TrLim.csv"
    p.write_text(TRLIM_CSV)
    rep = parse_trlim_csv(p)
    assert rep["n_contingencies"] == 6565
    assert rep["paths"] == ["100_A_____", "200_B_____"]
    assert len(rep["rows"]) == 5 and rep["ragged_rows"] == 1

    s = summarize_trlim(rep)
    a = s["paths"]["100_A_____"]
    assert a["first_limit_mw"] == 46.9            # not the relieved 1037.1 row
    assert a["first_limit"]["short"] == "C-D 230"
    assert [w["trlim_mw"] for w in a["worsened_initial_overloads"]] == [-111.3]
    assert a["relieved_initial_overloads"] == 1
    b = s["paths"]["200_B_____"]
    assert b["first_limit_mw"] == 900.0 and not b["worsened_initial_overloads"]

    line = summary_line("S", "SINK", s)
    assert "100_A: 46.9 MW on C-D 230 [1 pre-existing overload(s) worsened]" in line
