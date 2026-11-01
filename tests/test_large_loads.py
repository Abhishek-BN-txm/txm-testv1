"""Large loads tab: MW parsing, status classes, positioning rules, links and the area_view key."""

from __future__ import annotations

import csv
import json

import pytest

from tara_automation import large_loads as LL
from tara_automation import research as R
from tara_automation.ingest.aps import geo


@pytest.mark.parametrize("text, mw, hi, approx", [
    ('~4,500 MW committed ("4.5-plus GW")', 4500, None, True),
    ("~18-20 GW (18 GW / ~30 DCs Oct 2025)", 18000, 20000, True),
    ("up to 1,200 MW", 1200, None, True),
    ("225 MW critical (STACK web)", 225, None, False),
    ("Phase 1 ~200 MW (APS, 2022); >1 GW prior commitment", 200, None, True),
    ("240 MW IT at full build (5 x 48 MW)", 240, None, False),
    ("Not public", None, None, False),
    ("Data center MW not public; ~700 MW on-site gas generation", None, None, False),
    ("", None, None, False),
    ("a gigawatt", None, None, False),
])
def test_parse_mw(text, mw, hi, approx):
    r = LL.parse_mw(text)
    assert r["mw"] == mw and r["mw_hi"] == hi
    if mw is not None:
        assert r["approx"] is approx


def test_generation_figure_is_not_load():
    assert LL.parse_mw("~700 MW on-site gas generation")["mw"] is None


@pytest.mark.parametrize("status, conf, cls", [
    ("Operating/expanding", "", "operating"),
    ("Operating + under construction; add-on not yet committed", "", "operating"),
    ("Under construction (3 buildings broke ground)", "", "committed"),
    ("Committed; in build-out/ramping", "", "committed"),
    ("Uncommitted; no new commitments since 2024", "", "speculative"),
    ("Conceptual; P&Z recommended approval", "low", "speculative"),
    ("Zoning approved Sept 2026", "low-medium (speculative; not committed)", "speculative"),
    ("Planned", "low", "planned"),
    ("Proposed; no contract filed", "", "planned"),
])
def test_status_class(status, conf, cls):
    assert LL.status_class(status, conf) == cls


def test_serving_subs():
    assert LL.serving_subs("Runway 230 kV (on Microsoft site); Diamond 230 kV link (2027)") == [("Runway", 230.0, False), ("Diamond", 230.0, False)]
    s = LL.serving_subs("Existing APS on-site substation; likely Contrail 230 kV (SE corner) - inferred")
    assert s == [("Contrail", 230.0, True)]
    assert LL.serving_subs("APS substation on 12 acres of site (name not public)") == []
    assert LL.serving_subs("Not public") == []


PLANNED = [{"name": "Runway", "aliases": "", "kv": "230/69", "lat": "33.4", "lon": "-112.376", "uncertainty_mi": "1",
            "confidence": "medium", "method": "estimated: test", "source": "", "note": ""},
           {"name": "Wafer", "aliases": "", "kv": "500/230", "lat": "33.788", "lon": "-112.165", "uncertainty_mi": "1",
            "confidence": "high", "method": "documented: test", "source": "", "note": ""},
           {"name": "Freedom", "aliases": "", "kv": "230/69", "lat": "33.4067", "lon": "-112.4961", "uncertainty_mi": "1",
            "confidence": "high", "method": "documented: test", "source": "", "note": ""}]


@pytest.fixture
def grid():
    geo._cache.clear()
    g = {"subs": [{"n": "(City of) Phoenix", "kv": 0, "kvmin": 0.0, "p": "In Service", "ll": [33.19, -112.65]},
                  {"n": "Morgan", "kv": 500.0, "kvmin": 230.0, "p": "In Service", "ll": [33.8167, -112.2622]}],
         "lines": [{"n": "Morgan to Wafer 500 kV", "kv": 500.0, "f": "Morgan", "t": "Wafer",
                    "c": [[33.8167, -112.2622], [33.80, -112.2], [33.788, -112.165]]}],
         "planned": PLANNED}
    yield g
    geo._cache.clear()


def row(**kw):
    base = {"project": "X", "owner_developer": "Acme", "city_area": "Goodyear", "nearest_or_serving_substation": "",
            "mw_as_stated": "", "status": "Planned", "utility": "APS", "confidence": "", "loads_served": "",
            "source_urls": ""}
    base.update(kw)
    return base


def test_load_at_found_substation(grid):
    g = LL.locate_load(row(nearest_or_serving_substation="Runway 230 kV (on site)"), None, grid, {})
    assert g["how"] == "substation" and g["sub"] == "Runway" and (g["lat"], g["lon"]) == (33.4, -112.376)
    assert g["est"] is True and g["unc_mi"] == 1.0          # planned_subs row is an estimate
    g = LL.locate_load(row(nearest_or_serving_substation="Wafer 500/230 kV"), None, grid, {})
    assert g["how"] == "substation" and g["est"] is False


def test_inferred_substation_is_estimated(grid):
    g = LL.locate_load(row(nearest_or_serving_substation="Existing on-site; likely Wafer 230 kV - inferred"), None, grid, {})
    assert g["how"] == "substation" and g["inferred"] and g["est"]


def test_city_centre_fallback_and_not_placed(grid):
    hint = {"substations": "Diamond@230", "how": "city centre", "place": "Avondale, AZ", "lat": "33.43361",
            "lon": "-112.34972", "unc_mi": "4", "source": "https://en.wikipedia.org/wiki/Avondale,_Arizona"}
    g = LL.locate_load(row(nearest_or_serving_substation="Diamond 230 kV (new)"), hint, grid, {})
    assert g["how"] == "city centre" and g["est"] and g["unc_mi"] == 4.0 and "Diamond" in g["why"]
    g = LL.locate_load(row(nearest_or_serving_substation="Not public"), None, grid, {})
    assert g["how"] == "not placed" and "lat" not in g


def test_aggregate_and_other_utility_never_placed(grid):
    hint = {"lat": "33.4", "lon": "-112.3", "how": "city centre"}
    assert LL.locate_load(row(project="APS committed load - aggregate"), hint, grid, {})["how"] == "not placed"
    assert LL.locate_load(row(utility="SRP"), hint, grid, {})["how"] == "not placed"
    assert LL.locate_load(row(utility="APS (likely; not stated)"), hint, grid, {})["how"] == "city centre"


def test_kv_keeps_name_match_honest(grid):
    # 'Phoenix' would match '(City of) Phoenix' without a kV; with 230 kV it must not
    assert LL.find_sub("West Phoenix", [230.0], grid, {}) is None


def test_tx_follows_grid_route_or_straight_dashed(grid):
    t = {"facility": "Morgan-Wafer 500 kV #2", "kv": "500/230", "endpoints_substations": "Morgan - Wafer"}
    g = LL.locate_tx(t, {"lines": "Morgan@500 > Wafer"}, grid, {})
    assert g["how"] == "line" and g["segments"][0]["route"] == "grid" and len(g["segments"][0]["c"]) == 3
    g = LL.locate_tx(t, {"lines": "Freedom > Contrail > Wafer"}, grid, {})
    sg = g["segments"][0]
    assert sg["route"] == "straight" and sg["est"] and sg["via"] == ["Contrail"] and g["missing"] == ["Contrail"]
    g = LL.locate_tx({"facility": "Halo Vista substation", "kv": "Not public", "endpoints_substations": ""},
                     {"substations": "Halo Vista"}, grid, {})
    assert g["how"] == "not placed" and "Halo Vista" in g["why"]
    g = LL.locate_tx({"facility": "Wafer substation", "kv": "500/230", "endpoints_substations": ""},
                     {"substations": "Wafer"}, grid, {})
    assert g["how"] == "point" and g["points"][0]["name"] == "Wafer"


def test_tx_links():
    loads = [row(project="MS Goodyear", owner_developer="Microsoft", city_area="Goodyear (MC-85)",
                 nearest_or_serving_substation="Runway 230 kV"),
             row(project="MS El Mirage", owner_developer="Microsoft", city_area="El Mirage, 12901 W Olive"),
             row(project="Tonopah campus", owner_developer="Arizona Land Consulting", city_area="Tonopah")]
    txs = [{"facility": "Diamond 230 kV + Runway-Diamond link", "loads_served": "STACK (Diamond); Microsoft Goodyear (Runway)"},
           {"facility": "Contrail 230 kV", "loads_served": "data center in El Mirage (likely Microsoft El Mirage)"},
           {"facility": "Wafer", "loads_served": "TSMC Arizona"}]
    assert LL.tx_links(loads, txs) == {0: [0], 1: [1]}


def test_parse_notes():
    md = ("# APS large loads (2026-10-22)\n\n## Totals\n| Bucket | MW |\n|---|---|\n"
          "| New data-center commitments since 2024-01-01 | 0 | APS says |\n\n## Tariffs and conditions\n"
          "- XHLF rate: 5,000 kW.\n- The rate case proposes 45%.\n\n## Conflicts between sources\n"
          "1. **Peak.** APS says 9,164 MW on 2026-08-02.\n")
    n = LL.parse_notes(md)
    assert n["date"] == "2026-10-22" and n["peak"] == {"mw": 9164.0, "date": "2026-08-02"}
    assert n["commitments"] == {"since": "2024-01-01", "count": "0"}
    assert n["tariff"].startswith("The rate case") and "Conflicts between sources" in n["sections"]


@pytest.fixture
def area(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ROOT", tmp_path / "areas")
    return R.create_area("APS", "Arizona Public Service", "test area", {"areas": [14]}, ["APS"], ["Phoenix"])


def _write(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_area_view_key(area, grid):
    assert R.area_view(area)["large_loads"] is None
    d = area.root / "data" / "large_loads"
    d.mkdir(parents=True)
    _write(d / "large_loads.csv", [
        row(project="APS committed load - aggregate", mw_as_stated="~4,500 MW committed", status="Committed"),
        row(project="Microsoft Goodyear", owner_developer="Microsoft", nearest_or_serving_substation="Runway 230 kV",
            status="Operating", mw_as_stated="Not public", source_urls="https://a.example/x ; https://b.example/y"),
        row(project="Somewhere DC", status="Planned", mw_as_stated="300 MW")])
    _write(d / "tx_for_load.csv", [{"facility": "Runway-Wafer 230 kV", "kv": "230", "endpoints_substations": "Runway - Wafer",
                                    "in_service_year": "2028", "purpose": "Large load", "status": "", "loads_served": "Microsoft Goodyear",
                                    "source_urls": ""}])
    (d / "notes.md").write_text("# notes (2026-10-22)\n\n## Gaps\n- none\n", encoding="utf-8")
    v = R.area_view(area)["large_loads"]
    assert v["geo"] is None and v["headline"]["committed"]["mw"] == 4500
    assert v["loads"][1]["urls"] == ["https://a.example/x", "https://b.example/y"] and v["loads"][1]["tx_idx"] == [0]
    out = LL.build(area.root, "APS", grid, {})
    assert [g["how"] for g in out["loads"]] == ["not placed", "substation", "not placed"]
    assert out["tx"][0]["how"] == "line" and json.loads((d / LL.GEO).read_text(encoding="utf-8"))["counts"]
    v = R.area_view(area)["large_loads"]
    assert v["geo"]["loads"][1]["sub"] == "Runway" and v["stale"] is False
