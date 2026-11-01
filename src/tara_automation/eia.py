"""EIA data for a study area's balancing authority (task 29, pillar 2:
historical load and generation dispatch).

Pulled from EIA's open-data API v2 (https://api.eia.gov/v2/, free key in the
EIA_API_KEY environment variable), for the balancing authority in the area's
area.yaml (`eia_ba`, e.g. AZPS for APS):

  electricity/rto/region-data          hourly demand (D), net generation (NG),
                                       total interchange (TI); EIA-930
  electricity/rto/daily-fuel-type-data daily net generation by fuel; EIA-930
  electricity/rto/fuel-type-data       hourly net generation by fuel, last 12 months
  electricity/operating-generator-capacity
                                       generators in the BA, latest month; EIA-860M

Before pulling, each route's metadata is read and checked (frequency, facets,
data columns): if EIA changed a route, the pull stops with a message naming
what is missing, rather than guessing.

The statistics are computed here, not by the language model: annual energy,
peak (date and local hour), load factor and growth, monthly peaks, generation
mix by fuel, interchange, the dispatch by fuel at the peak hour of the
highest-demand days, and capacity by technology. They are written as the
area's `EIA` source and digest, so the summary cites them like any document,
at no model cost.

Raw downloads are kept in data/eia/raw/ (git-ignored); summary.json is tracked.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.eia.gov/v2/"
KEY_ENV = "EIA_API_KEY"
PAGE = 5000


class EiaError(RuntimeError):
    pass


def api_key() -> str | None:
    return os.environ.get(KEY_ENV) or None


def key_status() -> tuple[bool, str]:
    """EIA load and dispatch works without a key (the BA workbook); the key only
    adds the EIA-860M generator list."""
    if api_key():
        return True, "EIA API key found: the generator list (EIA-860M) is included"
    return True, (f"No EIA API key: load and dispatch come from EIA's published workbook; set {KEY_ENV} "
                  "(free at https://www.eia.gov/opendata/) to add the generator list.")


def _get(route: str, params: dict, key: str, opener=None) -> dict:
    q = urllib.parse.urlencode({**params, "api_key": key}, doseq=True)
    url = f"{API}{route}?{q}"
    opener = opener or urllib.request.urlopen
    last = None
    for attempt in range(3):
        try:
            with opener(urllib.request.Request(url, headers={"User-Agent": "tara-automation"}), timeout=90) as r:
                j = json.loads(r.read().decode("utf-8"))
            if "error" in j:
                raise EiaError(f"EIA {route}: {j['error']}")
            return j
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise EiaError("the EIA API key was rejected") from e
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(2 * (attempt + 1))
    raise EiaError(f"could not reach EIA ({route}): {last}")


def check_route(route: str, key: str, freq: str, facets: list[str], data: list[str], opener=None) -> dict:
    """Read a route's metadata and confirm it still offers what we use."""
    meta = _get(route, {}, key, opener).get("response", {})
    freqs = {f.get("id") for f in meta.get("frequency", [])}
    facs = {f.get("id") for f in meta.get("facets", [])}
    cols = set((meta.get("data") or {}).keys())
    missing = ([f"frequency {freq}"] if freq not in freqs else []) + \
              [f"facet {f}" for f in facets if f not in facs] + [f"data column {d}" for d in data if d not in cols]
    if missing:
        raise EiaError(f"EIA route {route} no longer offers: {', '.join(missing)}. "
                       f"It offers frequencies {sorted(freqs)}, facets {sorted(facs)}, data {sorted(cols)}.")
    return meta


def fetch_all(route: str, key: str, params: dict, progress=print, opener=None) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        j = _get(f"{route}/data", {**params, "offset": offset, "length": PAGE}, key, opener)
        resp = j.get("response", {})
        batch = resp.get("data", [])
        rows += batch
        total = int(resp.get("total") or 0)
        offset += len(batch)
        if not batch or offset >= total:
            break
        progress(f"  {route}: {offset:,} of {total:,} rows")
    return rows


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _local(period: str, offset_h: float) -> datetime:
    """EIA hourly periods are UTC 'YYYY-MM-DDTHH'."""
    return datetime.strptime(period[:13], "%Y-%m-%dT%H") + timedelta(hours=offset_h)


def pull(area_root: Path, ba: str, utc_offset: float = -7.0, years: int = 5, key: str | None = None,
         progress=print, opener=None, now: datetime | None = None) -> dict:
    """Download the raw data for `ba` into area_root/data/eia/raw/."""
    key = key or api_key()
    if not key:
        raise EiaError(key_status()[1])
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    start_year = now.year - years
    raw = area_root / "data" / "eia" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    out = {}

    progress(f"checking EIA routes for {ba}")
    check_route("electricity/rto/region-data", key, "hourly", ["respondent", "type"], ["value"], opener)
    check_route("electricity/rto/daily-fuel-type-data", key, "daily", ["respondent", "fueltype"], ["value"], opener)
    check_route("electricity/rto/fuel-type-data", key, "hourly", ["respondent", "fueltype"], ["value"], opener)
    gen_meta = check_route("electricity/operating-generator-capacity", key, "monthly",
                           ["balancing_authority_code"], ["nameplate-capacity-mw"], opener)

    progress(f"hourly demand, generation, interchange since {start_year}")
    out["region_hourly"] = fetch_all("electricity/rto/region-data", key, {
        "frequency": "hourly", "data[]": ["value"], "facets[respondent][]": [ba],
        "facets[type][]": ["D", "NG", "TI"], "start": f"{start_year}-01-01T00",
        "sort[0][column]": "period", "sort[0][direction]": "asc"}, progress, opener)
    progress("daily generation by fuel")
    out["fuel_daily"] = fetch_all("electricity/rto/daily-fuel-type-data", key, {
        "frequency": "daily", "data[]": ["value"], "facets[respondent][]": [ba],
        "start": f"{start_year}-01-01", "sort[0][column]": "period", "sort[0][direction]": "asc"}, progress, opener)
    progress("hourly generation by fuel, last 12 months")
    out["fuel_hourly"] = fetch_all("electricity/rto/fuel-type-data", key, {
        "frequency": "hourly", "data[]": ["value"], "facets[respondent][]": [ba],
        "start": (now - timedelta(days=372)).strftime("%Y-%m-%dT00"),
        "sort[0][column]": "period", "sort[0][direction]": "asc"}, progress, opener)
    progress("generators in the balancing authority (EIA-860M, latest month)")
    data_cols = [c for c in ("nameplate-capacity-mw", "net-summer-capacity-mw") if c in (gen_meta.get("data") or {})]
    latest = _get("electricity/operating-generator-capacity/data", {
        "frequency": "monthly", "data[]": data_cols, "facets[balancing_authority_code][]": [ba],
        "sort[0][column]": "period", "sort[0][direction]": "desc", "length": 1}, key, opener)
    lp = ((latest.get("response") or {}).get("data") or [{}])[0].get("period")
    out["generators"] = fetch_all("electricity/operating-generator-capacity", key, {
        "frequency": "monthly", "data[]": data_cols, "facets[balancing_authority_code][]": [ba],
        "start": lp, "end": lp}, progress, opener) if lp else []

    for k, rows in out.items():
        with gzip.open(raw / f"{k}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(rows, fh)
    meta = {"ba": ba, "utc_offset": utc_offset, "pulled_at": datetime.now().isoformat(timespec="seconds"),
            "rows": {k: len(v) for k, v in out.items()}, "generators_period": lp}
    (raw / "pull.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    progress(f"downloaded: " + ", ".join(f"{k} {v:,}" for k, v in meta["rows"].items()))
    return meta


def _generators(raw: Path, ba: str, key: str, progress=print, opener=None) -> None:
    """EIA-860M operating generators in the BA, latest month (API key needed)."""
    meta = check_route("electricity/operating-generator-capacity", key, "monthly",
                       ["balancing_authority_code"], ["nameplate-capacity-mw"], opener)
    cols = [c for c in ("nameplate-capacity-mw", "net-summer-capacity-mw") if c in (meta.get("data") or {})]
    latest = _get("electricity/operating-generator-capacity/data", {
        "frequency": "monthly", "data[]": cols, "facets[balancing_authority_code][]": [ba],
        "sort[0][column]": "period", "sort[0][direction]": "desc", "length": 1}, key, opener)
    lp = ((latest.get("response") or {}).get("data") or [{}])[0].get("period")
    rows = fetch_all("electricity/operating-generator-capacity", key, {
        "frequency": "monthly", "data[]": cols, "facets[balancing_authority_code][]": [ba],
        "start": lp, "end": lp}, progress, opener) if lp else []
    with gzip.open(raw / "generators.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(rows, fh)
    (raw / "generators_period.txt").write_text(lp or "", encoding="utf-8")


def load_raw(area_root: Path) -> tuple[dict, dict]:
    raw = area_root / "data" / "eia" / "raw"
    if not (raw / "pull.json").exists():
        raise EiaError("no EIA data downloaded yet")
    meta = json.loads((raw / "pull.json").read_text(encoding="utf-8"))
    gp = raw / "generators_period.txt"
    if gp.exists() and not meta.get("generators_period"):
        meta["generators_period"] = gp.read_text(encoding="utf-8").strip() or None
    data = {}
    for k in ("region_hourly", "fuel_daily", "fuel_hourly", "generators", "interchange_daily"):
        p = raw / f"{k}.json.gz"
        data[k] = json.load(gzip.open(p, "rt", encoding="utf-8")) if p.exists() else []
    return meta, data


def statistics_for(meta: dict, data: dict) -> dict:
    """Everything the desk and the summary show, computed from the raw rows."""
    off = float(meta.get("utc_offset", -7))
    series: dict[str, dict[datetime, float]] = {"D": {}, "NG": {}, "TI": {}}
    for r in data["region_hourly"]:
        t, v = r.get("type"), _num(r.get("value"))
        if t in series and v is not None:
            series[t][_local(r["period"], off)] = v
    # EIA-930 has bad hours: zeros, and isolated spikes (AZPS 2025-02-13 16:00
    # reads 10,529 MW between hours of ~4,200 MW). An hour is dropped when it is
    # <= 0, or differs from the median of the two hours either side by more
    # than 30 % AND more than 1,000 MW (a real ramp moves with its neighbours).
    dem = series["D"]
    bad = []
    for t, v in dem.items():
        nb = [dem[t + timedelta(hours=k)] for k in (-2, -1, 1, 2) if (t + timedelta(hours=k)) in dem]
        if v <= 0:
            bad.append(t)
        elif len(nb) >= 3:
            m = statistics.median(nb)
            if abs(v - m) > 1000 and abs(v - m) > 0.3 * m:
                bad.append(t)
    for t in bad:
        dem.pop(t)
    years = defaultdict(list)
    for t, v in dem.items():
        years[t.year].append((t, v))
    annual = []
    for y in sorted(years):
        pts = years[y]
        peak_t, peak = max(pts, key=lambda x: x[1])
        energy = sum(v for _, v in pts)
        annual.append({"year": y, "hours": len(pts), "energy_gwh": round(energy / 1000, 1),
                       "peak_mw": round(peak), "peak_time": peak_t.strftime("%Y-%m-%d %H:00"),
                       "min_mw": round(min(v for _, v in pts)), "load_factor": round(energy / len(pts) / peak, 3),
                       "complete": len(pts) >= 8700,
                       "net_gen_gwh": round(sum(v for t, v in series["NG"].items() if t.year == y) / 1000, 1),
                       "interchange_gwh": round(sum(v for t, v in series["TI"].items() if t.year == y) / 1000, 1)})
    full = [a for a in annual if a["complete"]]
    for prev, cur in zip(full, full[1:]):
        cur["peak_growth"] = round(cur["peak_mw"] / prev["peak_mw"] - 1, 4)
        cur["energy_growth"] = round(cur["energy_gwh"] / prev["energy_gwh"] - 1, 4)
    months = defaultdict(float)
    for t, v in dem.items():
        k = t.strftime("%Y-%m")
        months[k] = max(months[k], v)
    monthly = [{"month": k, "peak_mw": round(v)} for k, v in sorted(months.items())][-60:]

    # generation mix by fuel, per year (daily rows, MWh)
    names: dict[str, str] = {}
    mix = defaultdict(lambda: defaultdict(float))
    zones = defaultdict(int)
    for r in data["fuel_daily"]:
        zones[r.get("timezone", "")] += 1
    zone = max(zones, key=zones.get) if zones else ""
    for r in data["fuel_daily"]:
        if zone and r.get("timezone", "") != zone:
            continue
        v = _num(r.get("value"))
        if v is None:
            continue
        f = r.get("fueltype", "?")
        names[f] = r.get("type-name") or f
        mix[int(str(r["period"])[:4])][f] += v
    fuel_mix = []
    for y in sorted(mix):
        tot = sum(v for v in mix[y].values() if v > 0)
        fuel_mix.append({"year": y, "total_gwh": round(tot / 1000, 1), "by_fuel": {
            f: {"gwh": round(v / 1000, 1), "share": round(v / tot, 4) if tot else 0}
            for f, v in sorted(mix[y].items(), key=lambda kv: -kv[1])}})

    # dispatch at the peak hour, averaged over the 10 highest-demand days of the last 12 months
    fh = defaultdict(dict)
    for r in data["fuel_hourly"]:
        v = _num(r.get("value"))
        if v is not None:
            f = r.get("fueltype", "?")
            names.setdefault(f, r.get("type-name") or f)
            fh[_local(r["period"], off)][f] = v
    peak_days: dict = {}
    if fh:
        first = min(fh)
        daily_peak = defaultdict(lambda: (None, -1.0))
        for t, v in dem.items():
            if t >= first and v > daily_peak[t.date()][1]:
                daily_peak[t.date()] = (t, v)
        top = sorted(daily_peak.values(), key=lambda x: -x[1])[:10]
        hours = [t for t, _ in top if t in fh]
        profile = defaultdict(lambda: defaultdict(list))        # local hour -> fuel -> [MW]
        for t, _ in top:
            for h in range(24):
                tt = datetime(t.year, t.month, t.day, h)
                for f, v in fh.get(tt, {}).items():
                    profile[h][f].append(v)
        peak_days = {
            "days": [{"date": t.strftime("%Y-%m-%d"), "peak_hour": t.hour, "peak_mw": round(v)} for t, v in top],
            "at_peak_mw": {f: round(statistics.mean(fh[t].get(f, 0) for t in hours))
                           for f in sorted({f for t in hours for f in fh[t]})} if hours else {},
            "hourly_profile": [{"hour": h, **{f: round(statistics.mean(v)) for f, v in profile[h].items()}}
                               for h in range(24) if profile[h]],
        }

    # net interchange with each neighbouring BA, by year (daily rows, MWh; + = export to it)
    inter = defaultdict(lambda: defaultdict(float))
    for r in data.get("interchange_daily", []):
        v = _num(r.get("value"))
        if v is not None:
            inter[int(str(r["period"])[:4])][r.get("toba", "?")] += v
    interchange = [{"year": y, "by_ba": {b: round(v / 1000, 1) for b, v in sorted(inter[y].items(), key=lambda kv: -abs(kv[1]))}}
                   for y in sorted(inter)]

    # capacity by technology (EIA-860M)
    cap = defaultdict(lambda: {"units": 0, "nameplate_mw": 0.0})
    for r in data["generators"]:
        tech = r.get("technology") or r.get("energy_source_code") or "?"
        cap[tech]["units"] += 1
        cap[tech]["nameplate_mw"] += _num(r.get("nameplate-capacity-mw")) or 0
    capacity = sorted(({"technology": k, "units": v["units"], "nameplate_mw": round(v["nameplate_mw"], 1)}
                       for k, v in cap.items()), key=lambda x: -x["nameplate_mw"])
    return {"ba": meta["ba"], "pulled_at": meta["pulled_at"], "utc_offset": off, "bad_hours_dropped": len(bad),
            "annual": annual, "monthly_peaks": monthly, "fuel_names": names, "fuel_mix": fuel_mix,
            "fuel_timezone": zone, "peak_days": peak_days, "capacity": capacity, "interchange_by_ba": interchange,
            "source": meta.get("source", "EIA API v2"),
            "generators_period": meta.get("generators_period"),
            "capacity_total_mw": round(sum(c["nameplate_mw"] for c in capacity), 1)}


def findings(st: dict) -> list[dict]:
    """The statistics as digest findings (topic load_and_dispatch), cited as source EIA."""
    out = []
    loc = "EIA-930 hourly, " + st["ba"]
    for a in st["annual"]:
        g = f"; peak {a['peak_growth']:+.1%} and energy {a['energy_growth']:+.1%} on the year before" \
            if "peak_growth" in a else ""
        part = "" if a["complete"] else f" (partial year, {a['hours']:,} hours)"
        out.append({"topic": "load_and_dispatch", "location": loc, "statement":
                    f"{a['year']}{part}: demand {a['energy_gwh']:,.0f} GWh, peak {a['peak_mw']:,} MW at "
                    f"{a['peak_time']} local, load factor {a['load_factor']:.0%}, net interchange "
                    f"{a['interchange_gwh']:+,.0f} GWh (positive = net export){g}."})
    full = [m for m in st["fuel_mix"] if m["total_gwh"]]
    if full:
        m = full[-2] if len(full) > 1 and full[-1]["year"] == datetime.now().year else full[-1]
        mixs = ", ".join(f"{st['fuel_names'].get(f, f)} {v['share']:.0%}" for f, v in m["by_fuel"].items()
                         if v["share"] >= 0.01)
        out.append({"topic": "load_and_dispatch", "location": "EIA-930 daily by fuel",
                    "statement": f"{m['year']} net generation {m['total_gwh']:,.0f} GWh by fuel: {mixs}."})
    pd = st.get("peak_days")
    if pd and pd.get("at_peak_mw"):
        avg = statistics.mean(d["peak_mw"] for d in pd["days"])
        disp = ", ".join(f"{st['fuel_names'].get(f, f)} {v:,} MW" for f, v in
                         sorted(pd["at_peak_mw"].items(), key=lambda kv: -kv[1]) if abs(v) >= 1)
        out.append({"topic": "load_and_dispatch", "location": "EIA-930 hourly by fuel, last 12 months",
                    "statement": f"On the 10 highest-demand days of the last 12 months (average peak {avg:,.0f} MW, "
                                 f"{pd['days'][0]['date']} highest), generation at the peak hour averaged: {disp}."})
    ib = [x for x in st.get("interchange_by_ba", []) if x["by_ba"]]
    if ib:
        x = ib[-2] if len(ib) > 1 and ib[-1]["year"] == datetime.now().year else ib[-1]
        top = ", ".join(f"{b} {v:+,.0f} GWh" for b, v in list(x["by_ba"].items())[:6])
        out.append({"topic": "load_and_dispatch", "location": "EIA-930 daily interchange by neighbour",
                    "statement": f"{x['year']} net interchange with neighbouring balancing authorities "
                                 f"(+ = export from {st['ba']}): {top}."})
    if st["capacity"]:
        top = ", ".join(f"{c['technology']} {c['nameplate_mw']:,.0f} MW" for c in st["capacity"][:6])
        out.append({"topic": "load_and_dispatch", "location": f"EIA-860M {st.get('generators_period')}",
                    "statement": f"Operating generator nameplate in {st['ba']}: {st['capacity_total_mw']:,.0f} MW "
                                 f"({top})."})
    if st["bad_hours_dropped"]:
        out.append({"topic": "other", "location": loc, "statement":
                    f"{st['bad_hours_dropped']} hours of EIA-930 demand were dropped as bad data (zero, or a spike against "
                    "the hours around it)."})
    return out


def write_area_source(area, st: dict) -> dict:
    """summary.json + the EIA source row and digest, so the summary cites it."""
    from tara_automation import research as R
    d = area.root / "data" / "eia"
    d.mkdir(parents=True, exist_ok=True)
    text = json.dumps(st, indent=1)
    (d / "summary.json").write_text(text, encoding="utf-8")
    sha = hashlib.sha256(text.encode()).hexdigest()[:16]
    years = [a["year"] for a in st["annual"]]
    digest = {"title": f"EIA data for balancing authority {st['ba']}", "publisher": "U.S. EIA (Form 930, Form 860M)",
              "date": st["pulled_at"][:10], "doc_type": "data (computed locally)",
              "summary": (f"Hourly demand, net generation and interchange {years[0]}-{years[-1]}, generation by fuel, "
                          f"and the generator inventory for {st['ba']}, downloaded from the EIA API and summarised "
                          "on this machine.") if years else "No hourly data returned.",
              "relevance": "high", "findings": findings(st), "projects": [], "open_questions": [],
              "source_id": "EIA", "model": "computed (no language model)", "digested_at": st["pulled_at"],
              "usage": {"input": 0, "output": 0, "cache_read": 0}, "cost_usd": 0.0}
    (area.root / "digests").mkdir(exist_ok=True)
    (area.root / "digests" / "EIA.json").write_text(json.dumps(digest, indent=2), encoding="utf-8")
    rows = [r for r in area.sources() if r["id"] != "EIA"]
    rows.insert(0, {"id": "EIA", "kind": "data", "title": digest["title"], "added_by": "eia",
                    "added": st["pulled_at"], "status": "active", "sha": sha, "digested_sha": sha,
                    "publisher": digest["publisher"], "date": digest["date"], "doc_type": "data",
                    "relevance": "high"})
    area.save_sources(rows)
    area.log(f"EIA data for {st['ba']} updated: {len(digest['findings'])} findings (computed, $0)")
    return digest


# ---- no-key route: EIA's published workbook per balancing authority -------
# https://www.eia.gov/electricity/gridmonitor/knownissues/xls/<BA>.xlsx holds
# the whole EIA-930 history for one BA (hourly from July 2015: demand, net
# generation, interchange, EIA's adjusted values, generation by fuel; daily:
# interchange with each neighbouring BA). No API key needed. Verified for AZPS
# on 2026-10-21 (84 MB).
WORKBOOK_URL = "https://www.eia.gov/electricity/gridmonitor/knownissues/xls/{ba}.xlsx"
FUEL_NAMES = {"COL": "Coal", "NG": "Natural gas", "NUC": "Nuclear", "OIL": "Petroleum", "GEO": "Geothermal",
              "WAT": "Hydro", "PS": "Pumped storage", "SUN": "Solar", "SNB": "Solar with battery",
              "WND": "Wind", "WNB": "Wind with battery", "BAT": "Battery storage", "OES": "Other storage",
              "UES": "Unknown storage", "OTH": "Other", "UNK": "Unknown"}


def download_workbook(raw: Path, ba: str, progress=print) -> Path:
    url = WORKBOOK_URL.format(ba=ba)
    dest = raw / f"{ba}.xlsx"
    progress(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (tara-automation)"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r, open(dest.with_suffix(".part"), "wb") as fh:
            while chunk := r.read(1 << 20):
                fh.write(chunk)
    except OSError as e:
        raise EiaError(f"could not download the EIA workbook for {ba}: {e}") from e
    tmp = dest.with_suffix(".part")
    with open(tmp, "rb") as fh:
        if fh.read(2) != b"PK":
            raise EiaError(f"EIA returned something other than a workbook for {ba} (no such BA code?)")
    tmp.replace(dest)
    return dest


def import_workbook(area_root: Path, ba: str, path: Path, utc_offset: float = -7.0, years: int = 10,
                    progress=print) -> dict:
    """Read the BA workbook into the same raw rows the API route produces."""
    import openpyxl
    raw = area_root / "data" / "eia" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    progress(f"reading {path.name} (large: about a minute)")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    hourly, daily = wb["Published Hourly Data"], wb["Published Daily Data"]
    rows = hourly.iter_rows(values_only=True)
    h = list(next(rows))
    ix = {n: h.index(n) for n in ("UTC time", "Local time", "Time zone")}
    pick = lambda name, alt: h.index(name) if name in h else h.index(alt)  # noqa: E731
    cD, cNG, cTI = pick("Adjusted demand", "Demand"), pick("Adjusted net generation", "Net generation"), \
        pick("Adjusted total interchange", "Total interchange")
    fuels = {c[4:]: i for i, c in enumerate(h) if isinstance(c, str) and c.startswith("NG: ")}
    region, fuel_h = [], []
    last = None
    buf = []
    for r in rows:
        t = r[ix["UTC time"]]
        if t is None:
            continue
        last = t
        buf.append(r)
    if last is None:
        raise EiaError(f"{path.name}: no hourly rows")
    start = datetime(last.year - years, 1, 1)
    fuel_from = last - timedelta(days=372)
    zone = ""
    for r in buf:
        t = r[ix["UTC time"]]
        if t < start:
            continue
        zone = r[ix["Time zone"]] or zone
        p = t.strftime("%Y-%m-%dT%H")
        for typ, c in (("D", cD), ("NG", cNG), ("TI", cTI)):
            if r[c] is not None:
                region.append({"period": p, "type": typ, "value": r[c]})
        if t >= fuel_from:
            for f, c in fuels.items():
                if r[c] is not None:
                    fuel_h.append({"period": p, "fueltype": f, "type-name": FUEL_NAMES.get(f, f), "value": r[c]})
    rows = daily.iter_rows(values_only=True)
    dh = list(next(rows))
    dfuels = {c[4:]: i for i, c in enumerate(dh) if isinstance(c, str) and c.startswith("NG: ")}
    neighbours = {c: i for i, c in enumerate(dh) if isinstance(c, str) and c.isupper() and 2 <= len(c) <= 5
                  and c not in ("BA",)}
    fuel_d, inter = [], []
    for r in rows:
        d = r[dh.index("Local date")]
        if d is None or d < start:
            continue
        ds = d.strftime("%Y-%m-%d")
        tz = r[dh.index("Time zone")] or ""
        for f, c in dfuels.items():
            if r[c] is not None:
                fuel_d.append({"period": ds, "fueltype": f, "type-name": FUEL_NAMES.get(f, f), "timezone": tz,
                               "value": r[c]})
        for nb, c in neighbours.items():
            if r[c] is not None:
                inter.append({"period": ds, "toba": nb, "value": r[c]})
    out = {"region_hourly": region, "fuel_daily": fuel_d, "fuel_hourly": fuel_h, "interchange_daily": inter}
    old_gen = raw / "generators.json.gz"
    for k, v in out.items():
        with gzip.open(raw / f"{k}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(v, fh)
    meta = {"ba": ba, "utc_offset": utc_offset, "pulled_at": datetime.now().isoformat(timespec="seconds"),
            "source": f"EIA-930 BA workbook {path.name}", "time_zone": zone,
            "rows": {k: len(v) for k, v in out.items()},
            "generators_period": json.loads((raw / "pull.json").read_text(encoding="utf-8")).get("generators_period")
            if (raw / "pull.json").exists() and old_gen.exists() else None}
    (raw / "pull.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    progress("read: " + ", ".join(f"{k} {v:,}" for k, v in meta["rows"].items()))
    return meta


def update(area, progress=print, opener=None) -> dict:
    """Download (the BA workbook: no key; the generator list too when
    EIA_API_KEY is set), compute, write. Returns a short result."""
    m = area.meta()
    ba = m.get("eia_ba")
    if not ba:
        raise EiaError(f"area {area.id} has no eia_ba (EIA balancing authority code, e.g. AZPS) in area.yaml")
    raw = area.root / "data" / "eia" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    if api_key() and opener is not None:
        pull(area.root, ba, float(m.get("utc_offset", -7)), int(m.get("eia_years", 5)), progress=progress,
             opener=opener)
    else:
        wbp = download_workbook(raw, ba, progress)
        if api_key():
            _generators(raw, ba, api_key(), progress)
        import_workbook(area.root, ba, wbp, float(m.get("utc_offset", -7)), int(m.get("eia_years", 10)), progress)
    meta, data = load_raw(area.root)
    st = statistics_for(meta, data)
    d = write_area_source(area, st)
    progress(f"EIA statistics written: {len(d['findings'])} findings")
    return {"findings": len(d["findings"]), "years": [a["year"] for a in st["annual"]], "cost": 0.0}
