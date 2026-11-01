# Reference data

Data the application ships with. Treat it as read-only: to update it, replace
the file, record the new provenance below, and re-run the checks.

## Bus_Coordinates.xlsx

Bus latitude/longitude used by footprint polygons (`src/tara_automation/footprint.py`).

| | |
|---|---|
| Copied from | `..\TARA Automation\TARA_ATC_v2 - Colorado Run\Bus_Coordinates.xlsx` (dated 2023-03-11) |
| Copied on | 2026-10-11 |
| SHA-256 | `9093a20e89eb6407711db987038842ddeb7bdcb8e53ef98ee607a0b99495a437` |
| Other copy | `..\pacx-analysis\data\reference\Bus_Coordinates.xlsx` (2022-07-24). Different bytes, but its WECC rows are identical. |

One sheet, `Buses`, 123,030 rows covering WECC (22,499), ERCOT (8,508) and
the Eastern Interconnection (87,156). Columns: `Y_LAT`, `X_LONG`, `ENTITYID`,
`BUSNUM`, `BUSNAME`, `LONGNAME`, `BUSTYPE`, `VOLTAGE`, `INTERCONNECT`,
`PFAREA_ENTITYID`, `POWERFLOW_AREAID`, `POWERFLOW_AREA`, `POWERFLOW_ZONEID`,
`POWERFLOW_ZONE`, `TRANSMISSION_ZONE`, `SUBID`, `SUBNAME`.

### What we know about its quality (checked against EAGL 30HS_22-G7_pre, run 25)

- **Bus numbers repeat across interconnections.** Always filter on
  `INTERCONNECT` (WECC for WECC cases) before matching on `BUSNUM`. Within WECC,
  885 numbers appear twice; 65 of those pairs are more than 5 mi apart.
- **Match to the case:** 21,366 of 29,907 case buses (71 %) are found by
  number. Name and kV agree for 19,149; kV only for 2,002; name only for 80;
  neither for 162. "Neither" means the number has been reused for a different
  bus (median 22 mi off), so those rows are rejected. AZ study area coverage is
  only 40 %: the file predates many queue buses.
- **Two quality tiers:**

  | Rows | WECC buses | vs. case coordinates | Transformer ends > 1 mi apart |
  |---|---|---|---|
  | `SUBNAME` present | 13,223 | median 0.04 mi, 6 % > 5 mi | 3.8 % |
  | `SUBNAME` = NULL | 9,903 | median 3.4 mi, 42 % > 5 mi | 29 % |

  The NULL-substation rows look like estimated positions. They rank below the
  case's own (vetted) coordinates.
- 75 WECC rows have no usable lat/lon (`#N/A` or implausible).

## cb_2023_us_county_20m.zip and the state outlines (Footprint Builder map)

Borders drawn behind the Footprint Builder's map. `scripts/build_map_borders.py`
simplifies them and embeds the result in `tools/footprint_builder.html`
(52 states, 3,222 counties, about 75,000 points, 745 KB). Re-run it after
replacing either source.

| | Counties | States |
|---|---|---|
| File | `data/reference/cb_2023_us_county_20m.zip` | `..\TARA Automation\TARA_ATC_v2 - Colorado Run\gz_2010_us_040_00_500k.kml` (read in place; not copied) |
| Source | US Census Bureau cartographic boundary files, 2023, 1:20,000,000, from `https://www2.census.gov/geo/tiger/GENZ2023/shp/` | US Census Bureau, 2010, 1:500,000 |
| Licence | Public domain (US Government work) | Public domain |
| Downloaded | 2026-10-11, with the user's approval | already on this machine |
| SHA-256 | `479956f6e3cb1573705c1ceb35d17ca7cd1b5179f3947ee6adbaa274b7e931e4` | `6b9fe82bf4a634a5f97df658244d8af441d26f2e0e89bf1ce6d5c4966da26ae6` |
