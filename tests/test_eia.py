"""Task 29, pillar 2: EIA load and dispatch statistics (EIA API faked)."""

from __future__ import annotations

import io
import json
import urllib.parse
from datetime import datetime, timedelta

import pytest

from tara_automation import eia
from tara_automation import research as R

ROUTES = {
    "electricity/rto/region-data": ("hourly", ["respondent", "type"]),
    "electricity/rto/daily-fuel-type-data": ("daily", ["respondent", "fueltype", "timezone"]),
    "electricity/rto/fuel-type-data": ("hourly", ["respondent", "fueltype"]),
    "electricity/operating-generator-capacity": ("monthly", ["balancing_authority_code"]),
}


def make_data(now: datetime):
    region, fuel_h, fuel_d = [], [], []
    t = datetime(2024, 1, 1)
    while t < datetime(2026, 1, 1):
        utc = t + timedelta(hours=7)                     # local = UTC - 7
        base = 3000 + 1500 * (t.month in (6, 7, 8)) + 400 * (14 <= t.hour <= 19)
        d = base * (1.05 if t.year == 2025 else 1.0)
        if datetime(2025, 7, 15, 15) <= t <= datetime(2025, 7, 15, 19):
            d = 7000 if t.hour == 17 else 6900            # the 2025 peak: an evening plateau
        if t == datetime(2025, 2, 13, 16):
            d = 10500                                     # an isolated EIA-930 spike: dropped
        if t == datetime(2024, 3, 1, 3):
            d = 0                                         # a bad hour
        p = utc.strftime("%Y-%m-%dT%H")
        region += [{"period": p, "type": "D", "value": d}, {"period": p, "type": "NG", "value": d + 200},
                   {"period": p, "type": "TI", "value": 200}]
        if t >= now - timedelta(days=372):
            fuel_h += [{"period": p, "fueltype": "NG", "type-name": "Natural Gas", "value": d * 0.6},
                       {"period": p, "fueltype": "SUN", "type-name": "Solar", "value": d * 0.4}]
        t += timedelta(hours=1)
    for day in range(731):
        dd = (datetime(2024, 1, 1) + timedelta(days=day)).strftime("%Y-%m-%d")
        fuel_d += [{"period": dd, "fueltype": "NUC", "type-name": "Nuclear", "timezone": "Arizona", "value": 60000},
                   {"period": dd, "fueltype": "NG", "type-name": "Natural Gas", "timezone": "Arizona", "value": 40000},
                   {"period": dd, "fueltype": "NG", "type-name": "Natural Gas", "timezone": "Pacific", "value": 1}]
    gens = [{"period": "2026-08", "technology": "Solar Photovoltaic", "nameplate-capacity-mw": "300"},
            {"period": "2026-08", "technology": "Batteries", "nameplate-capacity-mw": "200"},
            {"period": "2026-08", "technology": "Solar Photovoltaic", "nameplate-capacity-mw": "100"}]
    return {"electricity/rto/region-data": region, "electricity/rto/daily-fuel-type-data": fuel_d,
            "electricity/rto/fuel-type-data": fuel_h, "electricity/operating-generator-capacity": gens}


class FakeEIA:
    """Answers metadata and paged data requests like api.eia.gov/v2."""

    def __init__(self, now, drop_facet=None):
        self.data = make_data(now)
        self.drop = drop_facet
        self.calls = 0

    def __call__(self, req, timeout=0):
        self.calls += 1
        u = urllib.parse.urlparse(req.full_url)
        q = urllib.parse.parse_qs(u.query)
        assert q["api_key"] == ["TEST"]
        route = u.path.removeprefix("/v2/")
        if route.endswith("/data"):
            rows = self.data[route[:-5]]
            if "start" in q and route.startswith("electricity/operating"):
                rows = [r for r in rows if r["period"] == q["start"][0]]
            off, n = int(q.get("offset", ["0"])[0]), int(q.get("length", ["5000"])[0])
            body = {"response": {"total": str(len(rows)), "data": rows[off:off + n]}}
        else:
            freq, facets = ROUTES[route]
            body = {"response": {"frequency": [{"id": freq}],
                                 "facets": [{"id": f} for f in facets if f != self.drop],
                                 "data": {"value": {}, "nameplate-capacity-mw": {}, "net-summer-capacity-mw": {}}}}
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def area(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ROOT", tmp_path / "areas")
    monkeypatch.setenv("EIA_API_KEY", "TEST")
    monkeypatch.setattr(eia.time, "sleep", lambda s: None)
    return R.create_area("APS", "APS", footprint={"areas": [14]}, eia_ba="azps")


def test_pull_compute_and_cite(area):
    now = datetime(2026, 1, 1)
    fake = FakeEIA(now)
    meta = eia.pull(area.root, "AZPS", -7, years=2, opener=fake, progress=lambda *_: None, now=now)
    assert meta["rows"]["region_hourly"] == 3 * 8784 + 3 * 8760 and fake.calls > 10      # paged
    st = eia.statistics_for(*eia.load_raw(area.root))
    y24, y25 = st["annual"]
    assert st["bad_hours_dropped"] == 2                                                  # the zero and the spike
    assert y25["peak_mw"] == 7000 and y25["peak_time"] == "2025-07-15 17:00"              # local time
    # +5 % load, but 2024 is a leap year (8,784 hours against 8,760): about +4.7 %
    assert y25["energy_growth"] == pytest.approx(1.05 * 8760 / 8784 - 1, abs=0.002)
    assert y25["interchange_gwh"] == pytest.approx(8760 * 0.2, abs=1)
    mix = {m["year"]: m for m in st["fuel_mix"]}[2025]["by_fuel"]
    assert mix["NUC"]["share"] == pytest.approx(0.6, abs=0.001)                           # Arizona rows only
    assert st["peak_days"]["days"][0]["date"] == "2025-07-15"
    assert st["peak_days"]["at_peak_mw"]["SUN"] > 0
    assert st["capacity"][0] == {"technology": "Solar Photovoltaic", "units": 2, "nameplate_mw": 400.0}
    d = eia.write_area_source(area, st)
    assert any("peak 7,000 MW" in f["statement"] for f in d["findings"])
    assert area.sources()[0]["id"] == "EIA" and R.pending(area) == []                    # nothing to read
    assert R.coverage(area)["load_and_dispatch"]["findings"] >= 3


def test_route_change_stops_with_a_clear_message(area):
    fake = FakeEIA(datetime(2026, 1, 1), drop_facet="type")
    with pytest.raises(eia.EiaError, match="no longer offers: facet type"):
        eia.pull(area.root, "AZPS", opener=fake, progress=lambda *_: None)


def test_without_a_key_the_ba_workbook_is_used(area, monkeypatch):
    """No EIA key: load and dispatch still work, from EIA's published workbook
    (download faked here; tests never touch the network)."""
    monkeypatch.delenv("EIA_API_KEY")
    assert eia.key_status()[0] is True
    seen = {}

    def fake_download(raw, ba, progress=print):
        seen["ba"] = ba
        raise eia.EiaError("offline in tests")
    monkeypatch.setattr(eia, "download_workbook", fake_download)
    with pytest.raises(eia.EiaError, match="offline"):
        eia.update(area, progress=lambda *_: None)
    assert seen["ba"] == "AZPS"
