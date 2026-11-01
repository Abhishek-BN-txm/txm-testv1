"""Footprint compiler (task 17): selectors, hops, emission form, guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from tara_automation.footprint import (FootprintError, check_references,
                                       compile_footprint, load_network,
                                       referenced_subsystems, render_sub,
                                       write_outputs)

BUS_HDR = ("   Bus#,Bus Name    ,Volt,Area,Zone,BTyp,CodeBTyp,Vmag [PU], "
           "Vmag [kV], Vangle,     PGen, QGen, PloadNom,   GShntOn,   BShntOn, "
           "Ownr,  PMism,")
BR_HDR = ("  Fr Bus,Fr Name     ,Volt,FrAr,FrZn, To Bus,To Name     ,Volt,ToAr,"
          "ToZn,CKT, Section, Mt, St,   MW_Flow,")

# bus, name, kV, area, zone, owner
BUSES = [
    (101, "A_500", 500, 1, 11, 7), (102, "A_230", 230, 1, 11, 7),
    (103, "A_115", 115, 1, 12, 7), (104, "A_69", 69, 1, 12, 8),
    (105, "A_POI", 230, 1, 12, 8), (106, "DUMMY_1", 230, 1, 12, 8),
    (201, "B_500", 500, 2, 21, 9), (202, "B_230", 230, 2, 21, 9),
    (203, "B_115", 115, 2, 22, 9),
    (301, "C_500", 500, 3, 31, 5), (302, "C_230", 230, 3, 31, 5),
    (303, "C_69", 69, 3, 31, 5),
]
# chain 105-102-101-201-202-203 ; 102-103-104 ; 101-301-302 ; 104-303 (69 kV path)
BRANCHES = [(105, 102, 1), (102, 101, 1), (101, 201, 1), (201, 202, 1),
            (202, 203, 1), (102, 103, 1), (103, 104, 1), (101, 301, 1),
            (301, 302, 1), (104, 303, 1), (106, 105, 0)]   # last is out of service


@pytest.fixture
def results(tmp_path) -> Path:
    d = tmp_path / "results"
    d.mkdir()
    lines = ["Bus Data . Client-X", "   Created - today", BUS_HDR]
    for n, name, kv, ar, zn, own in BUSES:
        lines.append(f"  {n},{name:<12},{kv},{ar},{zn},Load,1,1.0,{kv},0.0,"
                     f"0,0,0,0,0,{own},0,")
    (d / "BusData.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = ["Branch Data. Client-X", "   Created - today", BR_HDR]
    for a, b, st in BRANCHES:
        lines.append(f"  {a},X,1,1,1,{b},Y,1,1,1,1 ,1,F,{st},0.0,")
    (d / "BranchData.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_tx(d, [])
    (d / "CaseSum.csv").write_text(f" Buses,  {len(BUSES)} \n", encoding="utf-8")
    return d


TX_HDR = "  Bus#,Bus Name    ,Volt,   Bus#,Bus Name    ,Volt,CKT,Section,Area,"


def write_tx(d: Path, pairs) -> None:
    """TransfmrData.csv: transformers live here, never in BranchData."""
    lines = ["Transformer Data", "Created", TX_HDR]
    lines += [f"  {a},X,1,{b},Y,1,1 ,1,1," for a, b in pairs]
    (d / "TransfmrData.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def spec(tmp_path, text: str) -> Path:
    p = tmp_path / "fp.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_network_parse(results):
    net = load_network(results)
    assert len(net.buses) == 12
    assert net.buses[104].owner == 8 and net.buses[203].zone == 22
    assert 106 not in net.adjacency[105]          # out-of-service branch ignored
    assert net.adjacency[101] == {102, 201, 301}


def test_bus_count_mismatch_fails(results):
    (results / "CaseSum.csv").write_text(" Buses,  99 \n", encoding="utf-8")
    with pytest.raises(FootprintError, match="CaseSum"):
        load_network(results)


def test_moved_column_fails(results):
    p = results / "BusData.csv"
    p.write_text(p.read_text().replace("Ownr", "Owner"), encoding="utf-8")
    with pytest.raises(FootprintError, match="layout changed"):
        load_network(results)


def test_whole_areas_emit_area_form(results, tmp_path):
    fp = compile_footprint(spec(tmp_path, "study_area: {name: ST, areas: [1, 2]}\n"),
                           load_network(results))
    g = fp.study_area
    assert g.form == "AREA" and g.areas_exact == [1, 2]
    sub = render_sub(fp)
    assert "SUBSYSTEM ST\nAREA 1\nAREA 2\nEND" in sub
    assert "AREAS" not in sub          # AREAS a b is a range: never emitted


def test_force_bus_and_filters(results, tmp_path):
    net = load_network(results)
    fp = compile_footprint(spec(tmp_path, "study_area: {name: ST, areas: [1, 2]}\n"),
                           net, force_bus=True)
    assert fp.study_area.form == "BUS"
    assert render_sub(fp).count("\nBUS ") == 9
    fp = compile_footprint(spec(tmp_path, """
study_area: {name: ST, areas: ["1-2"]}
sub_areas:
  HV: {areas: [1], kv_min: 200, exclude: {name_regex: "^DUMMY"}}
  Z12: {zones: [12]}
  OWN: {owners: [9], kv_max: 230}
"""), net)
    assert fp.get("HV").buses == {101, 102, 105}
    assert fp.get("HV").form == "BUS"
    assert fp.get("Z12").buses == {103, 104, 105, 106}
    assert fp.get("OWN").buses == {202, 203}


def test_near_hops_and_via_kv(results, tmp_path):
    net = load_network(results)
    fp = compile_footprint(spec(tmp_path, """
study_area: {name: ST, areas: [1, 2, 3]}
sub_areas:
  N2: {near: {bus: 105, hops: 2}}
  N3HV: {near: {bus: 105, hops: 4, via_kv_min: 115}}
"""), net)
    assert fp.get("N2").buses == {105, 102, 101, 103}
    # 4 hops but never through the 69 kV buses 104 / 303
    assert fp.get("N3HV").buses == {105, 102, 101, 103, 201, 301, 202, 302}


def test_global_exclude_and_group_refs(results, tmp_path):
    fp = compile_footprint(spec(tmp_path, """
study_area: {name: ST, areas: [1, 2]}
sub_areas:
  A1: {areas: [1]}
  BOTH: {groups: [A1], buses: [302]}
exclude: {name_regex: "^DUMMY"}
"""), load_network(results))
    assert 106 not in fp.get("A1").buses
    assert fp.get("A1").form == "BUS"           # area 1 minus a bus is not whole
    assert fp.get("BOTH").buses == fp.get("A1").buses | {302}
    assert any("outside the study area" in w for w in fp.warnings)


@pytest.mark.parametrize("text, match", [
    ("sub_areas: {X: {areas: [1]}}\n", "study_area"),
    ("study_area: {areas: [9]}\n", "none of"),
    ("study_area: {buses: [999]}\n", "not in the case"),
    ("study_area: {areas: [1], kv_min: 900}\n", "no buses"),
    ("study_area: {areas: [1]}\nsub_areas: {X: {groups: [Y]}, Y: {groups: [X]}}\n",
     "circular"),
    ("study_area: {areas: [1]}\nsub_areas: {bad-name: {areas: [1]}}\n", "group name"),
    ("study_area: {areas: [1]}\nsources: {S: {areas: [1]}}\nsinks: {K: {buses: [101]}}\n",
     "share"),
    ("study_area: {areas: [1], ares: [2]}\n", "unknown key"),
    ("study_area: {areas: ['3-1']}\n", "backwards"),
    ("study_area: {areas: [1]}\nmon: auto\n", "monitor"),
    ("study_area: {areas: [1]}\ncon: nothere.con\n", "neither"),
])
def test_spec_errors(results, tmp_path, text, match):
    with pytest.raises(FootprintError, match=match):
        compile_footprint(spec(tmp_path, text), load_network(results))


def test_kv_filtered_study_area_warns(results, tmp_path):
    fp = compile_footprint(spec(tmp_path, "study_area: {areas: [1], kv_min: 115}\n"),
                           load_network(results))
    assert any("SINGLE TIE" in w for w in fp.warnings)


def test_write_outputs_auto_files(results, tmp_path):
    fp = compile_footprint(spec(tmp_path, """
study_area: {name: AZ, areas: [1, 2]}
sources: {SRC: {buses: [105]}}
sinks: {SNK: {areas: [3]}}
monitor: {kv_min: 50, kv_max: 800}
con: auto
mon: auto
"""), load_network(results))
    out = tmp_path / "out"
    rec = write_outputs(fp, out, required_names=["SRC", "SNK"])
    assert (out / "footprint.con").read_text() == (
        "SINGLE BRANCH IN SUBSYSTEM AZ\nSINGLE TIE FROM SUBSYSTEM AZ\n")
    assert "MONITOR BRANCHES IN SUBSYSTEM AZ kvrange 50 800" in (
        out / "footprint.mon").read_text()
    tags = (out / "bus_tags.csv").read_text().splitlines()
    assert tags[0] == "bus,name,kv,area,zone,owner,groups,lat,lon,located_by"
    assert any(t.startswith("105,") and ",AZ;SRC," in t for t in tags)
    assert set(rec["sha256"]) == {"sub", "con", "mon"}
    # deterministic: same spec + network -> same bytes
    first = (out / "footprint.sub").read_bytes()
    write_outputs(fp, out)
    assert (out / "footprint.sub").read_bytes() == first


def test_reference_check(results, tmp_path):
    con = tmp_path / "x.con"
    con.write_text("// SUBSYSTEM IGNORED in a comment\n"
                   "SINGLE BRANCH IN SUBSYSTEM AZ\nSINGLE TIE FROM SUBSYSTEM 'OTHER'\n")
    assert referenced_subsystems(con) == {"AZ", "OTHER"}
    fp = compile_footprint(spec(tmp_path, "study_area: {name: AZ, areas: [1]}\n"),
                           load_network(results))
    problems = check_references(fp, [con], names=["CAISO"])
    assert len(problems) == 2
    with pytest.raises(FootprintError, match="OTHER"):
        write_outputs(fp, tmp_path / "o", fallback={"con": con})


def test_pipeline_stage(results, tmp_path, capsys):
    import shutil

    from tara_automation.run_manager import RunManager
    from tara_automation.study import Study
    from tara_automation.study_pipeline import STAGES, stage_footprint

    assert STAGES.index("basecase") < STAGES.index("footprint") \
        < STAGES.index("contingency")
    case = tmp_path / "base_pre.epc"
    case.write_bytes(b"x" * 20_000)
    fp_yaml = spec(tmp_path, "study_area: {name: AZ, areas: [1, 2]}\n"
                   "sources: {SRC: {buses: [105]}}\nsinks: {SNK: {areas: [3]}}\n"
                   "monitor: {kv_min: 50, kv_max: 800}\ncon: auto\nmon: auto\n")
    study = Study.init(tmp_path / "studies", "T", [case], footprint=fp_yaml,
                       atc={"send": ["SRC"], "recv": "SNK"})
    rm = RunManager(tmp_path / "runs")
    with pytest.raises(RuntimeError, match="basecase"):
        stage_footprint(study, rm)

    run = rm.create_run(engine="comm", case=case)
    shutil.rmtree(run.results_dir)
    shutil.copytree(results, run.results_dir)
    study.config.runs["basecase"] = {study.baseline().label: run.number}
    rec = stage_footprint(study, rm)
    c = study.config
    assert Path(c.sub).name == "footprint.sub"
    assert Path(c.con).name == "footprint.con" and Path(c.mon).name == "footprint.mon"
    assert rec["groups"]["AZ"]["buses"] == 9

    # changing the footprint after contingency ran says those runs are stale
    c.runs["contingency"] = {"pre": 1}
    Path(c.footprint).write_text(
        Path(c.footprint).read_text().replace("[1, 2]", "[1]"), encoding="utf-8")
    capsys.readouterr()
    stage_footprint(study, rm)
    assert "Re-run them with --force" in capsys.readouterr().out

    # an ATC name the footprint does not define is refused
    c.atc = {"send": ["NOPE"], "recv": "SNK"}
    with pytest.raises(Exception, match="NOPE"):
        stage_footprint(study, rm)


# ---------------------------------------------------------------- polygons --
GEO_BUS_HDR = BUS_HDR + "  QMism,  MVAMism,Latitude,Longitude,"
GEO_BR_HDR = ("  Fr Bus,Fr Name     ,Volt,FrAr,FrZn, To Bus,To Name     ,Volt,ToAr,"
              "ToZn,CKT, Section, Mt, St,   MW_Flow, MVAr_flow,  MVA_flow, "
              "LossesMW, LossesMVAr,        R,        X,")
# bus, kV, lat, lon (0, 0 = no coordinate in the case)
GEO_BUSES = [(1, 230, 33.0, -112.0), (2, 69, 0, 0), (3, 230, 33.5, -111.5),
             (4, 230, 0, 0), (5, 230, 35.0, -110.0), (6, 115, 0, 0),
             (7, 115, 0, 0), (8, 115, 0, 0), (9, 230, 0, 0), (10, 230, 0, 0)]
GEO_TX = [(1, 2)]
# from, to, Mt (metered end), X
GEO_BRANCHES = [(1, 4, "F", 0.01), (4, 3, "F", 0.01), (3, 5, "F", 0.08),
                (5, 7, "F", 0.02), (7, 6, "F", 0.02), (5, 9, "F", 0.0001),
                (3, 10, "T", 0.03)]
# Located: 1, 3 (inside BOX) and 5 (outside). 8 is isolated.
# Flood from 1 and 3 reaches 2, 4, 10 and stops at 5; 6, 7, 9 lie beyond it.


def write_geo(d: Path, buses, branches, tx) -> Path:
    """buses: (n, kV, lat, lon) or (n, kV, lat, lon, area)."""
    d.mkdir()
    lines = ["Bus Data", "Created", GEO_BUS_HDR]
    for b in buses:
        n, kv, lat, lon = b[:4]
        area = b[4] if len(b) > 4 else 1
        lines.append(f"  {n},B{n},{kv},{area},1,Load,1,1.0,{kv},0.0,0,0,0,0,0,1,0,0,0,"
                     f"{lat},{lon},")
    (d / "BusData.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = ["Branch Data", "Created", GEO_BR_HDR]
    for a, b, mt, x in branches:
        lines.append(f"  {a},X,1,1,1,{b},Y,1,1,1,1 ,1,{mt},1,0,0,0,0,0,0.0,{x},")
    (d / "BranchData.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_tx(d, tx)
    return d


@pytest.fixture
def geo(tmp_path) -> Path:
    return write_geo(tmp_path / "geo", GEO_BUSES, GEO_BRANCHES, GEO_TX)


BOX = "[[32.5, -112.5], [32.5, -111.2], [33.8, -111.2], [33.8, -112.5]]"


def test_transformers_connect_from_transfmrdata(geo):
    net = load_network(geo)
    assert 2 in net.adjacency[1]                 # from TransfmrData
    assert 10 in net.adjacency[3]                # Mt = T is just a line


def test_missing_transfmrdata_fails(geo):
    (geo / "TransfmrData.csv").unlink()
    with pytest.raises(FootprintError, match="TransfmrData"):
        load_network(geo)


def test_locate_estimates_nothing(geo):
    from tara_automation.footprint import locate
    net = load_network(geo)
    s = locate(net)
    assert s["by_source"] == {"case": 3, "none": 7}
    assert net.buses[4].lat is None


def test_flood_rule(geo, tmp_path):
    fp = compile_footprint(spec(tmp_path, f"""
study_area: {{name: ST, areas: [1]}}
sub_areas:
  BOX: {{polygon: {BOX}}}
  BOX_HV: {{polygon: {BOX}, kv_min: 200}}
coordinates: {{file: none}}
"""), load_network(geo))
    assert fp.get("BOX").buses == {1, 2, 3, 4, 10}      # stops at 5
    assert fp.get("BOX_HV").buses == {1, 3, 4, 10}
    assert any("BOX: 2 located bus(es) inside the polygon (case 2); 3 unlocated "
               "bus(es) added by flooding; the flood stopped at 1" in n
               for n in fp.polygon_notes)
    assert any("come from flooding" in w for w in fp.warnings)   # 3 flooded > 2 inside
    assert any("coordinates.file is none" in w for w in fp.warnings)


def test_polygon_geojson_hole_and_kml(geo, tmp_path):
    import json as _json
    outer = [[-112.5, 32.5], [-111.2, 32.5], [-111.2, 33.8], [-112.5, 33.8], [-112.5, 32.5]]
    hole = [[-111.6, 33.4], [-111.4, 33.4], [-111.4, 33.6], [-111.6, 33.6], [-111.6, 33.4]]
    (tmp_path / "r.geojson").write_text(_json.dumps({
        "type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [outer, hole]}}]}))
    (tmp_path / "r.kml").write_text(
        '<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Placemark>'
        '<Polygon><outerBoundaryIs><LinearRing><coordinates>'
        + " ".join(f"{x},{y},0" for x, y in outer) +
        '</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>')
    fp = compile_footprint(spec(tmp_path, """
study_area: {name: ST, areas: [1]}
sub_areas:
  GJ: {polygon: {file: r.geojson}}
  KM: {polygon: {file: r.kml}}
coordinates: {file: none}
"""), load_network(geo))
    assert fp.get("GJ").buses == {1, 2, 4}     # 3 is in the hole: the flood stops there
    assert fp.get("KM").buses == {1, 2, 3, 4, 10}


def test_coords_file_is_primary(geo, tmp_path):
    (tmp_path / "bus_coords.csv").write_text("Bus,Lat,Lon,Note\n8,33.1,-111.9,site\n"
                                             "3,40.0,-100.0,moved\n999,33,-112,x\n")
    fp = compile_footprint(spec(tmp_path, f"""
study_area: {{name: ST, areas: [1]}}
sub_areas:
  BOX: {{polygon: {BOX}}}
coordinates: {{file: bus_coords.csv}}
"""), load_network(geo))
    assert fp.get("BOX").buses == {1, 2, 4, 8}    # 3 moved outside: flood stops at it
    loc = fp.network.located
    assert loc["by_source"] == {"case": 2, "file": 2, "none": 6}
    assert loc["coords_file_unknown_buses"] == 1
    out = tmp_path / "o"
    write_outputs(fp, out)
    assert "## Bus locations" in (out / "footprint_report.md").read_text()
    tags = (out / "bus_tags.csv").read_text()
    assert "8,B8,115,1,1,1,ST;BOX,33.10000,-111.90000,file" in tags
    assert "4,B4,230,1,1,1,ST;BOX,,," in tags


def test_case_coords_switched_off(geo, tmp_path):
    (tmp_path / "c.csv").write_text("bus,lat,lon\n1,33.0,-112.0\n5,35.0,-110.0\n")
    fp = compile_footprint(spec(tmp_path, f"""
study_area: {{name: ST, areas: [1]}}
sub_areas:
  BOX: {{polygon: {BOX}}}
coordinates: {{file: c.csv, use_case_coords: false}}
"""), load_network(geo))
    # 3 has no location now, so the flood passes through it and stops at 5
    assert fp.get("BOX").buses == {1, 2, 3, 4, 10}
    with pytest.raises(FootprintError, match="file is required"):
        compile_footprint(spec(tmp_path, f"""
study_area: {{name: ST, areas: [1]}}
sub_areas: {{BOX: {{polygon: {BOX}}}}}
coordinates: {{file: none, use_case_coords: false}}
"""), load_network(geo))


def test_flood_cap_and_stray_area(tmp_path):
    # 1 located inside; chain 11-12-13 unlocated; 13 is in another area
    d = write_geo(tmp_path / "chain",
                  [(1, 230, 33.0, -112.0), (11, 230, 0, 0), (12, 230, 0, 0),
                   (13, 230, 0, 0, 2)],
                  [(1, 11, "F", 0.01), (11, 12, "F", 0.01), (12, 13, "F", 0.01)], [])
    fp = compile_footprint(spec(tmp_path, f"""
study_area: {{name: ST, areas: [1, 2]}}
sub_areas: {{BOX: {{polygon: {BOX}}}}}
coordinates: {{file: none}}
"""), load_network(d))
    assert fp.get("BOX").buses == {1, 11, 12, 13}
    assert any("reached area(s) [2]" in w for w in fp.warnings)
    fp = compile_footprint(spec(tmp_path, f"""
study_area: {{name: ST, areas: [1, 2]}}
sub_areas: {{BOX: {{polygon: {BOX}}}}}
coordinates: {{file: none, max_flood_hops: 2}}
"""), load_network(d))
    assert fp.get("BOX").buses == {1, 11, 12}
    assert any("left 1 unlocated" in n for n in fp.polygon_notes)


@pytest.mark.parametrize("poly, match", [
    ("[[-112.0, 33.0], [-111.0, 33.0], [-111.0, 34.0]]", r"looks like \[lon, lat\]"),
    ("[[33.0, -112.0], [34.0, -111.0]]", "at least 3"),
    ("{file: nothere.geojson}", "not found"),
    ("{path: x}", "polygon takes"),
])
def test_polygon_errors(geo, tmp_path, poly, match):
    with pytest.raises(FootprintError, match=match):
        compile_footprint(spec(tmp_path, f"study_area: {{name: ST, areas: [1]}}\n"
                                         f"sub_areas: {{P: {{polygon: {poly}}}}}\n"
                                         f"coordinates: {{file: none}}\n"),
                          load_network(geo))


def test_no_coordinates_in_case(results, tmp_path):
    """A case without lat/lon columns: a polygon selects nothing -> error."""
    with pytest.raises(FootprintError, match="no buses"):
        compile_footprint(spec(tmp_path, f"study_area: {{name: ST, polygon: {BOX}}}\n"
                                         f"coordinates: {{file: none}}\n"),
                          load_network(results))


def test_junk_case_coordinates_rejected(tmp_path):
    """Run 25 patterns: longitude typed as latitude (lon 0.5), lat == lon."""
    from tara_automation.footprint import locate, plausible_coord
    assert not plausible_coord(-111.942, 0.5)
    assert not plausible_coord(34.56578, 34.56579)
    assert not plausible_coord(95.0, -110.0)
    assert plausible_coord(32.60316, -111.56811)
    d = write_geo(tmp_path / "junk",
                  [(1, 230, 33.0, -112.0), (2, 230, -111.942, 0.5),
                   (3, 230, 34.56578, 34.56579)],
                  [(1, 2, "F", 0.01)], [])
    net = load_network(d)
    assert net.rejected_coords == [2, 3]
    s = locate(net)
    assert net.buses[2].located_by is None and net.buses[3].located_by is None
    assert s["case_coords_rejected"] == 2


# ------------------------------------------------- reference workbook -----
def make_workbook(path: Path, rows) -> Path:
    """rows: (lat, lon, busnum, busname, kV, interconnect, subname)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Buses"
    ws.append(["Y_LAT", "X_LONG", "ENTITYID", "BUSNUM", "BUSNAME", "LONGNAME",
               "BUSTYPE", "VOLTAGE", "INTERCONNECT", "SUBID", "SUBNAME"])
    for lat, lon, n, name, kv, ic, sub in rows:
        ws.append([lat, lon, 0, n, name, "", "LOAD", kv, ic, 0, sub])
    wb.save(path)
    return path


def test_reference_workbook_order_and_checks(geo, tmp_path):
    from tara_automation import bus_coords
    from tara_automation.footprint import locate
    wb = make_workbook(tmp_path / "coords.xlsx", [
        (33.10, -111.90, 1, "B1", 230, "WECC", "Sub One"),      # named: beats case
        (40.00, -100.0, 3, "B3", 230, "WECC", "NULL"),          # estimated: case wins
        (33.20, -111.80, 4, "B4", 230, "WECC", None),           # estimated: fills gap
        (33.30, -111.70, 6, "OTHER", 345, "WECC", "Wrong"),     # number reused: rejected
        (45.00, -90.0, 7, "B7", 115, "EAST", "East Sub"),       # other interconnection
        ("#N/A", "#N/A", 9, "B9", 230, "WECC", "Bad"),          # unusable
        (33.00, -112.0, 10, "B10", 230, "WECC", "Ten"),
        (36.00, -100.0, 10, "B10", 230, "WECC", "Ten copy"),    # duplicate > 5 mi
    ])
    ref = bus_coords.load_reference(wb, "WECC", cache_dir=tmp_path / "cache")
    assert set(ref) == {1, 3, 4, 6, 10}
    assert ref[10].copies == 2 and ref[10].copies_far
    # the second load comes from the cache and is identical
    assert bus_coords.load_reference(wb, "WECC", cache_dir=tmp_path / "cache") == ref
    assert len(list((tmp_path / "cache").glob("bus_coords_WECC_*.csv"))) == 1

    net = load_network(geo)
    s = locate(net, wb, cache_dir=tmp_path / "cache")
    by = {n: (b.located_by, b.lat) for n, b in net.buses.items()}
    assert by[1] == ("file", 33.10)            # named row beats the case
    assert by[3] == ("case", 33.5)             # case beats an estimated row
    assert by[4] == ("file_estimated", 33.20)  # estimated row fills a gap
    assert by[6] == (None, None)               # reused number rejected
    assert by[7] == (None, None)               # EAST row ignored for WECC
    ref_stats = s["reference"]
    assert ref_stats["rejected_reused_number"] == 1
    assert ref_stats["duplicates_far"] == 1
    assert s["by_source"]["file_estimated"] == 1


def test_reference_workbook_errors(tmp_path):
    from tara_automation import bus_coords
    wb = make_workbook(tmp_path / "e.xlsx", [(33.0, -112.0, 1, "B1", 230, "EAST", "x")])
    with pytest.raises(bus_coords.BusCoordsError, match="no usable rows"):
        bus_coords.load_reference(wb, "WECC", cache_dir=None)
    import openpyxl
    bad = openpyxl.Workbook()
    bad.active.append(["LAT", "LON"])
    bad.save(tmp_path / "bad.xlsx")
    with pytest.raises(bus_coords.BusCoordsError, match="missing column"):
        bus_coords.load_reference(tmp_path / "bad.xlsx", "WECC", cache_dir=None)


def test_shipped_workbook_is_present():
    from tara_automation.bus_coords import DEFAULT_WORKBOOK
    assert DEFAULT_WORKBOOK.exists(), "data/reference/Bus_Coordinates.xlsx is missing"
