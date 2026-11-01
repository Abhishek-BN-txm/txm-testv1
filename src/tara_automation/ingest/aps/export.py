"""Flat tables of the ladder data (for analysis, scenario building and the study desk).

Writes data/queue/export/:
  generators.csv          one row per queue request per cluster report it appears in (plus serial and 2026 requests)
  network_upgrades.csv    one row per network upgrade per report, with trigger MW
  upgrade_allocations.csv one row per (upgrade, project) cost share
  contingent_facilities.csv  planned transmission each report assumed in service (areas with contingent/<key>.json)
The project library adapter (library.py) is the path into TARA cases; these tables
keep the cost / trigger / contingency context that projects.xlsx has no columns for.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from tara_automation.ingest.aps import AREAS, QueueData

def kv(poi):
    m = re.search(r'(\d{2,3})\s*-?\s*kv', poi or '', re.I)
    return int(m.group(1)) if m else ''

def latest_by_cycle(phases):
    last = {}
    for p in phases: last[(p['cycle'], p.get('sub', ''))] = p['file']
    return set(last.values())

def export(qd: QueueData, out: Path | None = None) -> dict[str, int]:
    """Write the four tables; returns rows per table."""
    out = Path(out or qd.export); out.mkdir(parents=True, exist_ok=True)
    G, U, A, K = [], [], [], []
    for k, (area, _, _) in AREAS.items():
        d = _json((qd.ladders / f'{k}.json'))
        latest = latest_by_cycle(d['phases'])
        for p in d['phases']:
            base = {'area_key': k, 'study_area': area, 'cycle': p['cycle'], 'phase': p['label'], 'report_file': p['file'],
                    'report_date': p['date'], 'is_latest_for_cycle': p['file'] in latest}
            for r in p['projects']:
                G.append({**base, 'stage': 'cluster', 'q': r['q'], 'poi': r['poi'], 'poi_kv': kv(r['poi']), 'mw': r['mw'],
                          'service': r['service'], 'tech': r.get('tech', ''), 'queue_status': r.get('status', ''),
                          'in_active_queue': r.get('in_active_queue'), 'station_nu_usd': r.get('station_nu', 0), 'total_alloc_usd': r.get('total', 0)})
            for u in p['upgrades']:
                U.append({**base, 'upgrade': u['label'], 'category': u.get('category', ''), 'cost_usd': u['cost'],
                          'cost_derived': u.get('cost_derived', False), 'lead_time': u.get('lead', ''), 'first_q': u.get('first_q', ''),
                          'trigger_cum_mw': u.get('trigger_cum_mw'), 'trigger_cum_nris_mw': u.get('trigger_cum_nris_mw'),
                          'n_sharing': u.get('n_sharing'), 'sharing_mw': u.get('sharing_mw')})
                for q, a in u['alloc'].items():
                    A.append({**base, 'upgrade': u['label'], 'q': q, 'pct': a.get('pct'), 'usd': a.get('usd'), 'status': a.get('status', 'required')})
        sf = qd.serial / f'{k}_serial_ladder.json'
        if sf.exists():
            s = _json((sf))
            base = {'area_key': k, 'study_area': area, 'cycle': 'Serial', 'phase': 'Serial', 'report_file': s.get('file', ''),
                    'report_date': s.get('date', ''), 'is_latest_for_cycle': True}
            for r in s.get('projects', []):
                G.append({**base, 'stage': 'serial', 'q': r['q'], 'poi': r.get('poi', ''), 'poi_kv': kv(r.get('poi')), 'mw': r.get('mw'),
                          'service': r.get('service', ''), 'tech': '', 'queue_status': r.get('status', ''), 'in_active_queue': True,
                          'station_nu_usd': r.get('station_nu', 0), 'total_alloc_usd': r.get('total', 0)})
            for u in s.get('upgrades', []):
                U.append({**base, 'upgrade': u['label'], 'category': 'serial NU', 'cost_usd': u.get('cost'), 'cost_derived': u.get('cost_derived', False),
                          'lead_time': '', 'first_q': '', 'trigger_cum_mw': '', 'trigger_cum_nris_mw': '', 'n_sharing': len(u.get('alloc', {})), 'sharing_mw': ''})
                for q, a in u.get('alloc', {}).items():
                    A.append({**base, 'upgrade': u['label'], 'q': q, 'pct': a.get('pct'), 'usd': a.get('usd'), 'status': a.get('status', 'required')})
        uf = qd.ladders / f'{k}_2026.json'
        if uf.exists():
            for r in _json((uf)):
                G.append({'area_key': k, 'study_area': area, 'cycle': '2026', 'phase': 'unstudied', 'report_file': '', 'report_date': '',
                          'is_latest_for_cycle': True, 'stage': 'unstudied', 'q': r['q'], 'poi': r['poi'], 'poi_kv': kv(r['poi']), 'mw': r['mw'],
                          'service': r['service'], 'tech': r['tech'], 'queue_status': r['status'], 'in_active_queue': True,
                          'station_nu_usd': '', 'total_alloc_usd': ''})
        cf = qd.contingent / f'{k}.json'
        if cf.exists():
            c = _json((cf)); files = {p['file']: p for p in d['phases']}
            for col in c['columns']:
                for f, st in c['status'].items():
                    if col['id'] in st:
                        K.append({'area_key': k, 'facility_id': col['id'], 'facility': col['label'], 'planned_isd': col.get('isd', ''),
                                  'report_file': f, 'phase': files.get(f, {}).get('label', f), 'status': st[col['id']], 'owner_q': ''})
                for q, o in (c.get('owners', {}).get(col['id'], {})).items():
                    for f in o.get('phases') or ['']:
                        K.append({'area_key': k, 'facility_id': col['id'], 'facility': col['label'], 'planned_isd': col.get('isd', ''),
                                  'report_file': f, 'phase': files.get(f, {}).get('label', f), 'status': 'owner', 'owner_q': q})
    counts = {}
    for name, rows in [('generators', G), ('network_upgrades', U), ('upgrade_allocations', A), ('contingent_facilities', K)]:
        if not rows: continue
        cols = list(dict.fromkeys(c for r in rows for c in r))
        with open(out / f'{name}.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
        counts[name] = len(rows)
    return counts


def _json(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))
