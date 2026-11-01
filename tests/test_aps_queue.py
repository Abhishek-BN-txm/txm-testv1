"""APS queue adapter (task 23): queue parse, POI matching, library rows, ladder, page.

All fixtures are synthetic and tiny; the real data under research/areas/APS
is regression-checked by `scripts/aps_queue.py build` (docs/tasks/23).
"""

from __future__ import annotations

import csv
import json

import pytest

from tara_automation import projects as pl
from tara_automation.ingest.aps import QueueData, ladder, library, pages, queue


# ------------------------------------------------------------------ queue ----
MD = """| Sort | FERC Q Number | Electrical Output MW | County | State | POI |
|  | Q900 | 250 | 250 | Mariopa | AZ | Delaney 500kV | 6/1/28 | 3/31/22 | 1:00 PM | Phase 1 Cluster Study | Network Resource Interconnection Service | 3/31/22 | Energy Storage | | LGIP | Active |
|  | Q901 | 100 | Yama | North Gila 230kV | 6/1/29 | 5/1/25 | 2:00 PM | Phase 1 Cluster Study | Energy Resource Interconnection Service | 5/1/25 | Solar | | LGIP | Active |
|  | Q900 | 999 | Maricopa | AZ | dup row | | | | | | | | | | |
"""


def test_parse_markdown_fixes_duplicate_mw_and_missing_state():
    rows, odd = queue.parse_markdown(MD)
    assert [r["FERC Q Number"] for r in rows] == ["Q900", "Q901"]          # first occurrence kept
    assert rows[0]["Electrical Output MW"] == "250" and rows[0]["Requested POI"] == "Delaney 500kV"
    assert rows[1]["Project Location State"] == "" and rows[1]["Requested POI"] == "North Gila 230kV"
    assert odd == []


def test_clean_normalises_county_and_year():
    rows, _ = queue.parse_markdown(MD)
    c = queue.clean(rows)
    assert [r["County (normalized)"] for r in c] == ["Maricopa", "Yuma"]
    assert [r["Submission Year"] for r in c] == ["2022", "2025"]


# -------------------------------------------------------------- POI text ----
@pytest.mark.parametrize("text,tap", [
    ("Cholla 500kV", False), ("County Line 500kV", False), ("Delaney 500kV Substation", False),
    ("Cedar Mountain - Moonkopi 500 kV line", True), ("HANG1 500kV Cut-In", True),
    ("N/A - line tap on 69 kV l", True), ("Saguaro-Cholla 500kV", True),
])
def test_line_tap(text, tap):
    assert library.is_line_tap(text) is tap


def test_poi_key_groups_spellings_but_not_voltages():
    k = library.poi_key
    assert k("Cholla 500kV") == k("Cholla 500kV Substation") == k("APS Cholla 500 kV switchyard")
    assert k("Cholla 500kV") != k("Cholla 230kV")


BUSES = {14000: library.Bus(14000, "CHOLLA", 500.0, 14), 14204: library.Bus(14204, "CHOLLA", 230.0, 14),
         22536: library.Bus(22536, "N.GILA", 500.0, 22), 14267: library.Bus(14267, "N.GILA", 230.0, 14),
         14002: library.Bus(14002, "MOENKOPI", 500.0, 14)}


def test_candidates_respect_kv_and_rank_by_name():
    c = library.candidates("North Gila 230kV", 230.0, BUSES)
    assert c[0][0].number == 14267 and c[0][1] >= 0.9
    assert all(b.kv == 230.0 for b, _ in c)
    assert library.candidates("Moonkopi 500kV Substation", 500.0, BUSES)[0][0].number == 14002


# ------------------------------------------------------------ library ----
QUEUE_FIELDS = queue.HEADER + ["County (normalized)", "Submission Year"]


def _qd(tmp_path) -> QueueData:
    qd = QueueData(tmp_path / "queue")
    (qd.root).mkdir(parents=True)
    (qd.root / "queue.yaml").write_text("posting: '20261013'\n", encoding="utf-8")
    rows = [
        {"FERC Q Number": "Q900", "Electrical Output MW": "250", "Requested POI": "Cholla 500kV",
         "LGP Projected In Service Date": "6/1/28", "Current Status": "Phase 1 Cluster Study",
         "Interconnection Request Type": "Network Resource Interconnection Service", "Energy Source Type": "Energy Storage"},
        {"FERC Q Number": "Q901", "Electrical Output MW": "300", "Requested POI": "Cholla 500kV Substation",
         "LGP Projected In Service Date": "6/1/29", "Current Status": "Phase 1 Cluster Study",
         "Interconnection Request Type": "Energy Resource Interconnection Service",
         "Energy Source Type": "Combined Energy Source (Solar + Energy Storage)"},
        {"FERC Q Number": "Q902", "Electrical Output MW": "500", "Requested POI": "Saguaro-Cholla 500kV",
         "LGP Projected In Service Date": "6/1/30", "Current Status": "Phase 1 Cluster Study",
         "Interconnection Request Type": "Network Resource Interconnection Service", "Energy Source Type": "Wind"},
        {"FERC Q Number": "Q903", "Electrical Output MW": "100", "Requested POI": "Cholla 230kV",
         "LGP Projected In Service Date": "6/1/20", "Current Status": "In-Service",
         "Interconnection Request Type": "Network Resource Interconnection Service", "Energy Source Type": "Solar"},
    ]
    queue.write_csv(rows, qd.queue_clean, QUEUE_FIELDS)
    by_area = [{"FERC Q Number": r["FERC Q Number"], "Study Area": "Four Corners-Cholla", "Confidence": "high",
                "Process": "In service" if r["Current Status"] == "In-Service" else "2026 Cluster",
                "Interconnection Request Type": r["Interconnection Request Type"],
                "Electrical Output MW": r["Electrical Output MW"], "Requested POI": r["Requested POI"],
                "Current Status": r["Current Status"], "Energy Source Type": r["Energy Source Type"]} for r in rows]
    queue.write_csv(by_area, qd.queue_by_area)
    return qd


def test_library_keeps_only_confirmed_pois(tmp_path):
    qd = _qd(tmp_path)
    c = library.build_library(qd, BUSES, "test")
    assert c["skipped_in_service"] == 1 and c["kept"] == 0 and c["attention"] == 3
    with open(qd.library / "poi_review.csv", encoding="utf-8") as f:
        review = list(csv.DictReader(f))
    cholla = next(r for r in review if r["poi_kv"] == "500.0" and not r["line_tap"])
    assert cholla["n_requests"] == "2" and cholla["cand1_bus"] == "14000"     # two spellings, one row
    assert any(r["line_tap"] == "Y" for r in review)
    assert pl.read_library(qd.library / "projects.xlsx").projects == []


def test_accept_then_library_validates(tmp_path):
    qd = _qd(tmp_path)
    library.build_library(qd, BUSES, "test")
    p = qd.library / "poi_review.csv"
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["cand1_bus"] == "14000":
            r["use_bus"] = "14000"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    n, problems = library.accept_review(qd, who="test", buses=BUSES)
    assert n == 1 and problems == []
    c = library.build_library(qd, BUSES, "test")
    assert c["kept"] == 2 and c["library_problems"] == 0
    lib = pl.read_library(qd.library / "projects.xlsx")
    by = lib.by_id()
    assert by["Q900"].kind == "storage" and by["Q900"].poi_bus == 14000 and by["Q900"].cod == 2028
    assert by["Q901"].kind == "gen" and by["Q901"].fuel == "solar+storage" and by["Q901"].cluster == "2026"


def test_accept_refuses_wrong_kv(tmp_path):
    qd = _qd(tmp_path)
    library.build_library(qd, BUSES, "test")
    p = qd.library / "poi_review.csv"
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows[0]["use_bus"] = "14204"                                 # a 230 kV bus for a 500 kV POI
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    n, problems = library.accept_review(qd, buses=BUSES)
    assert n == 0 and "kV" in problems[0]


# ------------------------------------------------------------- ladder ----
def _deliver(qd: QueueData):
    queue.write_csv([{"file": "APS_FcCho_2025_Cluster_Phase_1_x_20260603.pdf", "cycle": "2025", "phase": "1",
                      "report_date": "2026-06-03", "version": "1", "notes": "", "study_type": "cluster SIS"}],
                    qd.deliver / "aps_studies.csv")
    f = "APS_FcCho_2025_Cluster_Phase_1_x_20260603.pdf"
    queue.write_csv([
        {"file": f, "queue_id": "Q900", "MW": "250", "service_type": "NRIS", "POI": "Cholla 500 kV",
         "total_allocated_cost_$": "60000000", "allocated_NU_cost_$": "", "allocated_IF_cost_$": ""},
        {"file": f, "queue_id": "Q901", "MW": "300", "service_type": "ERIS", "POI": "Cholla 500 kV",
         "total_allocated_cost_$": "1000000", "allocated_NU_cost_$": "", "allocated_IF_cost_$": ""},
        {"file": f, "queue_id": "Q905", "MW": "500", "service_type": "NRIS", "POI": "Cholla 500 kV",
         "total_allocated_cost_$": "90000000", "allocated_NU_cost_$": "", "allocated_IF_cost_$": ""},
    ], qd.deliver / "aps_projects.csv")
    queue.write_csv([
        {"file": f, "upgrade_id_name": "New Cholla-X 500 kV line", "category": "network NU", "cost_$": "100000000",
         "lead_time_or_ISD": "96 months", "allocated_to": "Q900:40% ($40,000,000); Q905:60% ($60,000,000)"},
        {"file": f, "upgrade_id_name": "Q905 POI switchyard", "category": "station NU", "cost_$": "20000000",
         "lead_time_or_ISD": "48 months", "allocated_to": "Q905:100%"},
    ], qd.deliver / "aps_upgrades.csv")


def test_ladder_trigger_and_station_split(tmp_path):
    qd = _qd(tmp_path)
    _deliver(qd)
    d = ladder.build(qd, "Four Corners-Cholla", "FcCho")
    ph = d["phases"][0]
    assert ph["label"] == "2025 Ph1" and [r["q"] for r in ph["projects"]] == ["Q900", "Q901", "Q905"]
    q905 = ph["projects"][2]
    assert q905["station_nu"] == 20_000_000 and q905["cum_nris_mw"] == 750 and q905["in_active_queue"] is False
    (u,) = ph["upgrades"]
    assert u["cost"] == 100_000_000 and u["first_q"] == "Q900" and u["trigger_cum_nris_mw"] == 250
    assert d["source"].endswith("2026-10-13")


def test_page_renders_data(tmp_path):
    qd = _qd(tmp_path)
    _deliver(qd)
    qd.ladders.mkdir(parents=True)
    (qd.ladders / "fccho.json").write_text(json.dumps(ladder.build(qd, "Four Corners-Cholla", "FcCho")), encoding="utf-8")
    out = pages.render(qd, "fccho")
    html = out.read_text(encoding="utf-8")
    assert "__DATA__" not in html and "Four Corners–Cholla Ladder" in html and '"Q905"' in html


# --------------------------------------------------------------- desk ----
def test_desk_view_counts_and_confirmed_poi(tmp_path):
    from tara_automation.ingest.aps import desk
    qd = _qd(tmp_path)
    library.build_library(qd, BUSES, "candidates from run 71 (x)")
    v = desk.view(qd)
    assert v["posting"] == "20261013" and v["totals"]["requests"] == 4
    assert v["totals"]["in_service"] == 1 and v["totals"]["pending"] == 3
    assert v["totals"]["pending_mw"] == 1050.0                       # 250 + 300 + 500, in-service excluded
    fc = next(a for a in v["areas"] if a["key"] == "fccho")
    assert fc["requests"] == 4 and fc["by_process"]["2026 Cluster"]["n"] == 3
    assert v["library"]["review"]["pois"] >= 1 and v["library"]["confirmed_pois"] == 0
    assert not any(r["poi_confirmed"] for r in v["requests"])
    qd.poi_map.write_text("poi_text,poi_kv,bus,bus_name,bus_kv,confirmed_by,confirmed,note\n"
                          "Cholla 500kV,500,14000,CHOLLA,500,user,2026-10-22,\n", encoding="utf-8")
    v = desk.view(qd)
    assert {r["FERC Q Number"] for r in v["requests"] if r["poi_confirmed"]} == {"Q900", "Q901"}


def test_desk_resolve_serves_only_known_folders(tmp_path):
    from tara_automation.ingest.aps import desk
    qd = _qd(tmp_path)
    qd.pages.mkdir(parents=True)
    (qd.pages / "dsr_ladder.html").write_text("<html></html>", encoding="utf-8")
    assert desk.resolve(qd, "pages/dsr_ladder.html") == (qd.pages / "dsr_ladder.html").resolve()
    for bad in ["pages/../queue.yaml", "../queue.yaml", "queue.yaml", "raw/x.json", "pages/missing.html",
                "pages/dsr_ladder.csv", "parsed/queue_by_study_area.csv", "pages\\..\\queue.yaml", ""]:
        assert desk.resolve(qd, bad) is None, bad


def test_desk_endpoints_show_and_serve_queue(tmp_path, monkeypatch):
    import threading
    import urllib.request

    import scripts.study_desk as sd
    from tara_automation import research as R
    from tests.test_study_desk import call
    monkeypatch.setattr(R, "ROOT", tmp_path / "areas")
    R.create_area("APS", "APS", "", {"areas": [14]}, [], [], "", -7)
    R.create_area("XYZ", "XYZ", "", {}, [], [], "", -7)
    qd = _qd(tmp_path / "areas" / "APS" / "data")          # -> areas/APS/data/queue
    qd.pages.mkdir(parents=True)
    (qd.pages / "fccho_ladder.html").write_text("<html>ladder</html>", encoding="utf-8")
    srv = sd.make_server(0)
    sd.Handler.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{sd.Handler.port}"
    try:
        code, j = call(base + "/api/research/area?id=APS")
        assert code == 200 and j["queue"]["totals"]["requests"] == 4
        assert j["queue"]["areas"][0]["page"] == "pages/fccho_ladder.html"
        assert call(base + "/api/research/area?id=XYZ")[1]["queue"] is None      # no adapter / no data
        with urllib.request.urlopen(base + "/api/research/queue-file?id=APS&f=pages/fccho_ladder.html", timeout=10) as r:
            assert r.read() == b"<html>ladder</html>" and r.headers["Content-Type"].startswith("text/html")
            assert r.headers.get("Content-Disposition") is None                   # pages open inline
        assert call(base + "/api/research/queue-file?id=APS&f=../queue.yaml")[0] == 404
        assert call(base + "/api/research/queue-file?id=XYZ&f=pages/fccho_ladder.html")[0] == 404
    finally:
        srv.shutdown()


# ------------------------------------------------------- QUEUE digest ----
def test_queue_digest_findings_and_source_row(tmp_path, monkeypatch):
    from tara_automation import research as R
    from tara_automation.ingest import update_research_source
    from tara_automation.ingest.aps import digest
    F, P = digest.findings(_qd(tmp_path))
    text = " ".join(f["statement"] for f in F)
    assert "4 requests totalling 1,150 MW" in text and "3 not yet in service (1,050 MW)" in text
    assert all(f["topic"] == "interconnection_queue" for f in F)
    assert {p["name"] for p in P} == {"Q900", "Q901", "Q902"}               # pending only
    monkeypatch.setattr(R, "ROOT", tmp_path / "areas")
    a = R.create_area("APS", "APS", "", {}, [], [], "", -7)
    _qd(a.root / "data")
    d = update_research_source(a)
    assert d["source_id"] == "QUEUE" and d["cost_usd"] == 0.0 and a.digest("QUEUE")
    row = next(r for r in a.sources() if r["id"] == "QUEUE")
    assert row["status"] == "active" and row["digested_sha"] == row["sha"]
    assert "QUEUE" not in [r["id"] for r in R.pending(a)]                   # never sent to the model
    update_research_source(a)
    assert [r["id"] for r in a.sources()].count("QUEUE") == 1               # rewritten, not duplicated


# ---------------------------------------------------------------- geo ----
REF = {14000: {"lat": 34.94, "lon": -110.30, "name": "CHOLLA", "kv": 500.0, "sub": "Cholla"},
       14004: {"lat": 32.55, "lon": -111.30, "name": "SAGUARO", "kv": 500.0, "sub": "Saguaro"},
       14002: {"lat": 35.10, "lon": -111.40, "name": "SNVLY", "kv": 230.0, "sub": "Sun Valley"},
       14003: {"lat": 33.60, "lon": -112.40, "name": "TS2", "kv": 230.0, "sub": ""},
       30001: {"lat": 36.16, "lon": -115.15, "name": "COMMRCE", "kv": 230.0, "sub": "Commerce"}}


@pytest.mark.parametrize("poi,how,bus", [
    ("Cholla 500kV Substation", "bus", 14000),
    ("Sun Valley Substation 230kV", "bus", 14002),        # matched on the substation name, not the bus name
    ("Future TS-25 230kV Substation", None, None),         # TS-25 is not TS2
    ("Commerce Substation", None, None),                   # Las Vegas bus: outside APS territory
    ("Freedom 230kV Substation, BUS ID: 14003", "written", 14003),
    ("Saguaro - Cholla 500kV", "line_tap", None),
    ("Freedom Valley 230kV (33.41, 115.49)", None, None),  # written coordinates in California: not trusted
])
def test_geo_locate(poi, how, bus):
    from tara_automation.ingest.aps import geo
    loc = geo.locate(poi, {}, REF)
    assert (loc or {}).get("how") == how
    if bus:
        assert loc["bus"] == bus
    if how == "line_tap":
        assert loc["lat"] == pytest.approx((34.94 + 32.55) / 2)


def test_geo_confirmed_bus_wins():
    from tara_automation.ingest.aps import geo
    pmap = {library.poi_key("Cholla 500kV"): {"poi_text": "Cholla 500kV", "bus": "14004"}}
    assert geo.locate("Cholla 500kV", pmap, REF)["how"] == "confirmed"
    assert geo.locate("Cholla 500kV", pmap, REF)["bus"] == 14004           # the person's bus, not the name match


# ------------------------------------------------- grid-based locations ----
GRID = {"subs": [{"n": "Cholla", "kv": 500.0, "kvmin": 230.0, "o": "APS", "p": "In Service", "s": "AZ", "cty": "Navajo", "ll": [34.94, -110.30]},
                 {"n": "Saguaro", "kv": 500.0, "kvmin": 115.0, "o": "APS", "p": "In Service", "s": "AZ", "cty": "Pinal", "ll": [32.55, -111.30]},
                 {"n": "Saguaro", "kv": 230.0, "kvmin": 230.0, "o": "NVE", "p": "In Service", "s": "NV", "cty": "Clark", "ll": [36.1, -115.1]}],
        "lines": [{"n": "Saguaro to Cholla 500 kV", "kv": 500.0, "f": "Saguaro", "t": "Cholla", "p": "In Service",
                   "c": [[32.55, -111.30], [33.7, -110.8], [34.94, -110.30]]}],
        "planned": [{"name": "Bianco", "aliases": "", "kv": "500;230", "lat": "32.9", "lon": "-111.5", "source": "TYP 2026", "note": ""},
                    {"name": "Watermelon", "aliases": "", "kv": "", "lat": "", "lon": "", "source": "", "note": ""}]}


@pytest.mark.parametrize("label,ends", [
    ("Add 5-ohm series reactor to Ocotillo-Kyrene 230 kV Line", (["Ocotillo", "Kyrene"], 230.0)),
    ("Upgrade WAPA’s Saguaro-Oracle 115kv line, 19 miles", (["Saguaro", "Oracle"], 115.0)),
    ("Reconductor Q388Q392 SWST-Tat Momoli-Santa Rosa 230 kV line, 40 miles", (["Q388", "Tat Momoli", "Santa Rosa"], 230.0)),
    ("(2) 2-Ohm series reactor on both Liberty-Freedom 230 kV lines", (["Liberty", "Freedom"], 230.0)),
    ("Sitgreaves 500 kV station", None),
])
def test_upgrade_label_endpoints(label, ends):
    from tara_automation.ingest.aps import geo
    assert geo._endpoints(label) == ends


def test_grid_first_planned_subs_and_line_geometry():
    from tara_automation.ingest.aps import geo
    geo._cache.clear()
    loc = geo.locate("Cholla 230kV", {}, REF, GRID)
    assert loc["how"] == "grid" and loc["lat"] == 34.94                        # 230 kV is inside Cholla's 230-500 kV range
    assert geo.locate("Saguaro 500kV", {}, REF, GRID)["lon"] == -111.30       # not the Nevada Saguaro
    tap = geo.locate("Saguaro - Cholla 500kV", {}, REF, GRID)
    assert tap["how"] == "line_tap" and tap["line"] == GRID["lines"][0]["c"]   # on the grid line, not a straight midpoint
    g = geo.upgrade_geom("Reconductor Saguaro-Cholla 500 kV line", GRID, REF)
    assert g["how"] == "grid" and g["c"] == GRID["lines"][0]["c"]
    g = geo.upgrade_geom("New Saguaro-Bianco 500 kV line, 50 miles", GRID, REF)
    assert g["how"] == "straight" and g["c"][1] == [32.9, -111.5]               # Bianco from planned_subs.csv
    assert geo.upgrade_geom("New Watermelon-Cholla 500 kV line", GRID, REF) == {"kind": "unplaced", "need": ["Watermelon"], "kv": 500.0}
    g = geo.upgrade_geom("Reconductor Q900-Cholla 500 kV line", GRID, REF, {"Q900": {"lat": 33.0, "lon": -111.0}})
    assert g["c"][0] == [33.0, -111.0]                                          # a project's switching station


GRID_S = {**GRID,
          "subs": GRID["subs"] + [{"n": "Coronado", "kv": 500.0, "kvmin": 500.0, "o": "SRP", "p": "In Service", "s": "AZ", "cty": "Apache", "ll": [34.58, -109.27]}],
          "lines": GRID["lines"] + [
              {"n": "Cholla to Coronado 500 kV", "kv": 500.0, "f": "Cholla", "t": "Coronado", "p": "In Service", "c": [[34.94, -110.30], [34.58, -109.27]]},
              {"n": "Tap to Cholla 500 kV", "kv": 500.0, "f": "Tap", "t": "Cholla", "p": "In Service", "c": [[35.2, -110.6], [34.94, -110.30]]}]}


def test_series_devices_are_points_at_line_midpoints():
    from tara_automation.ingest.aps import geo
    geo._cache.clear()
    sag = GRID_S["lines"][0]
    # on an existing grid line: point at the midpoint along its route, the line kept for the highlight
    g = geo.upgrade_geom("Saguaro–Cholla 500 kV series cap upgrade", GRID_S, REF)
    assert g["kind"] == "point" and g["how"] == "series_line" and g["device"] == "series capacitor"
    assert g["c"] == [geo._mid(sag["c"])] and g["line"] == sag["c"] and g["of"] == sag["n"]
    g = geo.upgrade_geom("Add 5-ohm series reactor to Saguaro-Cholla 500 kV Line", GRID_S, REF)
    assert g["device"] == "series reactor" and g["how"] == "series_line"
    # no grid line, both ends located: midpoint of the straight segment
    g = geo.upgrade_geom("Bianco–Cholla 500 kV series compensation", GRID_S, REF)
    assert g["how"] == "series_straight" and g["line"] == [[32.9, -111.5], [34.94, -110.30]]
    assert g["c"][0] == pytest.approx([(32.9 + 34.94) / 2, (-111.5 - 110.30) / 2], abs=1e-6)
    # one named substation: the unique line to another place the label names, else at the substation
    g = geo.upgrade_geom("Relocate Cholla series cap to Coronado", GRID_S, REF)
    assert g["how"] == "series_line" and g["of"] == "Cholla to Coronado 500 kV"
    assert g["c"] == [geo._mid(GRID_S["lines"][1]["c"])]
    g = geo.upgrade_geom("Relocate Cholla series cap to Bianco", GRID_S, REF)
    assert g["how"] == "series_at_sub" and g["c"] == [[34.94, -110.30]] and "line" not in g and g["note"]
    # never invented: an unlocated end stays unplaced; a new line that includes series caps is still a line
    assert geo.upgrade_geom("Watermelon–Cholla 500 kV series cap", GRID_S, REF)["kind"] == "unplaced"
    assert geo.upgrade_geom("New Saguaro-Bianco 500 kV line incl. series caps", GRID_S, REF)["kind"] == "line"
    # the desk map keeps drawing series devices as lines
    assert geo.upgrade_geom("Saguaro–Cholla 500 kV series cap upgrade", GRID_S, REF, series=False)["kind"] == "line"


def test_page_carries_map_layer(tmp_path):
    qd = _qd(tmp_path)
    d = {"phases": [{"projects": [{"q": "Q900", "poi": "Cholla 500kV"}], "upgrades": [{"label": "Reconductor Saguaro-Cholla 500 kV line"}]}],
         "unstudied": [], "prior": []}
    queue_, layer = pages._map_data(qd, d)
    assert queue_["Q900"]["status"] == "Phase 1 Cluster Study"
    assert "Cholla 500kV" in layer["pois"] and "upgrades" in layer and "counties" in layer


# ------------------------------------------------- grid by state (the rule) ----
def test_state_outlines_and_point_in_state():
    from tara_automation import grid_layers as G
    assert G.in_state("AZ", -112.07, 33.45)               # Phoenix
    assert G.in_state("NM", -108.48, 36.69)               # Four Corners plant
    assert not G.in_state("AZ", -108.48, 36.69)
    assert G.state_at(-115.15, 36.17) == "NV"             # Las Vegas


def test_area_states_counts_case_area_buses(tmp_path, monkeypatch):
    from tara_automation import bus_coords as BC
    from tara_automation import grid_layers as G
    wb = tmp_path / "wb.xlsx"
    wb.write_bytes(b"x")
    monkeypatch.setattr(BC, "DEFAULT_WORKBOOK", wb)
    monkeypatch.setattr(BC, "CACHE_DIR", tmp_path)
    cache = tmp_path / f"bus_area_state_{BC._sha(wb)[:16]}.csv"
    rows = ["bus,area,lat,lon,state"] + [f"{i},14,33.4,-112.0,AZ" for i in range(8)] + \
        [f"{100 + i},14,36.7,-108.5,NM" for i in range(5)] + ["900,14,36.1,-115.1,NV", "901,20,33.4,-112.0,AZ"]
    cache.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert G.area_states([14]) == {"AZ": 8, "NM": 5}          # NV: one stray bus, below min_buses


# ------------------------------------------- planned / estimated locations ----
def test_planned_rows_match_spellings_kv_and_win_over_line_taps():
    from tara_automation.ingest.aps import geo
    geo._cache.clear()
    grid = dict(GRID, planned=[
        {"name": "Sitgreaves", "aliases": "Sitigraves; Stigreaves 345 kV", "kv": "500/345", "lat": "34.79", "lon": "-110.47",
         "uncertainty_mi": "1", "confidence": "high", "method": "documented: CEC", "source": "x", "note": ""},
        {"name": "HANG1", "aliases": "HANG1 500kV Cut-In; HANGI 500kV line", "kv": "500", "lat": "32.80", "lon": "-113.90",
         "uncertainty_mi": "37", "confidence": "low", "method": "estimate: on_line_midpoint", "source": "", "note": ""}])
    loc = geo.locate("Sitigraves 500kV", {}, REF, grid)
    assert loc["how"] == "planned" and loc["lat"] == 34.79 and loc["unc"] == 1.0
    assert geo.locate("Stigraves 230kV", {}, REF, grid) is None or geo.locate("Stigraves 230kV", {}, REF, grid)["how"] != "planned"  # wrong kV
    tap = geo.locate("HANG1 500kV Cut-In", {}, REF, grid)                     # a line tap, but listed: planned wins
    assert tap["how"] == "planned" and tap["unc"] == 37.0 and "estimated" in tap["label"]
    g = geo.upgrade_geom("New Sitgreaves-Cholla 500 kV line", grid, REF)
    assert g["kind"] == "line" and g["c"][0] == [34.79, -110.47]


# ------------------------------------------------- contingent facilities ----
@pytest.mark.parametrize("key", ["dsr", "fccho", "gila"])
def test_contingent_files_are_well_formed(key):
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "research/areas/APS/data/queue/contingent" / f"{key}.json"
    if not p.exists():
        pytest.skip("needs the APS queue data (research/areas/APS)")
    c = json.loads(p.read_text(encoding="utf-8"))
    ids = [col["id"] for col in c["columns"]]
    assert ids and len(ids) == len(set(ids)) and all(col["label"] and "isd" in col for col in c["columns"])
    assert c["source"] and c["status"]
    for f, st in c["status"].items():
        assert f.endswith(".pdf") and set(st) <= set(ids) and set(st.values()) <= {"C", "M", "X", "CX"}
    for fid, own in c.get("owners", {}).items():
        assert fid in ids
    if key == "fccho":                                     # every FCCHO cluster report is covered
        assert len(c["status"]) == 7 and all("FcCho" in f for f in c["status"])
        assert {"fcm2", "chpp", "sgli"} <= set(ids)
        assert all(st.get("fcm2") == "C" for st in c["status"].values())
    if key == "gila":                                      # every Gila Bend cluster report is covered
        lad = Path(__file__).resolve().parents[1] / "research/areas/APS/data/queue/ladders/gila.json"
        if lad.exists():                                   # status keys must be the ladder's report files
            assert set(c["status"]) == {p["file"] for p in json.loads(lad.read_text(encoding="utf-8"))["phases"]}
        assert len(c["status"]) == 8 and all("GilaBend" in f for f in c["status"])
        assert {"ts21", "pf1", "hazen", "pandabh", "lrsr"} <= set(ids)
        assert all(st.get("ts21") in ("C", "M") for st in c["status"].values())


def test_desk_area_summary_lists_contingent_facilities(tmp_path):
    from tara_automation.ingest.aps import desk
    qd = _qd(tmp_path)
    qd.contingent.mkdir(parents=True, exist_ok=True)
    (qd.contingent / "fccho.json").write_text(json.dumps({
        "columns": [{"id": "a", "label": "Line A", "isd": "2029"}, {"id": "b", "label": "Line B", "isd": ""}],
        "status": {"r1.pdf": {"a": "M", "b": "X"}, "r2.pdf": {"a": "C"}}, "source": "t"}), encoding="utf-8")
    phases = [{"file": "r1.pdf", "label": "Ph1", "date": "2025-01-01"}, {"file": "r2.pdf", "label": "Ph2", "date": "2026-01-01"}]
    cf = desk._contingent(qd, "fccho", phases)
    assert [c["id"] for c in cf] == ["a", "b"]
    assert cf[0]["latest_status"] == "C" and cf[0]["latest_phase"] == "Ph2" and cf[0]["contingent_in"] == ["Ph2"]
    assert cf[1]["latest_status"] == "X" and cf[1]["contingent_in"] == []
    assert desk._contingent(qd, "dsr", phases) == []


# ------------------------------------------------ contracted (offtake) ----
CONTRACTED_HEADER = "q,project_name,developer,mw,tech,offtaker,contract_type,contract_status,class,confidence,evidence,source_url,note\n"


def _contracted(qd: QueueData, body: str):
    qd.contracted.write_text(CONTRACTED_HEADER + body, encoding="utf-8")


def test_contracted_absent_valid_and_bad_rows(tmp_path):
    from tara_automation.ingest.aps import contracted
    qd = _qd(tmp_path)
    assert contracted.read(qd.contracted) == ({}, []) and contracted.load(qd) == {}
    _contracted(qd, "Q900,Sun A,Dev,250,Storage,APS,PPA,executed,aps_ppa,high,ACC filing,https://x/1,\n"
                    "901,Sun B,Dev,300,Solar,APS,,selected,APS_SELECTED,Medium,RFP shortlist,,\n"
                    ",Unmatched,Dev,100,Solar,SRP,PPA,,other_offtaker,high,,,\n"
                    "Q902,Wind C,Dev,500,Wind,?,,,contracted,high,,,\n"
                    "Q903,Old,Dev,100,Solar,APS,,,aps_owned,certain,,,\n"
                    "Q900,dup,Dev,250,Storage,APS,PPA,,aps_owned,high,,,\n"
                    " SPI01 ,Old PV,APS,10,Solar,APS,,In service,aps_owned,high,,,\n"
                    "Q236/272,Old gas,APS,100,Gas,APS,,Announced,aps_owned,medium,,,\n")
    m, problems = contracted.read(qd.contracted)
    assert set(m) == {"Q900", "Q901", "SPI01", "Q236/272"}             # exact ids; '901' -> Q901, blank q ignored
    assert m["Q236/272"]["announced"] is True and m["Q900"]["announced"] is False
    assert m["Q900"]["class"] == "aps_ppa" and m["Q900"]["offtaker"] == "APS" and m["Q900"]["source_url"] == "https://x/1"
    assert m["Q901"]["class"] == "aps_selected" and m["Q901"]["confidence"] == "medium"
    assert len(problems) == 3 and "class 'contracted'" in problems[0] and "confidence" in problems[1] and "twice" in problems[2]
    qd.contracted.write_text("q,mw\nQ900,1\n", encoding="utf-8")
    m, problems = contracted.read(qd.contracted)
    assert m == {} and "missing column" in problems[0]


def test_desk_view_includes_contracted_totals(tmp_path):
    from tara_automation.ingest.aps import desk
    qd = _qd(tmp_path)
    v = desk.view(qd)                                                    # no file: no highlight, no error
    assert v["contracted"]["present"] is False and v["contracted"]["rows"] == 0 and v["contracted"]["pending"] == {}
    assert all(r["offtake"] is None for r in v["requests"])
    _contracted(qd, "Q900,A,D,250,Storage,APS,PPA,executed,aps_ppa,high,e,,\n"
                    "Q901,B,D,300,Solar,APS,,announced,aps_likely,low,e,,\n"
                    "Q903,C,D,100,Solar,APS,BTA,in service,aps_owned,high,e,,\n"
                    "Q999,D,D,50,Solar,APS,,,aps_ppa,high,e,,\n")
    v = desk.view(qd)
    c = v["contracted"]
    assert c["present"] and c["rows"] == 4 and c["not_in_queue"] == ["Q999"] and c["announced"] == ["Q901"]
    assert c["pending"] == {"aps_ppa": {"n": 1, "mw": 250.0}, "aps_likely": {"n": 1, "mw": 300.0}}   # Q903 in service
    assert c["in_service"] == {"aps_owned": {"n": 1, "mw": 100.0}}      # counted apart from pending
    assert c["all"]["aps_owned"] == {"n": 1, "mw": 100.0}
    fc = next(a for a in v["areas"] if a["key"] == "fccho")
    assert fc["contracted"] == c["pending"] and fc["contracted_in_service"] == c["in_service"]
    q900 = next(r for r in v["requests"] if r["FERC Q Number"] == "Q900")
    assert q900["offtake"]["class"] == "aps_ppa" and q900["offtake"]["label"] == "APS PPA / tolling"
    assert next(r for r in v["requests"] if r["FERC Q Number"] == "Q902")["offtake"] is None


def test_page_data_carries_contracted(tmp_path):
    qd = _qd(tmp_path)
    _deliver(qd)
    qd.ladders.mkdir(parents=True)
    (qd.ladders / "fccho.json").write_text(json.dumps(ladder.build(qd, "Four Corners-Cholla", "FcCho")), encoding="utf-8")
    d = pages.page_data(qd, "fccho")
    assert d["contracted"] == {} and d["contracted_meta"]["present"] is False
    _contracted(qd, "Q905,X,D,500,Solar,APS,PPA,executed,aps_ppa,high,ACC decision,https://x/2,\nQ900,Y,D,250,Storage,APS,,,bogus,high,,,\n")
    d = pages.page_data(qd, "fccho")
    assert set(d["contracted"]) == {"Q905"} and d["contracted"]["Q905"]["confidence"] == "high"
    assert len(d["contracted_meta"]["problems"]) == 1 and d["contracted_meta"]["classes"]["aps_ppa"] == "APS PPA / tolling"
    html = pages.render(qd, "fccho").read_text(encoding="utf-8")
    assert '"contracted": {"Q905"' in html and 'value="offtake"' in html


def test_digest_cites_contracted_mw(tmp_path):
    from tara_automation.ingest.aps import digest
    qd = _qd(tmp_path)
    F, _ = digest.findings(qd)
    assert not any("offtake" in f["statement"] for f in F)
    _contracted(qd, "Q900,A,D,250,Storage,APS,PPA,executed,aps_ppa,high,e,,\nQ902,B,D,500,Wind,PNM,PPA,,other_offtaker,medium,e,,\n"
                    "Q903,C,D,100,Solar,APS,BTA,in service,aps_owned,high,e,,\n")
    F, _ = digest.findings(qd)
    f, f2 = [f for f in F if "offtake" in f["statement"]]
    assert "already in service (100 MW)" in f2["statement"]
    assert "2 pending requests (750 MW)" in f["statement"] and "APS PPA / tolling 1 / 250 MW" in f["statement"]
    assert "Four Corners-Cholla" in f["statement"] and f["location"].startswith("contracted.csv")
