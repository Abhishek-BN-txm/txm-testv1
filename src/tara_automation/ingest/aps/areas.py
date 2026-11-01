"""Study area of every APS queue request.

APS studies its clusters in eight areas. A request's area comes from, in order:
  1. the report rosters (CONFIRMED below, plus every project row of a parsed
     report whose filename names the area) -> confidence "confirmed";
  2. POI keyword rules (RULES; spellings as the queue posting has them);
  3. county fallback -> confidence "low".
`Process` is the cycle the request sits in (cluster year, serial, in service).
"""

from __future__ import annotations

import collections
import csv
import glob
import re

from tara_automation.ingest.aps import QueueData

# (1) Confirmed from parsed cluster/FaS reports (queue_id -> area, source)
CONFIRMED = {}
def conf(area, ids, src):
    for i in ids: CONFIRMED[i] = (area, src)
conf('Hoodoo Wash-Yuma', ['Q368','Q442','Q443','Q425','Q466'], 'HDW-Yuma Transition Ph1')
conf('Hoodoo Wash-Yuma', ['Q564','Q568','Q570'], 'HDW-Yuma 2024 Ph1')
conf('Hoodoo Wash-Yuma', ['Q596','Q597','Q599','Q603'], 'HDW-Yuma 2025 Ph1')
conf('Delaney-Sun Valley-Redhawk', ['Q437','Q467','Q474','Q475'], 'Delaney-Sun Valley Transition Ph2')
conf('Delaney-Sun Valley-Redhawk', ['Q519'], 'Redhawk Transition Ph1/Ph2')
conf('Delaney-Sun Valley-Redhawk', ['Q550','Q567','Q572'], 'Del-Svly-Red 2024 Ph1/Ph2')
conf('Delaney-Sun Valley-Redhawk', ['Q580','Q582','Q595','Q605','Q617'], 'Del-Svly-Red 2025 Ph1')
conf('Four Corners-Cholla', ['Q333','Q347','Q352','Q387','Q402','Q422','Q423','Q424','Q480','Q497','Q528','Q538','Q539'], 'FcCho Transition Ph1-3')
conf('Gila Bend', ['Q463','Q488','Q507'], 'Gila Bend Transition Ph1/Ph2')

FILEAREA=[('South_of','South of Phoenix'),('SofPhx','South of Phoenix'),('soPhx','South of Phoenix'),('HDW','Hoodoo Wash-Yuma'),('GilaBend','Gila Bend'),('FcCho','Four Corners-Cholla'),('Redhawk','Delaney-Sun Valley-Redhawk'),
          ('Del','Delaney-Sun Valley-Redhawk'),('DLN','Delaney-Sun Valley-Redhawk'),('NSTS','NSTS (Navajo South)'),
          ('Northern','Northern (69 kV Flagstaff/Winslow)'),('Phoenix','Phoenix Metro'),('SofPhx','South of Phoenix'),('soPhx','South of Phoenix'),('South_of','South of Phoenix')]

# (2) POI keyword rules: (regex on POI, area, confidence)
RULES = [
    (r'hoodoo|hood(o|ow)\s*wash|hang\s*(1|i|ii)|north\s*gila|texas\s*hill|orchard|desert\s*sands|yuca|yucca|yuma\s*69', 'Hoodoo Wash-Yuma', 'high'),
    (r'panda|hazen|hazon|haem|freedom|komatke|gila\s*river|gila\s*bend|cotton\s*center|ajo', 'Gila Bend', 'high'),
    (r'delaney|sun\s*valley|redhawk|red\s*hawk|ts\s*-?30|trilby|trilly', 'Delaney-Sun Valley-Redhawk', 'high'),
    (r'sag(u|ua)?a?ro|sagaro|saugaro|saquaro|milligan|santa\s*rosa|helios|bianco|ts\s*-?25|ts\s*-?32|casa\s*grande|san\s*manuel|tat\s*mo', 'South of Phoenix', 'high'),
    (r'twin\s*ar|cataract|winslow|blue\s*ridge|woody\s*mountain', 'Northern (69 kV Flagstaff/Winslow)', 'medium'),
    (r'cholla|sit(i)?gr?(e)?a?ves|stigreaves|chevelon|chevron|four\s*corners|zeniff|show\s*low|conley', 'Four Corners-Cholla', 'high'),
    (r'mo(o|e|se)n?(k|l)o(p|r)i|cedar\s*m|el\s*do(r|n)ado|colar|dugas|rabbit\s*canyon|yavapai\s*500', 'NSTS (Navajo South)', 'high'),
    (r'westwing|raceway|runway|ocotillo|oostillo|broadway|central\s*230|palm\s*valley|reach|scatter\s*wash|scatterwash|el\s*sol|colter|honeywell|perryville|egg\s*ranch|gillespie|raptor|wwtp|lukefeld|toyota|buckeye|independence|wafer', 'Phoenix Metro', 'medium'),
    (r'watermelon|county\s*line', 'Gila Bend', 'low'),
    (r'paloma|komal', 'Gila Bend', 'medium'),
    (r'stratus|sarival|commerce|avery|tsmc', 'Phoenix Metro', 'medium'),
    (r'parkway', 'Delaney-Sun Valley-Redhawk', 'low'),
    (r'granite\s*creek|lone?s?ome\s*valley', 'Northern (69 kV Flagstaff/Winslow)', 'low'),
    (r'bac+a(r|n)a|bacura|baccara|raptor|sabre', 'Phoenix Metro', 'high'),
]
# (3) county fallback
COUNTY = {'Yuma':'Hoodoo Wash-Yuma','Navajo':'Four Corners-Cholla','San Juan':'Four Corners-Cholla',
          'Coconino':'NSTS (Navajo South)','Yavapai':'NSTS (Navajo South)',
          'Pinal':'South of Phoenix','Pima':'South of Phoenix'}

def process(x):
    s, sy = x['Current Status'], x['Submission Year']
    if x['FERC Q Number'] in CYCLE and s not in ('In-Service','COD - Pending Refund'): return CYCLE[x['FERC Q Number']]
    if s in ('In-Service','COD - Pending Refund'): return 'In service'
    if s.startswith('Surplus'): return 'Surplus'
    if s.startswith('Transitional Cluster'): return 'Transition Cluster'
    if sy in ('2024','2025','2026'): return f'{sy} Cluster'
    if 'Cluster' in s: return 'Transition Cluster'
    return 'Pre-2024 (Transition or serial)'



def assign(qd: QueueData) -> tuple[list[dict], str]:
    """Rows of queue_by_study_area.csv and a text summary."""
    CONF = dict(CONFIRMED)
    CYCLE = {}
    for f in sorted(glob.glob(str(qd.parsed / "*_projects.csv"))):
        with open(f, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                fn = r.get("file", ""); qid = (r.get("queue_id") or "").strip()
                a = next((a for k, a in FILEAREA if k in fn), None)
                if a and re.match(r"^Q\d+$", qid) and qid not in CONF:
                    CONF[qid] = (a, fn.replace(".pdf", ""))
                if a and re.match(r"^Q\d+$", qid):
                    cyc = ("Transition Cluster" if "Transition" in fn else
                           ("2024 Cluster" if "2024" in fn else ("2025 Cluster" if "2025" in fn else None)))
                    if cyc:
                        CYCLE[qid] = cyc

    def process(x):
        s, sy = x["Current Status"], x["Submission Year"]
        if x["FERC Q Number"] in CYCLE and s not in ("In-Service", "COD - Pending Refund"):
            return CYCLE[x["FERC Q Number"]]
        if s in ("In-Service", "COD - Pending Refund"):
            return "In service"
        if s.startswith("Surplus"):
            return "Surplus"
        if s.startswith("Transitional Cluster"):
            return "Transition Cluster"
        if sy in ("2024", "2025", "2026"):
            return f"{sy} Cluster"
        if "Cluster" in s:
            return "Transition Cluster"
        return "Pre-2024 (Transition or serial)"

    with open(qd.queue_clean, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for x in rows:
        q, poi, co = x["FERC Q Number"], x["Requested POI"] or "", x["County (normalized)"]
        if q in CONF:
            area, src = CONF[q]
            basis, conf_ = f"Listed in {src} report", "confirmed"
        else:
            area = None
            for rx, a, c in RULES:
                if re.search(rx, poi, re.I):
                    area, basis, conf_ = a, f'POI "{poi}" matches {a} area', c
                    break
            if not area and co in COUNTY:
                area, basis, conf_ = COUNTY[co], f"County fallback ({co})", "low"
            if not area:
                area, basis, conf_ = "Unassigned", f'POI "{poi}" not mapped', "n/a"
        x["Study Area"], x["Assignment Basis"], x["Confidence"], x["Process"] = area, basis, conf_, process(x)

    def mw(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    agg = collections.defaultdict(collections.Counter)
    cnt = collections.defaultdict(collections.Counter)
    for x in rows:
        agg[x["Study Area"]][x["Process"]] += mw(x["Electrical Output MW"])
        cnt[x["Study Area"]][x["Process"]] += 1
    lines = ["area|" + "|".join(PROCS) + "|pending MW (excl in-service)"]
    for a in sorted(agg, key=lambda a: -sum(v for p, v in agg[a].items() if p not in ("In service", "Surplus"))):
        pend = sum(v for p, v in agg[a].items() if p not in ("In service", "Surplus"))
        lines.append(a + "|" + "|".join(f"{cnt[a][p]} / {agg[a][p]:.0f}" for p in PROCS) + f"|{pend:.0f}")
    lines.append(str(collections.Counter(x["Confidence"] for x in rows)))
    un = [(x["FERC Q Number"], x["Requested POI"]) for x in rows if x["Study Area"] == "Unassigned"]
    lines.append(f"Unassigned: {un}")
    return rows, "\n".join(lines)


COLUMNS = ["FERC Q Number", "Study Area", "Confidence", "Process", "Electrical Output MW", "Requested POI",
           "County (normalized)", "Energy Source Type", "Interconnection Request Type", "Current Status",
           "Submission Year", "Assignment Basis"]
PROCS = ["Transition Cluster", "2024 Cluster", "2025 Cluster", "2026 Cluster",
         "Pre-2024 (Transition or serial)", "In service", "Surplus"]
