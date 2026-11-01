"""Task 27: case differences from TARA's case reports, grouped into projects."""

from __future__ import annotations

from pathlib import Path

import pytest

from tara_automation.case_diff import compare
from tara_automation.case_reports import ReportError, read_results

REPO = Path(__file__).resolve().parents[1]

# Header rows as TARA 26.02 writes them (runs/86/results), trimmed to the
# columns case_reports.py reads plus their neighbours.
BUS_H = ("Bus#,Bus Name,Volt,Area,Zone,BTyp,CodeBTyp,Vmag [PU],Vmag [kV],Vangle,PGen,QGen,PloadNom,"
         "GShntOn,BShntOn,Ownr,PMism,QMism,MVAMism,Latitude,Longitude")
BR_H = ("Fr Bus,Fr Name,Volt,FrAr,FrZn,To Bus,To Name,Volt,ToAr,ToZn,CKT,Section,Mt,St,MW_Flow,MVAr_flow,"
        "MVA_flow,LossesMW,LossesMVAr,R,X,Charg,TranRat,PhsShftDeg,ShuntGFrom,ShuntBFrom,ShuntGTo,ShuntBTo,"
        "RateA,RateB,RateC,RateD,Length,Own1,Own1%,Transformer?")
TX_H = ("Bus#,Bus Name,Volt,Bus#,Bus Name,Volt,CKT,Section,Area,AreaName,voltViol,Status,tap,#tap,CurTarV,"
        "voltMin,voltMax,Ratio,AngleDegrees,RatioTo,Rat_Min,Rat_Max,Step,LinDropR,LinDropX,Tabl,Mon,Bus #,"
        "MonitBus,Volt")
GEN_H = ("Bus#,Bus Name,Volt,Area,Zone,BTyp,ID,Sta,PGen,POn,QGen,Pmin,Pmax,Pmax2,Pavail,Qmin,Qmax,%Share,"
         "CtrlB#,ContBusNme,Volt,VoltTarg,VoltMagPU,VoltError,Own1,Own1%,MBase,RSource,XSource")
LOAD_H = "Bus#,Bus Name,Volt,Area,Zone,ID,St,BsSt,VoltMagPU,Ownr,ActLoad,Pconst,Qconst,Pcurrt,Qcurrt,PAdmit,QAdmit"
SH_H = ("Bus#,Bus Name,Volt,Area,Zone,ShuntID,Shunt Name,BTyp,Vmag [PU],Vmag [kV],Vangle,Status,ShuntMW,"
        "ShuntMVar,Mod,ModDescr,BlockOrder,CtlBs#,CtlBsNam,NomV,Vmin,Vmax,MonBusVlt,VoltViol,ShSt,GShunt,"
        "BShunt,AdmitMin,AdmitMax,RmPct")


def _row(header: str, vals: dict) -> str:
    seen: dict[str, int] = {}
    out = []
    for name in header.split(","):
        k = seen.get(name, 0)
        seen[name] = k + 1
        v = vals.get(name, "0")
        out.append(str(v[k] if isinstance(v, list) else v))
    return ",".join(out)


def _write(path: Path, header: str, rows: list[dict]) -> None:
    lines = [" TEST  report banner", " Loadflow Case: test.epc", header] + [_row(header, r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


EXISTING_BUSES = {1: ("AAA", 500), 2: ("BBB", 500), 3: ("CCC", 230), 4: ("DDD", 230), 9: ("FAR", 230),
                  70: ("REMOTE1", 230), 71: ("REMOTE2", 230)}


def make_case(d: Path, *, buses, lines, txs=(), gens=(), shunts=()) -> Path:
    d.mkdir(parents=True)
    _write(d / "BusData.csv", BUS_H, [{"Bus#": n, "Bus Name": nm, "Volt": kv, "Area": 14, "Zone": 140,
                                         "BTyp": "Load", "Ownr": 1} for n, (nm, kv) in buses.items()])
    br, tx = [], []
    for a, b, ck, st, x, rate in lines:
        br.append({"Fr Bus": a, "Fr Name": buses[a][0], "Volt": [buses[a][1], buses[b][1]], "FrAr": 14,
                   "To Bus": b, "To Name": buses[b][0], "CKT": ck, "Section": 1, "Mt": "F", "St": st,
                   "R": 0.001, "X": x, "Charg": 0.01, "RateA": rate, "RateB": rate, "RateC": rate,
                   "RateD": rate, "Own1": 1})
    for a, b, ck, x, rate in txs:
        br.append({"Fr Bus": a, "Fr Name": buses[a][0], "Volt": [buses[a][1], buses[b][1]], "FrAr": 14,
                   "To Bus": b, "To Name": buses[b][0], "CKT": ck, "Section": 1, "Mt": "F", "St": 1,
                   "R": 0.0, "X": x, "RateA": rate, "RateB": rate, "Own1": 1, "Transformer?": "Transformer"})
        tx.append({"Bus#": [a, b, b], "Bus Name": [buses[a][0], buses[b][0], buses[b][0]],
                   "Volt": [buses[a][1], buses[b][1], buses[b][1]], "CKT": ck, "Section": 1, "Area": 14,
                   "Status": "NoReg", "Rat_Min": 0.9, "Rat_Max": 1.1, "Step": 0.00625, "voltMin": 0.98,
                   "voltMax": 1.02})
    # a series-capacitor bypass written twice under one key: must be skipped
    br += [{"Fr Bus": 1, "Fr Name": "AAA", "Volt": [500, 500], "To Bus": 2, "To Name": "BBB",
            "CKT": "BP", "Section": 2, "St": 1, "X": 0.0001}] * 2
    _write(d / "BranchData.csv", BR_H, br)
    _write(d / "TransfmrData.csv", TX_H, tx)
    _write(d / "GenData.csv", GEN_H, [{"Bus#": b, "Bus Name": buses[b][0], "Volt": [buses[b][1]] * 2,
                                        "Area": 14, "ID": gid, "Sta": 1, "PGen": pg, "Pmax": pmax,
                                        "Qmin": qmin, "Qmax": qmax, "CtrlB#": b, "VoltTarg": 1.0,
                                        "MBase": pmax, "XSource": 0.2}
                                       for b, gid, pg, pmax, qmin, qmax in gens])
    _write(d / "LoadData.csv", LOAD_H, [{"Bus#": 3, "Bus Name": "CCC", "Volt": 230, "Area": 14, "ID": "1",
                                          "St": 1, "Pconst": 100, "Qconst": 20}])
    _write(d / "ShuntData.csv", SH_H, [{"Bus#": b, "Bus Name": buses[b][0], "Volt": buses[b][1], "Area": 14,
                                         "ShuntID": sid, "Status": st, "Mod": mode, "ModDescr": "Vlt_Discr",
                                         "BShunt": bsh, "AdmitMin": 0, "AdmitMax": 50}
                                        for b, sid, st, mode, bsh in shunts])
    return d


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cases")
    base_lines = [(1, 2, "1", 1, 0.01, 1000), (2, 3, "1", 1, 0.02, 800), (3, 4, "1", 1, 0.02, 400),
                  (4, 9, "1", 1, 0.02, 400), (70, 71, "1", 1, 0.03, 300), (1, 2, "3", 1, 0.01, 1000)]
    study = make_case(tmp / "study" / "results", buses=EXISTING_BUSES, lines=base_lines,
                      txs=[(2, 3, "T", 0.05, 600)],
                      gens=[(2, "1", 300, 500, -100, 200)],
                      shunts=[(3, "s1", 7, 1, 0)])
    new = {**EXISTING_BUSES, 20: ("FAR", 115), 30: ("Q1_PV", 34.5), 40: ("Q2_PV", 34.5),
           50: ("SUBX", 500), 51: ("Q3", 500), 52: ("Q3_PV", 34.5)}
    donor_lines = [ln for ln in base_lines if ln[1:3] != (2, "3") or ln[0] != 1]   # 1-2 ckt 3 removed
    donor_lines = [ln if ln[:3] != (3, 4, "1") else (3, 4, "1", 1, 0.02, 600) for ln in donor_lines]  # rerate
    donor_lines = [ln if ln[:3] != (70, 71, "1") else (70, 71, "1", 1, 0.015, 300) for ln in donor_lines]
    donor_lines += [
        (3, 4, "2", 1, 0.02, 400), (4, 9, "2", 1, 0.02, 400),        # transmission: share bus 4
        (1, 50, "1", 1, 0.005, 2000), (50, 2, "1", 1, 0.005, 2000),  # loop-in substation SUBX
        (50, 51, "1", 1, 0.001, 500),                                # Q3 gen-tie to SUBX
    ]
    donor = make_case(tmp / "donor" / "results", buses=new, lines=donor_lines,
                      txs=[(2, 3, "T", 0.05, 600), (9, 20, "1", 0.08, 200),   # new 115 kV level at FAR
                           (1, 30, "1", 0.1, 300), (1, 40, "1", 0.1, 300),    # Q1 and Q2 at POI AAA
                           (51, 52, "1", 0.1, 300)],
                      gens=[(2, "1", 250, 500, -110, 210),                    # redispatch + Q limits
                            (30, "1", 280, 280, -90, 90), (40, "1", 290, 290, -95, 95),
                            (52, "1", 400, 400, -130, 130)],
                      shunts=[(3, "s1", 6, 1, 0)])                            # switched: step only
    return study, donor


def _group_of(d: dict, element_id: str) -> dict:
    return next(g for g in d["groups"] if any(e["id"] == element_id for e in g["elements"]))


def test_reader_skips_bypass_and_marks_transformers(pair):
    c = read_results(pair[1])
    assert c.skipped["series-cap bypass records"] == 2
    assert (2, 3, "T", 1) in c["transformer"] and (2, 3, "T", 1) not in c["branch"]
    assert c["transformer"][(1, 30, "1", 1)].f["rat_max"] == 1.1


def test_generation_projects_at_one_poi_stay_apart(pair):
    d = compare(*pair)
    q1, q2 = _group_of(d, "generator:30:1"), _group_of(d, "generator:40:1")
    assert q1["id"] != q2["id"]
    assert {e["id"] for e in q1["elements"]} == {"bus:30", "transformer:1:30:1:1", "generator:30:1"}
    assert q1["mw_added"] == 280


def test_lines_sharing_a_bus_and_a_new_voltage_level_are_one_project(pair):
    d = compare(*pair)
    g = _group_of(d, "branch:3:4:2:1")
    ids = {e["id"] for e in g["elements"]}
    assert {"branch:4:9:2:1", "transformer:9:20:1:1", "bus:20"} <= ids
    assert "branch:3:4:1:1" in ids              # the re-rate shares bus 3/4: attached
    assert g["kind"] == "transmission"


def test_new_substation_is_transmission_and_does_not_absorb_its_generator(pair):
    d = compare(*pair)
    sub, q3 = _group_of(d, "bus:50"), _group_of(d, "generator:52:1")
    assert sub["id"] != q3["id"]
    assert "branch:50:51:1:1" in {e["id"] for e in q3["elements"]}     # gen-tie stays with Q3
    assert {"branch:1:50:1:1", "branch:2:50:1:1"} <= {e["id"] for e in sub["elements"]}
    assert "branch:1:2:3:1" in {e["id"] for e in sub["elements"]}      # removed line, shares buses
    assert not any(e["kind"] == "generator" for e in sub["elements"])


def test_remote_change_is_its_own_upgrade_and_dispatch_is_listed_apart(pair):
    d = compare(*pair)
    up = _group_of(d, "branch:70:71:1:1")
    assert up["kind"] == "upgrade / setting change" and up["n"] == 1
    assert [c["field"] for c in up["elements"][0]["changes"]] == ["x"]
    disp = {e["id"]: [c["field"] for c in e["changes"]] for e in d["dispatch"]}
    assert set(disp["generator:2:1"]) == {"pgen", "qmax", "qmin"}
    every = {e["id"] for g in d["groups"] for e in g["elements"]} | set(disp)
    assert "shunt:3:S1" not in every            # a switched shunt's block step is solved state


def test_missing_table_is_a_clear_error(tmp_path):
    (tmp_path / "results").mkdir()
    with pytest.raises(ReportError, match="BusData.csv missing"):
        read_results(tmp_path / "results")


@pytest.mark.skipif(not (REPO / "runs/89/results/BusData.csv").exists(), reason="needs runs 88/89")
def test_srp_mitigation_case():
    """Real case pair: SRP C26_33HS post -> post with mitigation (runs 88, 89)."""
    d = compare(REPO / "runs/88/results", REPO / "runs/89/results")
    p1 = d["groups"][0]
    assert p1["kind"] == "transmission" and "ABEL" in p1["label"]
    ids = {e["id"] for e in p1["elements"]}
    assert "branch:15071:15080:2:1" in ids      # Randolph - Pinal Central 500 kV ckt 2
    assert d["summary"]["removed"] == 1


# ------------------------------------------------- single-line diagram data --
def test_diagram_data_for_a_project(pair):
    from tara_automation.case_sld import view
    study, donor = read_results(pair[0]), read_results(pair[1])
    v = view(study, donor, [1, 30])                      # Q1's POI and its new bus
    buses = {b["bus"]: b for b in v["buses"]}
    assert buses[30]["action"] == "added" and buses[30]["study"] is None
    assert buses[1]["action"] == "same"
    el = {e["id"]: e for e in v["elements"]}
    assert el["transformer:1:30:1:1"]["action"] == "added" and not el["transformer:1:30:1:1"].get("stub")
    assert el["generator:30:1"]["donor"]["pmax"] == 280
    # bus 1's other connections lead off the diagram: stubs with the far bus named
    stub = el["branch:1:2:1:1"]
    assert stub["stub"] and stub["far"] == 2 and v["more"][1] >= 3
    assert any(f["bus"] == 2 and f["name"] == "BBB" for f in v["far"])
    assert el["branch:1:2:3:1"]["action"] == "removed"            # only in the study case
    # a bus with only two line sections and nothing else is drawn as a joint
    v2 = view(study, donor, [50])
    assert {b["bus"]: b["form"] for b in v2["buses"]}[50] == "bus"   # 3 lines: a real bus


def test_bus_view_takes_every_neighbour_and_solved_values(pair):
    from tara_automation.case_sld import view
    study, donor = read_results(pair[0]), read_results(pair[1])
    v = view(study, donor, [], around=[1])
    assert {b["bus"] for b in v["buses"]} == {1, 2, 30, 40, 50}       # AAA and all its neighbours
    e = {x["id"]: x for x in v["elements"]}
    assert e["branch:2:3:1:1"]["stub"] and e["branch:2:3:1:1"]["far"] == 3   # neighbour's own line
    f = e["transformer:1:30:1:1"]["donor"]
    assert {"mw", "mvar", "mva", "loss_mw", "tap_ratio"} <= set(f)          # for gauges and flows
    assert "vpu" in next(b for b in v["buses"] if b["bus"] == 1)["donor"]
