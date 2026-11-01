"""Task 25: base sub/con/mon appended by the footprint, study requests, desk."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tara_automation.footprint import (FootprintError, compile_footprint,
                                       load_network, subsystem_definitions,
                                       write_outputs)
from tests.test_footprint import results  # noqa: F401  (fixture)

BASE_SUB = """// user's own study data file
SUBSYSTEM AZ
AREA 1
AREA 2
END

SUBSYSTEM 'GENSUB2'
AREAS 1 2
SCALE ALL GENERATION EXCEPT
SUBSYSTEM AZ
END
END
"""
BASE_CON = "SINGLE BRANCH IN SUBSYSTEM AZ\nSINGLE TIE FROM SUBSYSTEM AZ\n"
BASE_MON = "MONITOR BRANCHES IN SUBSYSTEM AZ kvrange 100 800\n"


def write(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------- appending to a base --
def test_subsystem_definitions_skip_nested_references():
    assert subsystem_definitions(BASE_SUB) == ["AZ", "GENSUB2"]
    assert subsystem_definitions("SUBSYSTEM X /* c */\nJOIN\nSUBSYSTEM AZ\nEND\nEND\n"
                                 "SUBSYSTEM Y\nBUS 1\nEND\n") == ["X", "Y"]


def test_base_files_are_appended_not_replaced(results, tmp_path):  # noqa: F811
    write(tmp_path, "AZ.sub", BASE_SUB)
    write(tmp_path, "AZ.con", BASE_CON)
    write(tmp_path, "AZ.mon", BASE_MON)
    spec = write(tmp_path, "fp.yaml", """
study_area: {name: ED_STUDY, areas: [1]}
sub_areas: {HV: {areas: [1], kv_min: 200}}
monitor: {kv_min: 50, kv_max: 800}
con: auto
base: {sub: AZ.sub, con: AZ.con, mon: AZ.mon}
""")
    fp = compile_footprint(spec, load_network(results))
    out = tmp_path / "out"
    rec = write_outputs(fp, out)
    sub = (out / "footprint.sub").read_text()
    assert sub.startswith(BASE_SUB)                       # the user's file, untouched
    assert "appended by tara_automation.footprint" in sub
    assert "SUBSYSTEM ED_STUDY" in sub and "SUBSYSTEM HV" in sub
    con = (out / "footprint.con").read_text()
    assert con.startswith(BASE_CON)
    assert "SINGLE BRANCH IN SUBSYSTEM ED_STUDY" in con
    assert rec["appended"] == {"sub": 2, "con": 2}
    # mon not generated -> the user's file is used as it is
    assert Path(rec["files"]["mon"]) == (tmp_path / "AZ.mon").resolve()
    assert (tmp_path / "AZ.sub").read_text() == BASE_SUB   # source never modified
    assert set(rec["base"]) == {"sub", "con", "mon"}
    assert any("appended to your AZ.con" in w for w in fp.warnings)


def test_generated_lines_already_in_base_are_not_repeated(results, tmp_path):  # noqa: F811
    write(tmp_path, "AZ.sub", BASE_SUB)
    write(tmp_path, "AZ.con", "single  branch in subsystem ED // mine\n")
    spec = write(tmp_path, "fp.yaml", "study_area: {name: ED, areas: [1]}\ncon: auto\n"
                                      "base: {sub: AZ.sub, con: AZ.con}\n")
    rec = write_outputs(compile_footprint(spec, load_network(results)), tmp_path / "o")
    con = (tmp_path / "o" / "footprint.con").read_text()
    assert con.count("SINGLE BRANCH") == 0 and con.count("single  branch") == 1
    assert "SINGLE TIE FROM SUBSYSTEM ED" in con
    assert rec["appended"]["con"] == 1


@pytest.mark.parametrize("yaml_text, match", [
    ("study_area: {name: AZ, areas: [1]}\nbase: {sub: AZ.sub}\n", "already defined"),
    ("study_area: {name: gensub2, areas: [1]}\nbase: {sub: AZ.sub}\n", "already defined"),
    ("study_area: {name: S, areas: [1]}\nbase: {sub: nope.sub}\n", "missing or empty"),
    ("study_area: {name: S, areas: [1]}\nbase: {sub: AZ.sub, foo: x}\n", "base:"),
    ("study_area: {name: S, areas: [1]}\ncon: AZ.con\nbase: {con: AZ.con}\n", "not both"),
])
def test_base_errors(results, tmp_path, yaml_text, match):  # noqa: F811
    write(tmp_path, "AZ.sub", BASE_SUB)
    write(tmp_path, "AZ.con", BASE_CON)
    with pytest.raises(FootprintError, match=match):
        compile_footprint(write(tmp_path, "fp.yaml", yaml_text), load_network(results))


def test_base_con_may_reference_base_subsystems(results, tmp_path):  # noqa: F811
    """The user's .con names AZ, defined only in their .sub: that is fine."""
    write(tmp_path, "AZ.sub", BASE_SUB)
    write(tmp_path, "AZ.con", BASE_CON)
    spec = write(tmp_path, "fp.yaml", "study_area: {name: ED, areas: [1]}\n"
                                      "base: {sub: AZ.sub, con: AZ.con}\n")
    rec = write_outputs(compile_footprint(spec, load_network(results)), tmp_path / "o")
    assert Path(rec["files"]["con"]) == (tmp_path / "AZ.con").resolve()


# --------------------------------------------------------- study requests --
def make_case(tmp: Path, name: str) -> Path:
    p = tmp / "cases" / name
    p.parent.mkdir(exist_ok=True)
    p.write_bytes(b"x" * 20_000)
    return p


def request(tmp: Path, **over) -> dict:
    req = {
        "kind": "tara-study-request", "version": 1,
        "study": {"name": "Desk_Test", "objective": "test objective", "area": "WECC / AZ",
                  "rating_cont": 2, "loading_cutoff": 70, "notes": ["a note"]},
        "cases": [{"path": str(make_case(tmp, "30HS_pre.epc")), "label": None},
                  {"path": str(make_case(tmp, "30HS_pst1.epc")), "label": "post1"}],
        "base_files": {"sub": str(write(tmp, "AZ.sub", BASE_SUB)),
                       "con": str(write(tmp, "AZ.con", BASE_CON))},
        "footprint_yaml": ("study_area:\n  name: ED\n  areas: [1]\ncon: auto\n"
                           f"base: {{sub: {json.dumps(str(tmp / 'AZ.sub'))}}}\n"),
    }
    req.update(over)
    return req


def test_create_study_from_request(tmp_path):
    from tara_automation.study import Study
    from tara_automation.study_request import create
    studies = tmp_path / "studies"
    res = create(request(tmp_path), studies)
    root = studies / "Desk_Test"
    assert res["path"] == str(root)
    for d in ("01_References", "02_Base_Cases", "03_TARA_Inputs", "04_TARA_Outputs",
              "05_Analysis", "06_Reports", "07_Correspondence"):
        assert (root / d).is_dir()
    st = Study.load(studies, "Desk_Test")
    c = st.config
    assert [cs.label for cs in st.ordered()] == ["pre", "post1"]
    # the cases are copied into the study and the study points at the copies
    cases_dir = root / "03_TARA_Inputs" / "cases"
    assert [Path(cs.path) for cs in st.ordered()] == [cases_dir / "30HS_pre.epc",
                                                      cases_dir / "30HS_pst1.epc"]
    assert (cases_dir / "30HS_pre.epc").stat().st_size == 20_000
    assert (tmp_path / "cases" / "30HS_pre.epc").exists()          # original untouched
    readme = (root / "02_Base_Cases" / "README.md").read_text()
    assert "Copied from" in readme and str(tmp_path / "cases" / "30HS_pre.epc") in readme
    assert c.objective == "test objective" and c.notes == ["a note"]
    assert Path(c.sub) == root / "03_TARA_Inputs" / "base" / "AZ.sub"
    assert Path(c.sub).read_text() == BASE_SUB
    fp = (root / "03_TARA_Inputs" / "footprint.yaml").read_text()
    assert "sub: base/AZ.sub" in fp and "con: base/AZ.con" in fp   # copies, not sources
    assert Path(c.footprint) == root / "03_TARA_Inputs" / "footprint.yaml"
    assert (root / "01_References" / "study_request.json").exists()
    assert "--stage basecase footprint contingency" in res["next"][1]


def test_request_problems_are_all_reported(tmp_path):
    from tara_automation.study_request import StudyRequestError, create, validate
    studies = tmp_path / "studies"
    bad = request(tmp_path)
    bad["study"] = {"name": "1bad name", "objective": "", "area": ""}
    bad["cases"] = [{"path": "relative.epc"}, {"path": str(tmp_path / "missing.epc")}]
    bad["base_files"] = {"sub": str(tmp_path / "nope.sub")}
    with pytest.raises(StudyRequestError) as e:
        validate(bad, studies)
    text = " | ".join(e.value.problems)
    for want in ("study name", "objective is required", "area is required",
                 "full path", "not found", "base .sub"):
        assert want in text
    # an existing study is never overwritten
    create(request(tmp_path), studies)
    with pytest.raises(StudyRequestError, match="already exists"):
        create(request(tmp_path), studies)
    with pytest.raises(StudyRequestError, match="not a study request"):
        validate({"kind": "other"}, studies)


# ------------------------------------------------------------ the desk -----
@pytest.fixture
def desk(tmp_path, monkeypatch):
    import scripts.study_desk as sd
    monkeypatch.setattr(sd, "STUDIES", tmp_path / "studies")
    monkeypatch.setattr(sd, "browse_roots", lambda: [tmp_path.resolve()])
    srv = sd.make_server(0)
    port = srv.server_address[1]
    sd.Handler.port = port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}", port
    srv.shutdown()


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_desk_serves_page_and_health(desk, tmp_path):
    base, _ = desk
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        html = r.read().decode("utf-8")
    assert html.startswith("<!doctype html>") and "Footprint Builder" in html
    code, j = call(base + "/api/health")
    assert code == 200 and j["app"] == "tara-study-desk"


def test_desk_browse_stays_inside_roots(desk, tmp_path):
    base, _ = desk
    make_case(tmp_path, "30HS_pre.epc")
    code, j = call(f"{base}/api/browse?kind=case&path={tmp_path / 'cases'}")
    assert code == 200 and [f["name"] for f in j["files"]] == ["30HS_pre.epc"]
    code, _ = call(f"{base}/api/browse?kind=case&path={tmp_path.parent}")
    assert code == 403
    write(tmp_path, "AZ.sub", BASE_SUB)
    code, j = call(f"{base}/api/sub-names?path={tmp_path / 'AZ.sub'}")
    assert j["names"] == ["AZ", "GENSUB2"]


def test_desk_create_requires_own_origin(desk, tmp_path):
    base, port = desk
    body = json.dumps(request(tmp_path)).encode("utf-8")
    code, _ = call(base + "/api/studies", body, {"Content-Type": "application/json"})
    assert code == 403                                   # no Origin
    code, _ = call(base + "/api/studies", body, {"Content-Type": "application/json",
                                                  "Origin": "https://evil.example"})
    assert code == 403
    code, _ = call(base + "/api/studies", body, {"Content-Type": "text/plain",
                                                  "Origin": base})
    assert code == 415
    code, j = call(base + "/api/studies", body, {"Content-Type": "application/json",
                                                  "Origin": base})
    assert code == 201 and j["name"] == "Desk_Test"
    assert (tmp_path / "studies" / "Desk_Test" / "study.json").exists()
    code, j = call(base + "/api/studies", body, {"Content-Type": "application/json",
                                                  "Origin": base})
    assert code == 422 and any("already exists" in p for p in j["problems"])


def test_desk_rejects_foreign_host(desk):
    base, _ = desk
    code, _ = call(base + "/api/health", headers={"Host": "attacker.example"})
    assert code == 403


# ------------------------------------------------------------- bus lists ---
from tests.test_footprint import geo  # noqa: E402,F401  (fixture)

POLY = "[[32.5, -112.5], [32.5, -111.2], [33.8, -111.2], [33.8, -112.5]]"
LIST_YAML = (f"study_area: {{name: ST, areas: [1]}}\n"
             f"sub_areas:\n  BOX: {{polygon: {POLY}}}\n  MIX: {{polygon: {POLY}, buses: [5]}}\n"
             f"coordinates: {{file: none}}\n")


def test_preview_labels_how_each_bus_was_selected(geo):  # noqa: F811
    from tara_automation.footprint_preview import COLUMNS, preview
    out = preview(LIST_YAML, geo)
    box = out["groups"]["BOX"]
    how = {r[0]: r[COLUMNS.index("how")] for r in box["rows"]}
    assert how == {1: "inside", 3: "inside", 2: "flooded", 4: "flooded", 10: "flooded"}
    assert (box["total"], box["inside"], box["flooded"], box["stops"]) == (5, 2, 3, 1)
    mix = {r[0]: r[6] for r in out["groups"]["MIX"]["rows"]}
    assert mix[5] == "selector" and mix[1] == "inside"
    assert out["groups"]["ST"]["polygon"] is False


def _fake_run(runs: Path, n: int, results: Path, status="success") -> None:
    import shutil
    d = runs / str(n)
    shutil.copytree(results, d / "results")
    (d / "manifest.json").write_text(json.dumps(
        {"run": n, "status": status, "case": f"C:/cases/case{n}.epc", "label": f"basecase {n}"}))


def test_desk_networks_and_resolve(desk, tmp_path, geo, monkeypatch):  # noqa: F811
    import scripts.study_desk as sd
    runs = tmp_path / "runs"
    runs.mkdir()
    _fake_run(runs, 7, geo)
    _fake_run(runs, 9, geo, status="failed")        # not offered
    monkeypatch.setattr(sd, "RUNS", runs)
    base, _ = desk
    code, j = call(base + "/api/networks")
    assert code == 200 and [r["run"] for r in j["runs"]] == [7]
    hdr = {"Content-Type": "application/json", "Origin": base}
    body = json.dumps({"yaml": LIST_YAML, "run": 7}).encode()
    code, j = call(base + "/api/resolve", body, hdr)
    assert code == 200 and j["groups"]["BOX"]["total"] == 5 and j["run"] == 7
    code, j = call(base + "/api/resolve", json.dumps({"yaml": LIST_YAML, "run": 9}).encode(), hdr)
    assert code == 404
    bad = LIST_YAML.replace("BOX:", "1BAD:")
    code, j = call(base + "/api/resolve", json.dumps({"yaml": bad, "run": 7}).encode(), hdr)
    assert code == 422 and "group name" in j["error"]
    code, _ = call(base + "/api/resolve", body, {"Content-Type": "application/json",
                                                 "Origin": "https://evil.example"})
    assert code == 403


def test_resolve_by_case_solves_once_then_reuses(desk, tmp_path, geo, monkeypatch):  # noqa: F811
    """A case with no basecase run is solved (TARA stubbed) and reused after."""
    import os
    import time
    import scripts.study_desk as sd
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(sd, "RUNS", runs)
    case = make_case(tmp_path, "30HS_new.epc")
    solves = []

    def fake_solve(c):
        n = 20 + len(solves)
        _fake_run(runs, n, geo)
        m = json.loads((runs / str(n) / "manifest.json").read_text())
        m.update(case=str(c), created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        (runs / str(n) / "manifest.json").write_text(json.dumps(m))
        solves.append(n)
        return n

    monkeypatch.setattr(sd, "_solve", fake_solve)
    base, _ = desk
    hdr = {"Content-Type": "application/json", "Origin": base}
    body = json.dumps({"yaml": LIST_YAML, "case": str(case)}).encode()
    code, j = call(base + "/api/resolve", body, hdr)
    assert code == 200 and j["run"] == 20 and j["solved"]["run"] == 20
    assert j["groups"]["BOX"]["total"] == 5
    code, j = call(base + "/api/resolve", body, hdr)          # reused, not re-solved
    assert code == 200 and j["run"] == 20 and j["solved"] is None and solves == [20]
    # the case file changed after its run -> solved again
    later = time.time() + 120
    os.utime(case, (later, later))
    code, j = call(base + "/api/resolve", body, hdr)
    assert code == 200 and j["solved"]["run"] == 21 and solves == [20, 21]
    # cases outside the browse roots are refused
    outside = Path(tmp_path.anchor) / "nowhere" / "x.epc"
    code, _ = call(base + "/api/resolve", json.dumps({"yaml": LIST_YAML, "case": str(outside)}).encode(), hdr)
    assert code in (403, 404)


def test_find_basecase_respects_case_mtime(tmp_path, geo):  # noqa: F811
    import os
    from tara_automation.footprint_preview import find_basecase
    runs = tmp_path / "runs"
    runs.mkdir()
    case = make_case(tmp_path, "c_pre.epc")
    _fake_run(runs, 3, geo)
    m = json.loads((runs / "3" / "manifest.json").read_text())
    m.update(case=str(case), created_at="2030-01-01T00:00:00")
    (runs / "3" / "manifest.json").write_text(json.dumps(m))
    assert find_basecase(case, runs)["run"] == 3
    m["created_at"] = "2000-01-01T00:00:00"                    # run older than the case
    (runs / "3" / "manifest.json").write_text(json.dumps(m))
    assert find_basecase(case, runs) is None
    os.utime(case, (946_000_000, 946_000_000))                 # case from 1999
    assert find_basecase(case, runs)["run"] == 3


def test_open_study_accepts_only_study_names(desk, tmp_path, monkeypatch):
    import os
    base, _ = desk
    opened = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(str(p)), raising=False)
    hdr = {"Content-Type": "application/json", "Origin": base}
    call(base + "/api/studies", json.dumps(request(tmp_path)).encode(), hdr)
    code, j = call(base + "/api/open-study", json.dumps({"name": "Desk_Test"}).encode(), hdr)
    assert code == 200 and opened and opened[0].endswith("Desk_Test")
    for bad in ("..", "../x", "Desk_Test/../..", "C:/Windows", "", "Nope"):
        code, _ = call(base + "/api/open-study", json.dumps({"name": bad}).encode(), hdr)
        assert code == 404, bad
    code, _ = call(base + "/api/open-study", json.dumps({"name": "Desk_Test"}).encode(),
                   {"Content-Type": "application/json", "Origin": "https://evil.example"})
    assert code == 403
    assert len(opened) == 1



def test_same_name_cases_do_not_overwrite(tmp_path):
    from tara_automation.study import Study
    from tara_automation.study_request import create
    a = tmp_path / "a" / "30HS_pre.epc"
    b = tmp_path / "b" / "30HS_pre.epc"
    for f, byte in ((a, b"a"), (b, b"b")):
        f.parent.mkdir()
        f.write_bytes(byte * 20_000)
    req = request(tmp_path)
    req["cases"] = [{"path": str(a), "label": "one"}, {"path": str(b), "label": "two"}]
    create(req, tmp_path / "studies")
    st = Study.load(tmp_path / "studies", "Desk_Test")
    names = [Path(cs.path).name for cs in st.ordered()]
    assert names == ["30HS_pre.epc", "30HS_pre_2.epc"]
    assert Path(st.ordered()[1].path).read_bytes()[:1] == b"b"


# ----------------------------------------------------------- study runs ----
def _wait_job(base, job_id, timeout=10):
    import time
    end = time.time() + timeout
    while time.time() < end:
        code, j = call(f"{base}/api/job?id={job_id}")
        if j.get("status") != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_run_study_job_progress_busy_and_info(desk, tmp_path, monkeypatch):
    import threading
    import tara_automation.study_pipeline as sp
    base, _ = desk
    hdr = {"Content-Type": "application/json", "Origin": base}
    call(base + "/api/studies", json.dumps(request(tmp_path)).encode(), hdr)
    gate = threading.Event()
    seen = []

    def fake_run_all(study, stages, force=False, jobs=1):
        seen.append((stages[0], force))
        print(f"  {stages[0]}: pretend work for {study.config.name}")
        gate.wait(5)

    monkeypatch.setattr(sp, "run_all", fake_run_all)
    body = json.dumps({"name": "Desk_Test", "stages": ["contingency", "basecase", "bogus"]}).encode()
    code, j = call(base + "/api/run-study", body, hdr)
    assert code == 202 and j["stages"] == ["basecase", "contingency"]       # pipeline order
    job = j["job"]
    # a second run while TARA is busy is refused with a clear message
    code, j2 = call(base + "/api/run-study", body, hdr)
    assert code == 409 and "busy running study Desk_Test" in j2["error"]
    gate.set()
    done = _wait_job(base, job)
    assert done["status"] == "done" and [d["stage"] for d in done["done"]] == ["basecase", "contingency"]
    assert any("pretend work" in line for line in done["log"])
    assert seen == [("basecase", False), ("contingency", False)]
    # errors are reported, and the lock is released afterwards
    def boom(study, stages, force=False, jobs=1):
        raise RuntimeError("contingency run(s) failed: run 99")
    monkeypatch.setattr(sp, "run_all", boom)
    code, j = call(base + "/api/run-study", json.dumps({"name": "Desk_Test", "stages": ["basecase"]}).encode(), hdr)
    done = _wait_job(base, j["job"])
    assert done["status"] == "error" and "run 99" in done["error"]
    # study info
    code, info = call(f"{base}/api/study-info?name=Desk_Test")
    assert code == 200 and [c["label"] for c in info["cases"]] == ["pre", "post1"]
    assert info["running_job"] is None and "approve" in info
    for bad in ("../x", "Nope", ""):
        assert call(f"{base}/api/study-info?name={bad}")[0] == 404
    assert call(base + "/api/run-study", json.dumps({"name": "Nope", "stages": ["basecase"]}).encode(), hdr)[0] == 404
    assert call(base + "/api/run-study", body, {"Content-Type": "application/json",
                                                 "Origin": "https://evil.example"})[0] == 403


def test_case_info_summary(tmp_path):
    from tara_automation.case_info import case_info, render_md
    res = Path("runs/68/results")
    if not (res / "CaseSum.csv").exists():
        pytest.skip("run 68 results not on this machine")
    i = case_info(res, None, "pre", 68)
    assert i["solution"]["converged"] and i["solution"]["iterations"] == 34
    assert i["solution"]["counts"]["buses"] == 29907
    assert any("G_COULE_22" in f and "above Pmax" in f for f in i["flags"])
    assert i["scope"] == "whole case" and i["loadings"]["lines_checked"] > 30000
    assert "| pre | 68 | yes | 34 |" in render_md([i], "T")


# ---------------------------------------------- task 27: projects from a case --
from tests.test_case_diff import pair  # noqa: E402,F401  (fixture)


def test_case_diff_solves_each_case_once_and_compares(desk, tmp_path, pair, monkeypatch):  # noqa: F811
    """Both cases are solved (TARA stubbed) once, then compared from the reports."""
    import time
    import scripts.study_desk as sd
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(sd, "RUNS", runs)
    study, donor = make_case(tmp_path, "post.epc"), make_case(tmp_path, "mit.epc")
    results_for = {study.name: pair[0], donor.name: pair[1]}
    solves = []

    def fake_solve(c):
        n = 30 + len(solves)
        _fake_run(runs, n, results_for[Path(c).name])
        m = json.loads((runs / str(n) / "manifest.json").read_text())
        m.update(case=str(c), created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        (runs / str(n) / "manifest.json").write_text(json.dumps(m))
        solves.append(n)
        return n

    monkeypatch.setattr(sd, "_solve", fake_solve)
    base, _ = desk
    hdr = {"Content-Type": "application/json", "Origin": base}
    body = json.dumps({"study_case": str(study), "donor_case": str(donor)}).encode()
    code, j = call(base + "/api/case-diff", body, hdr)
    assert code == 200, j
    assert [s["run"] for s in j["solved"]] == [30, 31]
    assert j["study"]["run"] == 30 and j["donor"]["run"] == 31
    assert any(e["id"] == "generator:30:1" for g in j["groups"] for e in g["elements"])
    code, j = call(base + "/api/case-diff", body, hdr)        # reused, nothing re-solved
    assert code == 200 and j["solved"] == [] and solves == [30, 31]
    same = json.dumps({"study_case": str(study), "donor_case": str(study)}).encode()
    assert call(base + "/api/case-diff", same, hdr)[0] == 400
    assert call(base + "/api/case-diff", body, {"Content-Type": "application/json",
                                                "Origin": "https://evil.example"})[0] == 403


def test_flagged_projects_are_saved_with_the_study(tmp_path):
    from tara_automation.study_request import StudyRequestError, create
    req = request(tmp_path)
    pre, pst = req["cases"][0]["path"], req["cases"][1]["path"]
    req["carry"] = {"study_case": pre, "donor_case": pst,
                    "elements": ["generator:30:1", "bus:30"], "groups": {"bus:30": "P3"}}
    res = create(req, tmp_path / "studies")
    carry = json.loads((Path(res["path"]) / "03_TARA_Inputs" / "changes" / "carry_projects.json").read_text())
    assert carry["elements"] == ["bus:30", "generator:30:1"]
    # paths point at the study's own copies of the cases
    assert Path(carry["study_case"]).parent == Path(res["path"]) / "03_TARA_Inputs" / "cases"
    assert Path(carry["donor_case"]).name == "30HS_pst1.epc"
    bad = request(tmp_path, study={**req["study"], "name": "Other"})
    bad["carry"] = {"study_case": str(tmp_path / "nowhere.epc"), "donor_case": pst, "elements": ["bus:1"]}
    with pytest.raises(StudyRequestError) as e:
        create(bad, tmp_path / "studies")
    assert any("study_case not found" in p for p in e.value.problems)
