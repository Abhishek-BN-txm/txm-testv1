# APS queue -> project library: ingest report

Generated 2026-10-22 16:53 from Active Queue posting 20261013; POI candidates from run 71 (/sessions/rcw-01g1narjvxkedphdfmzbs1nf/mnt/tara-automation/runs/71/results/BusData.csv).

| | requests |
|---|---|
| read (queue_by_study_area.csv) | 239 |
| skipped: already in service | 35 |
| kept in projects.xlsx (POI confirmed) | 0 |
| need attention | 204 |

## Why rows need attention

| reason | requests |
|---|---|
| POI not yet mapped to a case bus (see poi_review.csv) | 177 |
| line tap: build the POI on the change workbook and reference it (changes_ref) | 27 |
| 0 MW in the posting (uprate or add-on: check the request) | 10 |

## POIs to confirm (123)

Largest first. Fill `use_bus` in `poi_review.csv` for each POI you confirm (a bus number from the case), then run `python scripts/aps_queue.py accept-poi`. Line taps need a new bus on the change workbook instead.

| POI | kV | requests | MW | best candidate |
|---|---|---|---|---|
| Delaney 500 kV Substation | 500.0 | 10 | 4300 | 14012 DELANEY (1.0) |
| Cholla 500kV | 500.0 | 16 | 3774 | 14000 CHOLLA (1.0) |
| Four Corners 345kV | 345.0 | 3 | 3500 | 14101 FOURCORN (0.892) |
| TS30 500kV | 500.0 | 4 | 3027 | 14150 TS22 (0.55) |
| Saguaro 500kV | 500.0 | 4 | 2729.7 | 14004 SAGUARO (1.0) |
| Hoodoo Wash 500kV | 500.0 | 6 | 2050 | 22342 HDWSH (0.667) |
| Watermelon 500kV Substation | 500.0 | 2 | 1883 | 29400 ANTELOPE (0.556) |
| Panda 230kV | 230.0 | 6 | 1880 | 14238 PANDA 12 (0.883) |
| Sun Valley Substation 230kV | 230.0 | 6 | 1750 | 26103 VALLEY (0.8) |
| Cedar Mountain - Moonkopi 500 kV line | 500.0 | 3 | 1742.2 | line tap |
| Sitigraves 500kV | 500.0 | 2 | 1500 | 14435 AGAVE (0.583) |
| Sun Valley 500kV Substation | 500.0 | 2 | 1500 | 14010 SNVLY (0.764) |
| North Gila 500kV | 500.0 | 2 | 1300 | 22536 N.GILA (1.0) |
| County Line 500kV | 500.0 | 1 | 1266 | 40287 G_COULE (0.625) |
| Moonkopi 500kV Substation | 500.0 | 2 | 1250 | 14002 MOENKOPI (0.925) |
| Orchard |  | 2 | 1100 | 61260 ORCHARD (1.0) |
| Moonkopi El Dorado 500kV Line | 500.0 | 2 | 1000 | line tap |
| TS10 500kV Substation | 500.0 | 1 | 1000 | 19069 FMITS-&1 (0.6) |
| HANG1 500kV Cut-In | 500.0 | 2 | 1000 | line tap |
| Cedar Mountain 500kV Substation | 500.0 | 1 | 1000 | 14014 CEDARMT (1.0) |
| El Donado - Moonkopi 500kV | 500.0 | 1 | 1000 | line tap |
| Proposed TS30 |  | 1 | 1000 | 14289 TS34 (0.8) |
| APS Chevron 345 kV Substation | 345.0 | 2 | 999 | 14114 CHEVELON (0.85) |
| Milligan 230kV substation | 230.0 | 6 | 945 | 14271 MILLIGAN (1.0) |
| Trilby Wash 230kV | 230.0 | 3 | 850 | 14254 TRLBY (0.717) |
| Helios 230kV Switchyard | 230.0 | 2 | 716.1 | 24044 ELLIS (0.727) |
| Santa Rosa 230kV | 230.0 | 3 | 650 | 14226 SNTAROSA (0.991) |
| Westwing 230kV Substation | 230.0 | 2 | 600 | 14231 WESTWNGS (0.925) |
| APS Saguaro Substation |  | 1 | 576.7 | 14004 SAGUARO (1.0) |
| Bianco 230 kV | 230.0 | 2 | 550 | 14685 BIANCO (1.0) |
| Bacura 230kV Yard | 230.0 | 1 | 535 | 19204 HARCUVAR (0.556) |
| HANGII 500kV | 500.0 | 1 | 500 | 67826 ANTICLIN (0.571) |
| Orchard 230kV | 230.0 | 2 | 500 | 70313 ORCHARD (1.0) |
| HANGI 500kV line | 500.0 | 2 | 500 | line tap |
| Four Corners - Cha�la, 345 kV Line. | 345.0 | 1 | 500 | line tap |
| Saguaro - Cholla 500kV | 500.0 | 1 | 500 | line tap |
| Hassayampa - North Gila 500kV | 500.0 | 1 | 500 | line tap |
| North Gila 230 kV | 230.0 | 2 | 500 | 14267 N.GILA (1.0) |
| Parkway Substation 230kV | 230.0 | 2 | 500 | 30467 PARKWAY (1.0) |
| Proposed Alisa 2 Project Substation |  | 1 | 500 | 34377 AIRPROD_JCT (0.696) |
