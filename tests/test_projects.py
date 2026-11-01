"""Project library -> ChangeSet tests.

Fixtures are built from the shipped templates (projects + change workbook),
so a test can never encode a column layout the templates do not have.
"""

from __future__ import annotations

import pytest

from tara_automation import projects as pl
from tara_automation.casechange import read_workbook, write_template
from tara_automation.casechange.inch import InchWriter
from tara_automation.casechange.workbook import append_rows

GEN = {"id": "Q1", "name": "Solar one", "source": "queue", "kind": "gen",
       "poi_bus": 100, "mw": 200, "fuel": "solar", "status": "FS",
       "cod": 2029, "cluster": 2023}


def _lib(tmp_path, *rows):
    p = tmp_path / "projects.xlsx"
    pl.write_template(p)
    pl.append_projects(p, list(rows))
    return pl.read_library(p)


def _case():
    return pl.CaseIndex(
        buses={100: ("WESTWING", 500.0), 101: ("WESTWING2", 230.0),
               200: ("PALOVERDE", 500.0)},
        gen_ids={100: {"1", "2"}}, load_ids={200: {"1"}})


def _changes(tmp_path):
    p = tmp_path / "changes.xlsx"
    write_template(p)
    append_rows(p, "Scenarios", [
        {"ScenarioID": "POI1", "Name": "poi", "NewBusStart": 990001},
        {"ScenarioID": "LINE", "Name": "line"}])
    append_rows(p, "New_Buses", [
        {"Scenarios": "POI1", "BusName": "NEWPOI", "BusNomVolt": 34.5,
         "AreaNum": 14}])
    append_rows(p, "New_Branches", [
        {"Scenarios": "POI1", "BusNumFr": "@NEWPOI", "BusNumTo": 100,
         "LineR": 0.001, "LineX": 0.01, "RateA": 500},
        {"Scenarios": "LINE", "BusNumFr": 100, "BusNumTo": 200, "CKT": "2",
         "LineR": 0.001, "LineX": 0.02, "RateA": 2000}])
    return read_workbook(p)


def _render(cs):
    return InchWriter(cs, cs.scenarios[0], "base.epc", "out.epc").render()


# --------------------------------------------------------------- reading ----
def test_template_reads_back_empty(tmp_path):
    p = pl.write_template(tmp_path / "t.xlsx")
    lib = pl.read_library(p)
    assert lib.projects == [] and lib.ok


def test_reads_and_normalises(tmp_path):
    lib = _lib(tmp_path, dict(GEN, cod="2029-06-01", source="QUEUE",
                              kind="GEN"))
    p = lib.projects[0]
    assert lib.ok, lib.problems
    assert (p.source, p.kind, p.cod, p.cluster) == ("queue", "gen", 2029,
                                                    "2023")
    assert p.dispatch == 200


@pytest.mark.parametrize("row,col", [
    (dict(GEN, id="Q1"), "id"),                       # duplicate (2nd row)
    (dict(GEN, id="Q2", poi_bus=None), "poi_bus"),
    (dict(GEN, id="Q2", mw=0), "mw"),
    (dict(GEN, id="Q2", pgen=250), "pgen"),
    (dict(GEN, id="Q2", pgen=-5), "pgen"),
    (dict(GEN, id="Q2", poi_bus="@NEW"), "poi_bus"),  # @ref without ref
    (dict(GEN, id="Q2", p_complete=60), "p_complete"),
    (dict(GEN, id="Q2", kind="transmission"), "changes_ref"),
    (dict(GEN, id="Q2", cod="someday"), "cod"),
    (dict(GEN, id="Q2", source="rumour"), "source"),
])
def test_validation_problems(tmp_path, row, col):
    lib = _lib(tmp_path, GEN, row)
    assert any(p.column == col for p in lib.problems), lib.problems


def test_storage_may_charge(tmp_path):
    lib = _lib(tmp_path, dict(GEN, kind="storage", pgen=-150))
    assert lib.ok, lib.problems


# ------------------------------------------------------------- selection ----
def test_selection_filters(tmp_path):
    lib = _lib(tmp_path,
               GEN,
               dict(GEN, id="Q2", status="SIS", cod=2032),
               dict(GEN, id="T1", source="TYP", kind="transmission",
                    status="approved", changes_ref="LINE", poi_bus=None,
                    mw=None),
               dict(GEN, id="Q3", enabled="N"))
    ids = lambda spec: [p.id for p in pl.select(lib, spec)]  # noqa: E731
    assert ids({}) == ["Q1", "Q2", "T1"]                     # Q3 disabled
    assert ids({"source": "queue"}) == ["Q1", "Q2"]
    assert ids({"status": ["fs", "GIA"]}) == ["Q1"]          # case-insensitive
    assert ids({"cod_by": 2030}) == ["Q1", "T1"]
    assert ids({"cluster": 2023, "exclude": ["Q1"]}) == ["Q2", "T1"]
    assert ids({"ids": ["Q3"]}) == []                         # disabled wins
    with pytest.raises(pl.SelectionError, match="not in the library"):
        pl.select(lib, {"ids": ["NOPE"]})
    with pytest.raises(pl.SelectionError, match="unknown selection key"):
        pl.select(lib, {"colour": "red"})


def test_parse_selection():
    assert pl.parse_selection("{source: queue, status: [FS, GIA]}") == {
        "source": "queue", "status": ["FS", "GIA"]}
    with pytest.raises(pl.SelectionError):
        pl.parse_selection("[1, 2]")


# ------------------------------------------------------------ changeset -----
def test_gen_maps_to_new_generator_with_free_id(tmp_path):
    lib = _lib(tmp_path, GEN, dict(GEN, id="Q2", mw=50))
    cs = pl.to_changeset(lib.projects, case=_case(), scenario_id="S")
    assert cs.ok, cs.problems
    gens = cs.rows["New_Generators"]
    # bus 100 already has machines 1 and 2 in the case
    assert [g["GenID"] for g in gens] == ["3", "4"]
    assert gens[0]["GenMWMax"] == 200 and gens[0]["GenMW"] == 200
    assert gens[0]["GenMWMin"] == 0.0
    assert "#ADD_GEN" in _render(cs)


def test_storage_and_load_mapping(tmp_path):
    lib = _lib(tmp_path,
               dict(GEN, kind="storage", poi_bus=200, pgen=-100),
               dict(GEN, id="L1", kind="load", poi_bus=200, mw=85, mvar=20))
    cs = pl.to_changeset(lib.projects, case=_case())
    assert cs.ok, cs.problems
    g = cs.rows["New_Generators"][0]
    assert (g["GenMW"], g["GenMWMax"], g["GenMWMin"]) == (-100, 200, -200)
    ld = cs.rows["New_Loads"][0]
    assert (ld["LoadID"], ld["LoadP0"], ld["LoadQ0"]) == ("2", 85, 20)


def test_explicit_id_clash_is_an_error(tmp_path):
    lib = _lib(tmp_path, dict(GEN, gen_id="2"))
    cs = pl.to_changeset(lib.projects, case=_case())
    assert any(p.column == "gen_id" for p in cs.problems)


def test_missing_poi_lists_closest_matches(tmp_path):
    lib = _lib(tmp_path, dict(GEN, poi_bus=999, poi_name="WESTWNG",
                              poi_kv=500))
    cs = pl.to_changeset(lib.projects, case=_case())
    msg = str(cs.problems[0])
    assert "not in the case" in msg
    assert msg.index("100 WESTWING 500") < msg.index("PALOVERDE")


def test_poi_kv_mismatch_is_an_error(tmp_path):
    lib = _lib(tmp_path, dict(GEN, poi_bus=101, poi_kv=500))
    cs = pl.to_changeset(lib.projects, case=_case())
    assert any(p.column == "poi_kv" for p in cs.problems)


def test_transmission_copies_ref_rows(tmp_path):
    lib = _lib(tmp_path, dict(GEN, id="T1", source="TYP",
                              kind="transmission", changes_ref="LINE",
                              poi_bus=None, mw=None))
    cs = pl.to_changeset(lib.projects, changes=_changes(tmp_path),
                         case=_case(), scenario_id="S")
    assert cs.ok, cs.problems
    br = cs.rows["New_Branches"]
    assert len(br) == 1 and br[0]["CKT"] == "2" and br[0]["Scenarios"] == ["S"]
    assert cs.rows["New_Generators"] == []


def test_gen_with_facilities_ref(tmp_path):
    """A generator on a new POI: facilities from the change workbook, the
    machine from the library, the auto bus number frozen."""
    lib = _lib(tmp_path, dict(GEN, poi_bus="@NEWPOI", changes_ref="POI1"))
    cs = pl.to_changeset(lib.projects, changes=_changes(tmp_path),
                         case=_case(), scenario_id="S", new_bus_start=500)
    assert cs.ok, cs.problems
    assert cs.rows["New_Buses"][0]["BusNum"] == 990001   # not 500
    inherited = pl.to_changeset(lib.projects, changes=_changes(tmp_path),
                                case=_case())
    assert inherited.scenarios[0].new_bus_start == 990001
    text = _render(cs)
    assert "990001,1,1,200,0,200" in text                 # gen on the new bus


def test_shared_ref_copied_once(tmp_path):
    lib = _lib(tmp_path,
               dict(GEN, poi_bus="@NEWPOI", changes_ref="POI1"),
               dict(GEN, id="Q2", mw=50, poi_bus="@NEWPOI",
                    changes_ref="POI1"))
    cs = pl.to_changeset(lib.projects, changes=_changes(tmp_path),
                         case=_case())
    assert cs.ok, cs.problems
    assert len(cs.rows["New_Buses"]) == 1
    assert [g["GenID"] for g in cs.rows["New_Generators"]] == ["1", "2"]


def test_ref_that_already_adds_the_generator_is_rejected(tmp_path):
    ch = _changes(tmp_path)
    p = tmp_path / "changes.xlsx"
    append_rows(p, "New_Generators", [
        {"Scenarios": "POI1", "BusNum": "@NEWPOI", "GenMW": 200,
         "GenMWMax": 200}])
    lib = _lib(tmp_path, dict(GEN, poi_bus="@NEWPOI", changes_ref="POI1"))
    cs = pl.to_changeset(lib.projects, changes=read_workbook(p), case=_case())
    assert any("added twice" in p.message for p in cs.problems)
    assert ch.ok


def test_unknown_ref_and_unknown_new_bus(tmp_path):
    lib = _lib(tmp_path,
               dict(GEN, poi_bus="@NEWPOI", changes_ref="NOPE"),
               dict(GEN, id="Q2", poi_bus="@OTHER", changes_ref="POI1"))
    cs = pl.to_changeset(lib.projects, changes=_changes(tmp_path),
                         case=_case())
    cols = [p.column for p in cs.problems]
    assert cols == ["changes_ref", "poi_bus"]


def test_generated_workbook_round_trips(tmp_path):
    lib = _lib(tmp_path, GEN, dict(GEN, id="Q2", poi_bus="@NEWPOI",
                                   changes_ref="POI1"))
    cs = pl.to_changeset(lib.projects, changes=_changes(tmp_path),
                         case=_case(), scenario_id="S", name="two")
    out = pl.write_changes_workbook(cs, tmp_path / "gen.xlsx")
    back = read_workbook(out)
    assert back.ok, back.problems
    assert _render(back).split("\n")[3:] == _render(cs).split("\n")[3:]
