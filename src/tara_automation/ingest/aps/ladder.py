"""Ladder data for one APS study area: projects in queue order x network upgrades.

For every cluster report of the area (data/deliver/aps_studies.csv) it lists the
projects with their service type and cost, and the network upgrades with each
project's allocated share. The *trigger* of an upgrade is the earliest-queued
project allocated to it, and the cumulative (NRIS) MW at that row is the MW in
the cluster when the upgrade first appears. Cluster allocation is by flow
impact, not queue order, so the trigger is a reading aid, not a serial rule.

Regression-checked against the published ladders.
"""

from __future__ import annotations

import csv
import datetime
import re

from tara_automation.ingest.aps import QueueData


def _iso(stamp: str) -> str:
    return f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}' if re.fullmatch(r'\d{8}', stamp or '') else stamp


def _rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def num(v):
    try: return float(re.sub(r'[^\d.]', '', v or '') or 0)
    except ValueError: return 0.0

def parse_date(s):
    for fmt in ('%m/%d/%y', '%m/%d/%Y'):
        try: return datetime.datetime.strptime((s or '').strip(), fmt).date()
        except ValueError: pass
    return None



def build(qd: QueueData, AREA: str, FILEKEY: str) -> dict:
    """Ladder dict for study area AREA; FILEKEY is a regex on report filenames."""
    # ---- queue info (active queue) ----
    Q = {r['FERC Q Number']: r for r in _rows(qd.queue_clean)}
    A = {r['FERC Q Number']: r for r in _rows(qd.queue_by_area)}

    def qinfo(qid):
        r = Q.get(qid)
        if not r: return {'in_active_queue': False}
        d = parse_date(r.get('Interconnection Request Submission Date')) or parse_date(r.get('Date of Issuance of Queue'))
        return {'in_active_queue': True, 'queue_date': d.isoformat() if d else '', 'status': r['Current Status'],
                'tech': r['Energy Source Type'], 'service_req': r['Interconnection Request Type'], 'poi_queue': r['Requested POI'],
                'mw_queue': num(r['Electrical Output MW'])}

    # ---- studies / phases ----
    studies = [s for s in _rows(qd.deliver / 'aps_studies.csv') if re.search(FILEKEY, s['file']) and 'FAIL' not in (s.get('notes', '') + s.get('study_type', '')).upper()]
    def phase_label(s):
        fn = s['file']
        cyc = s.get('cycle') or ('Transition' if re.search(r'Trans|TC', fn) else '?')
        ph = s.get('phase') or '?'
        return f"{cyc} Ph{ph}", cyc
    def fix_date(field, fn):
        m = re.search(r'\d{4}-\d{2}-\d{2}', field or '')
        d = m.group(0) if m else ''
        fm = re.search(r'(20\d{2})(\d{2})(\d{2})', fn)
        if fm:
            fd = f"{fm.group(1)}-{fm.group(2)}-{fm.group(3)}"
            try:
                if not d or (datetime.date.fromisoformat(fd) - datetime.date.fromisoformat(d)).days > 180: d = fd
            except ValueError: pass
        return d
    phases = []
    for s in studies:
        lab, cyc = phase_label(s)
        sub = 'Redhawk' if 'Redhawk' in s['file'] and 'Del' not in s['file'] else ('Delaney–Sun Valley' if 'Delaney-SunValley' in s['file'] else '')
        if sub: lab = f"{lab} · {sub}"
        phases.append({'file': s['file'], 'label': lab, 'cycle': cyc, 'sub': sub, 'date': fix_date(s.get('report_date', ''), s['file']), 'version': s.get('version', '')})
    order = {'Transition': 0, '2024': 1, '2025': 2}
    phases.sort(key=lambda p: (order.get(p['cycle'], 9), p['sub'], p['date']))

    # ---- projects per phase ----
    def svc(v):
        v = (v or '').upper()
        if 'BOTH' in v or ('NR' in v and 'ER' in v.replace('NRIS', '')): return 'NRIS+ERIS'
        return 'NRIS' if v.startswith('NR') else ('ERIS' if v.startswith('ER') else v)
    projects = {}
    for r in _rows(qd.deliver / 'aps_projects.csv'):
        if not re.search(FILEKEY, r['file']): continue
        qid = (r['queue_id'] or '').split()[0]
        mw = num(r['MW'])
        ft = r.get('fuel/tech') or r.get('fuel_tech') or ''
        if not mw and re.search(r'\d', ft):
            mw = max(float(x) for x in re.findall(r'(\d+(?:\.\d+)?)', ft))
        projects.setdefault(r['file'], {})[qid] = {
            'mw': mw, 'service': svc(r['service_type']), 'poi': r['POI'],
            'total': num(r['total_allocated_cost_$']), 'nu': num(r['allocated_NU_cost_$']), 'tif': num(r['allocated_IF_cost_$']),
            'isd': r.get('estimated_ISD_or_backstop', ''), 'notes': r.get('status/notes', '')}

    # ---- upgrades per phase ----
    STATION = re.compile(r'switchyard (expansion|development)|new breaker|extend bus|network upgrades,? (new|extend)', re.I)
    ALLOC = re.compile(r'(Q\d+)\s*:\s*([\d.]+)%\s*(?:=\s*\$?\s*([\d,.]+))?')
    def clean(name):
        n = re.sub(r'^cost line item:\s*', '', name or '').strip()
        n = re.sub(r'^NU\s*\d+\s*:\s*', '', n)
        n = re.sub(r'\s+', ' ', n)
        n = n.replace('500kV', '500 kV').replace('345kV', '345 kV').replace('230kv', '230 kV').replace('230kV', '230 kV').replace('500kv', '500 kV')
        n = re.sub(r'\b500 k line\b', '500 kV line', n).replace(' k ties', ' kV ties')
        return n
    def key(n):
        k = n.lower()
        rules = [('sitgreaves-silverking', 'Sitgreaves–Silverking 500 kV line upgrade'),
                 ('new sitgreaves-pinnacle peak', 'New Sitgreaves–Pinnacle Peak 500 kV line'),
                 ('sitgreaves-pinnacle peak #2', 'New Sitgreaves–Pinnacle Peak 500 kV line'),
                 ('sitgreaves 500 kv station', 'Sitgreaves 500 kV station'),
                 ('new sitgreaves 500', 'Sitgreaves 500 kV station'),
                 ('2 sitgreaves-cholla', 'Upgrade 2 Sitgreaves–Cholla 500 kV lines'),
                 ('cholla series capacitor to sitgreav', 'Relocate Cholla series cap to Sitgreaves'),
                 ('series capacitor to sitgreaves-pinnacle', 'Add series cap Sitgreaves–Pinnacle Peak'),
                 ('add series capacitor to sitgreaves', 'Add series cap Sitgreaves–Pinnacle Peak'),
                 ('mazatzal', 'Sitgreaves–Mazatzal 345 kV loop into Chevelon'),
                 ('cholla-chevelon', 'Cholla–Chevelon 345 kV loop into Sitgreaves'),
                 ('tonto', 'Tonto–Irving–Childs 69 kV upgrade'),
                 ('four corners-moenkopi 500', 'Four Corners–Moenkopi #2 loop-in to Q480'),
                 ('q480-moenkopi #1', 'Upgrade Q480–Moenkopi #1 500 kV line'),
                 ('moenkopi series capacitor', 'Moenkopi series cap split/relocate (to Q480)'),
                 ('relocated moenkopi series', 'Moenkopi series cap split/relocate (to Q480)'),
                 ('230 kv ties at pinnacle', 'APS/SRP/WAPA 230 kV ties at Pinnacle Peak'),
                 ('pinnacle peak-lone peak', 'Pinnacle Peak–Lone Peak–Sunnyslope 230 kV'),
                 ('four corners-shiprock', "WAPA Four Corners–Shiprock 345 kV"),
                 ('q528-cholla 345', 'Upgrade Q528–Cholla 345 kV line'),
                 ('cholla series capacitor to q52', 'Relocate Cholla series cap to Q528'),
                 ('pinnacle peak 500/345', 'Pinnacle Peak 500/345 kV addition'),
                 ('pinnacle peak 500/230', 'Pinnacle Peak 500/345 kV addition'),
                 ('cholla-silverking 500 kv series', 'Cholla–Silverking 500 kV series cap upgrade'),
                 ('q609-four corners 345', 'Reduce Q609–Four Corners 345 kV series comp'),
                 ]
        for pat, lab in rules:
            if pat in k: return lab
        return n_title(n)
    def n_title(n): return n[:70]

    upgrades = {}   # file -> {upgrade_label: {...}}
    station = {}    # file -> {qid: $}
    SEG = re.compile(r'(Q\d+[A-Z]?)\s*:\s*([^;]*)')
    def parse_alloc(txt, cost):
        out = {}
        for qid, rest in SEG.findall(txt or ''):
            pct = re.search(r'([\d.]+)\s*%', rest)
            usd = re.search(r'\$\s*([\d,.]+)\s*(M|K)?', rest)
            p = float(pct.group(1)) if pct else None
            u = None
            if usd:
                u = float(usd.group(1).replace(',', '') or 0)
                if usd.group(2) == 'M': u *= 1e6
                elif usd.group(2) == 'K': u *= 1e3
            if p is None and u is not None and cost: p = round(u / cost * 100, 1)
            if p is None and u is not None and not cost: p = None
            out[qid] = {'pct': p, 'usd': u}
        if out and all(v['pct'] is None for v in out.values()):
            tot = sum(v['usd'] or 0 for v in out.values())
            for v in out.values(): v['pct'] = round((v['usd'] or 0) / tot * 100, 1) if tot else 0
        for v in out.values():
            if v['pct'] is None: v['pct'] = 0
        return out

    rows_all = [r for r in _rows(qd.deliver / 'aps_upgrades.csv') if re.search(FILEKEY, r['file'])]
    lineitem_files = {r['file'] for r in rows_all if 'cost line item' in (r.get('upgrade_id/name') or r.get('upgrade_id_name') or '').lower()}
    SKIPCAT = ('TOIF', 'TPIF', 'note')
    for r in rows_all:
        raw_name = r.get('upgrade_id/name') or r.get('upgrade_id_name') or ''
        name = clean(raw_name)
        cat = (r.get('category') or '').strip()
        if cat in SKIPCAT: continue
        cost = num(r.get('cost_$')) if re.search(r'\d', r.get('cost_$') or '') else 0
        allocs = parse_alloc(r.get('allocated_to'), cost)
        if not allocs: continue
        f = r['file']
        if f in lineitem_files:
            # Cholla/NSTS-style tables: NU label rows (pct only) + "cost line item" rows ($)
            if STATION.search(name):
                for qid, v in allocs.items():
                    station.setdefault(f, {})[qid] = station.get(f, {}).get(qid, 0) + (v['usd'] or 0)
                continue
            if not cost:
                derived = [v['usd'] / (v['pct'] / 100) for v in allocs.values() if v['usd'] and v['pct'] > 0]
                cost = round(sorted(derived)[len(derived) // 2], -5) if derived else 0
                derived_flag = bool(derived)
            else:
                derived_flag = False
            lab = key(name)
            u = upgrades.setdefault(f, {}).setdefault(lab, {'label': lab, 'cost': 0, 'cost_derived': False, 'lead': '', 'category': cat, 'alloc': {}, 'parts': []})
            if 'cost line item' in raw_name.lower():
                if u['alloc'] and u['parts'] and set(allocs) <= set(u['alloc']) and len(allocs) < len(u['alloc']):
                    continue
                if u['parts'] and u['cost'] and cost and abs(cost - u['cost']) / u['cost'] < 0.35 and not (set(allocs) & set(u['alloc'])):
                    for qid, v in allocs.items():
                        u['alloc'][qid] = {'pct': v['pct'], 'usd': v['usd'] or (u['cost'] * v['pct'] / 100)}
                    continue
                u['cost'] += cost; u['cost_derived'] |= derived_flag; u['parts'].append(name)
                u['lead'] = u['lead'] or (r.get('lead_time_or_ISD') or '')[:40]
                for qid, v in allocs.items():
                    a = u['alloc'].setdefault(qid, {'pct': v['pct'], 'usd': 0})
                    a['usd'] += v['usd'] or (cost * v['pct'] / 100)
                    a['pct'] = v['pct']
            else:
                for qid, v in allocs.items():
                    u['alloc'].setdefault(qid, {'pct': v['pct'], 'usd': 0})
            continue
        # generic one-row-per-upgrade tables
        live = {q: v for q, v in allocs.items() if v['pct'] > 0 or (v['usd'] or 0) > 0}
        if not live: continue
        if not cost:
            s_usd = sum(v['usd'] or 0 for v in live.values())
            if s_usd: cost = s_usd
        if cat == 'affected system' and not cost: continue
        if cat == 'station NU' and len(live) == 1:
            qid, v = next(iter(live.items()))
            station.setdefault(f, {})[qid] = station.get(f, {}).get(qid, 0) + (v['usd'] or cost * v['pct'] / 100)
            continue
        lab = re.sub(r'\s*\[cost_\$.*?\]', '', name).strip()[:90] or (r.get('description') or '')[:90]
        base = lab; i = 2
        while lab in upgrades.get(f, {}): lab = f'{base} ({i})'; i += 1
        desc = re.sub(r'\s*\[cost_\$.*?\]|\s*\|\|.*', '', r.get('description') or '').strip()
        upgrades.setdefault(f, {})[lab] = {
            'label': lab, 'cost': cost, 'cost_derived': False, 'lead': (r.get('lead_time_or_ISD') or '')[:40],
            'category': cat + (' (affected system)' if cat == 'affected system' else ''), 'parts': [desc] if desc and desc != lab else [lab],
            'alloc': {q: {'pct': v['pct'], 'usd': v['usd'] if v['usd'] is not None else cost * v['pct'] / 100} for q, v in live.items()}}

    # ---- assemble per phase ----
    out_phases = []
    for ph in phases:
        f = ph['file']
        prj = projects.get(f, {})
        ups = [u for u in upgrades.get(f, {}).values() if u['parts'] or u['cost']]
        rows = []
        for qid, p in prj.items():
            info = qinfo(qid)
            rows.append({'q': qid, **p, **info, 'station_nu': station.get(f, {}).get(qid, 0)})
        rows.sort(key=lambda x: int(re.sub(r'\D', '', x['q']) or 0))   # APS queue numbers are assigned in queue-date order
        cum = cum_nr = 0
        for r in rows:
            cum += r['mw']; cum_nr += r['mw'] if r['service'].startswith('NR') else 0
            r['cum_mw'] = cum; r['cum_nris_mw'] = cum_nr
        qorder = [r['q'] for r in rows]
        for u in ups:
            firsts = [q for q in qorder if q in u['alloc'] and u['alloc'][q]['pct'] > 0]
            u['first_q'] = firsts[0] if firsts else ''
            trig = next((r for r in rows if r['q'] == u['first_q']), None)
            u['trigger_cum_mw'] = trig['cum_mw'] if trig else None
            u['trigger_cum_nris_mw'] = trig['cum_nris_mw'] if trig else None
            u['n_sharing'] = len(firsts)
            u['sharing_mw'] = sum(r['mw'] for r in rows if r['q'] in firsts)
        ups.sort(key=lambda u: (qorder.index(u['first_q']) if u['first_q'] in qorder else 99, -u['cost']))
        out_phases.append({**ph, 'projects': rows, 'upgrades': ups})

    # prior-queued (pre-cluster / in-service) projects in the area for context
    prior = []
    for qid, a in A.items():
        if a['Study Area'] != AREA: continue
        if a['Process'] in ('Pre-2024 (Transition or serial)', 'In service', 'Surplus'):
            if any(qid in projects.get(p['file'], {}) for p in phases): continue
            info = qinfo(qid)
            prior.append({'q': qid, 'mw': num(a['Electrical Output MW']), 'poi': a['Requested POI'], 'process': a['Process'],
                          'status': a['Current Status'], 'tech': a['Energy Source Type'], 'queue_date': info.get('queue_date', '')})
    prior.sort(key=lambda x: int(re.sub(r'\D', '', x['q']) or 0) + (100000 if x['q'].startswith('SP') else 0))
    return {'area': AREA, 'generated': datetime.date.today().isoformat(),
            'source': 'APS OASIS cluster study reports; Active Queue posting ' + _iso(qd.posting),
            'phases': out_phases, 'prior': prior}


def describe(d: dict) -> str:
    """Plain-text dump of a ladder (what the old script printed)."""
    out = []
    for ph in d['phases']:
        out.append(f"== {ph['label']} ({ph['date']}) projects={len(ph['projects'])} upgrades={len(ph['upgrades'])}")
        for r in ph['projects']:
            out.append(f"  {r['q']:5s} {r.get('queue_date',''):10s} {r['mw']:7.1f} {r['service']:5s} cum={r['cum_mw']:6.0f} "
                       f"nris={r['cum_nris_mw']:6.0f} station=${r['station_nu']/1e6:5.1f}M total=${r['total']/1e6:7.1f}M")
        for u in ph['upgrades']:
            out.append(f"   NU ${u['cost']/1e6:7.1f}M{'*' if u['cost_derived'] else ' '} first={u['first_q']:5s} at "
                       f"{u['trigger_cum_nris_mw']} MW NRIS, shared by {u['n_sharing']} ({u['sharing_mw']:.0f} MW)  {u['label']}")
    return "\n".join(out)


def svc_from_request_type(t: str) -> str:
    t = (t or '').upper()
    if 'NETWORK' in t and 'ENERGY' in t:
        return 'NRIS+ERIS'
    if 'NETWORK' in t or t.startswith('NR'):
        return 'NRIS'
    if 'ENERGY' in t or t.startswith('ER'):
        return 'ERIS'
    return t


def build_unstudied(qd: QueueData, area: str, cycle: str = '2026 Cluster') -> list[dict]:
    """Requests of a cluster window with no study results yet (the 2026 cluster)."""
    Q = {r['FERC Q Number']: r for r in _rows(qd.queue_clean)}
    out = []
    for a in _rows(qd.queue_by_area):
        if a['Study Area'] != area or a['Process'] != cycle:
            continue
        r = Q[a['FERC Q Number']]
        out.append({'q': a['FERC Q Number'], 'mw': float(r['Electrical Output MW'] or 0),
                    'service': svc_from_request_type(r['Interconnection Request Type']),
                    'service_raw': r['Interconnection Request Type'], 'poi': r['Requested POI'],
                    'tech': r['Energy Source Type'], 'status': r['Current Status'],
                    'queue_date': r['Interconnection Request Submission Date'] or r['Date of Issuance of Queue'],
                    'requested_isd': r['LGP Projected In Service Date'], 'confidence': a['Confidence']})
    out.sort(key=lambda x: int(re.sub(r'\D', '', x['q']) or 0))
    return out
