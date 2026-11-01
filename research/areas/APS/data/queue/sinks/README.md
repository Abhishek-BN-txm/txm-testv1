# APS cluster-study sinks

What each APS interconnection cluster study **states** about how it balances the
added cluster MW (the sink). Taken from the report text, with quotes. Physical flow
paths are a separate piece of work and are not covered here.

## Files

| File | Contents |
| --- | --- |
| `sinks_by_study.csv` | One row per report (the 71 rows of `deliver/aps_studies.csv` plus 2 historical serial-cluster SIS reports): sink method, elements, MW, a verbatim quote (under 40 words), section/page, confidence and notes. |
| `<key>.yaml` | A footprint `sinks:` block for each area: `fccho`, `phx`, `dsr`, `gila`, `sofphx`, `hdw`, `nsts`, `north`. Each cycle (Transition / 2024 / 2025) gets one group, built from that cycle's **latest** report. Comment lines give the file, date, section, quote and the report's MW split. The header marks the CURRENT cycle (the newest cycle that has a stated sink). |
| `sink_participation.csv` | Per-group, per-area participation %. HS/HW come from the report's load-ratio table. For the Northern and Gila Bend Transition groups, the % is computed from run 71 area load, because those reports give only the rule. |

## Method

1. The report markdown (`queue/raw/*.json`, key `markdown`) was searched for the
   "Generation Dispatch" / "Generation Dispatch Assumption(s)" section and for any
   offset, redispatch, interchange or sink wording. Facilities Studies (FaS) have no
   dispatch section. Two Transition Phase 1 reports exist only as partial table
   extracts. All of these are recorded as `not stated`.
2. Quotes are copied verbatim. Whitespace is collapsed and markdown tags removed.
   Page numbers come from each report's table of contents.
3. The report's areas were mapped to run 71 (30HS_22-G7_pre) area numbers. The
   report numbers match the case numbers: 10 NM, 14 APS, 15 SRP, 16 TEP, 17 AEPCO,
   18 NV, 19 WALC, 21 IID, 22 SDG&E, 24 SCE, 26 LADWP, 30 PG&E, 65 PACE, 73 WAPA RM.

### Sink methods found

- **Load-ratio offset (most 2024/2025 and Transition reports).** Quote: "All queue
  generation added to the case was offset proportionally according to load within AZ
  and bordering states." The non-AZ share is scaled by Pgen within each area. The AZ
  share (APS area 14) is "applied outside the study region". Most reports list the
  same 10 areas (10, 14, 18, 21, 22, 24, 26, 30, 65, 73). The Gila Bend 2024 Ph2+/2025 Ph2
  reports list 12 areas instead, including SRP, TEP, AEPCO and WALC, and leave out NV
  and PG&E.
- **APS + SRP interchange (Gila Bend 2024 Ph1, 2025 Ph1).** "All 2024 cluster queue
  projects are dispatched against the interchange within APS and SRP area." In 2025
  Ph1 the MW is "spread equally among generators in the SRP area". These are
  superseded by Phase 2 of the same cycles.
- **Areas away from APS (Northern and Gila Bend Transition).** Generation is
  dispatched "against the interchange of all areas away from APS, excluding any area
  located within the state of Arizona or East of APS territory", with shares by load
  and units scaled by MVA weight. The area list is **an interpretation** (confidence
  medium; see the caveats).
- **Historical serial clusters (2018 Cholla, 2022 Q311 SIS).** These had three
  alternative offset cases: California, NM/Cholla coal, and the Palo Verde hub.
  They are in the CSV only, not in the YAMLs.

## How to use

Paste a group under `sinks:` in a study's `footprint.yaml`:

```yaml
sinks:
  PHX_2025_SINK:          # copied from phx.yaml
    areas: [10, 14, 18, 21, 22, 24, 26, 30, 65, 73]
    exclude:              # the reports take the AZ share outside the study region
      near: {bus: <POI bus>, hops: 4, via_kv_min: 69}
```

You can also reference a pasted group from another group with `groups: [PHX_2025_SINK]`.
Compile with:

```
PYTHONPATH=src:. python3 scripts/footprint.py compile --spec <footprint.yaml> --run 71 --out <dir>
```

Every group is made of whole areas, so the compiler writes `AREA n` lines. The
selector grammar has no weights. To reproduce the report's MW split, scale each
area by the % in `sink_participation.csv`, or build one sink per area.

## Validation (run 71)

Each YAML was wrapped in a minimal spec (`study_area: {name: AZ, areas: [14]}` plus
the sinks block) and compiled into `_scratch/sink_check/<key>`. Bus counts are listed below.
All 23 groups compiled with no warnings, and each is written as whole `AREA n` lines:

| Groups | Areas | Buses |
| --- | --- | --- |
| FCCHO_TC/2024/2025, PHX_TC/2024/2025, DSR_TC_DSV/TC_RH/2024/2025, SOFPHX_TC/2024/2025, HDW_TC/2024/2025, NSTS_TC/2024/2025 | 10,14,18,21,22,24,26,30,65,73 | 14,628 |
| GILA_2024_SINK, GILA_2025_SINK | 10,14,15,16,17,19,21,22,24,26,65,73 | 11,310 |
| GILA_TC_SINK, NORTH_TC_SINK | 18,21,22,24,26,30,40,50,52,54,60,62,63,64,65 | 22,356 |


## Caveats

- **The selectors are a superset.** The reports scale *generation* by Pgen within
  each area. The groups contain every bus in the area, and the transfer engine's
  generator participation does the rest. Check that its scaling rule (Pgen / Pmax /
  MVA) matches the report.
- **"Outside the study region"**: no report says which APS units carry the AZ share
  ("selectively applied"), so the YAMLs contain all of area 14 (and 15/16/17/19 for
  Gila Bend). Add a study-specific `exclude`.
- **Interpreted area lists**: for the Northern and Gila Bend Transition groups, the
  YAML uses WECC areas west and north of AZ (18, 21, 22, 24, 26, 30, 40, 50, 52, 54,
  60, 62, 63, 64, 65). It leaves out 10/11 (east), 70/73 (Colorado, ambiguous) and 20
  CFE (ambiguous).
- **One revision differs**: the Phoenix Metro 2024 Ph2 report says the non-AZ share
  was applied "according to load" rather than Pgen.
- **Santan**: several Phoenix Metro and South of Phoenix reports hold SRP Santan at
  1226 MW. This is a fixed dispatch, not a sink.
- **Implied MW**: `MW_balanced` is never stated explicitly. It is the cluster MW,
  quoted from `aps_studies.csv`.
- The report percentages apply to the reports' own base cases (2028/2030 HS/HW), not
  to run 71's load.
