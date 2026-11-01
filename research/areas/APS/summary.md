# Arizona Public Service (APS): study area research

_Generated 2026-10-22T20:22:58 by external research session (from the digests) from 16 sources._

APS is a fast-growing summer-peaking system: EIA-930 shows AZPS energy up 7.1% in 2024 and 8.1% in 2025 and a new peak of 8,982 MW on 2 August 2026 [EIA], driven mainly by data centres and large industrial load [S11][S7]. The 2026 IRP (due at the ACC on 30 October 2026) answers with roughly 4 GW of new gas, 5.5 GW of solar and 3.1 GW of batteries by 2041, and now keeps Four Corners beyond 2031 [S8][S9]. The 2026-2035 ten-year plan more than doubles new 500 kV mileage to ~72 miles ($6.2B), centred on the Jojoba-Independence-Rudd and Panda-Freedom corridor, the Wafer and Independence 500/230 kV substations and Desert Sun [S14]. Against this, APS's Active Queue posted 13 October 2026 holds 198 requests not yet in service totalling 71.0 GW, about 7.9 times the 2026 peak, a third of it (24.8 GW) in the 2026 cluster that has not yet been studied; the latest cluster reports carry about $3.1B of network and station upgrades, led by the Sitgreaves–Pinnacle Peak and Saguaro–Bianco–Independence 500 kV lines [QUEUE]. History shows two-thirds of older queued MW withdrawing [S15].

## Status by pillar

| Pillar | Status | As of | Key numbers | Next action |
|---|---|---|---|---|
| Interconnection queue | Current | 2026-10-13 (APS Active Queue posting; cluster reports to 2026-10-21) | Pending requests (not in service): 198 / 71.0 GW [QUEUE]; 2026 cluster, not yet studied: 54 / 24.8 GW [QUEUE]; Upgrades in the latest cluster reports: ≈ $3.1B [QUEUE]; Withdrawn, requests queued by 2020: 65% of MW [S15] | Confirm POIs in poi_review.csv, starting with the 27 exact name-and-kV matches, then run accept-poi so the queue requests reach the project library. |
| Historical load and generation dispatch (EIA) | Current | EIA-930 hourly through 2026-10 | Peak 2026 to date: 8,982 MW (2 Aug) [EIA]; Energy 2025: 38,815 GWh, +8.1% [EIA]; 2025 mix: gas 39%, coal 25%, solar 20% [EIA]; 2041 peak forecast: 11,700 MW [S11] | Set EIA_API_KEY and Update EIA data to add the generator list. |
| Integrated resource plans (IRP) | Partial | RPAC deck 2026-09; 2026 IRP due 2026-10-30 | New gas by 2041: 4,020 MW [S8]; New solar / batteries: 5,494 / 3,098 MW [S8]; Procured 2023-24 RFPs: 8,460 MW [S10]; Four Corners: beyond 2031 [S8] [S7] | After 30 Oct 2026, add the filed 2026 IRP from the ACC docket and refresh the summary. |
| Ten-year transmission plans | Current | 2026-2035 Ten-Year Plan, filed 2026-01-30 | New 500 kV: ~72 mi [S14]; New 230 kV: ~190 mi [S14]; Plan cost: $6.2B [S14]; Key project: Jojoba-Independence-Rudd 500 kV, 2031 [S14] | Request the CEII Technical Study Report (ratings for the new 500 kV lines) before modelling them. |

## Interconnection queue

- APS's Active Queue posted 13 Oct 2026 lists 239 requests (77.8 GW): 198 not yet in service (71.0 GW), 35 in service (6.3 GW) and 6 surplus requests. [QUEUE]
- Pending by cycle: 2026 cluster 54 requests / 24.8 GW (not yet studied); 2025 cluster 36 / 10.3 GW; 2024 cluster 21 / 5.0 GW; Transition cluster 42 / 16.5 GW; pre-2024 serial or transition 45 / 14.5 GW. [QUEUE]
- Pending technology: solar+storage hybrids 27.8 GW (39%), storage 14.8 GW (21%), natural gas 11.1 GW (16%), wind 6.7 GW (9%), solar+storage+gas hybrids 4.5 GW; standalone solar only 2.3 GW. Service: NRIS 46.3 GW, ERIS 22.1 GW. [QUEUE]
- Pending by APS study area: Four Corners–Cholla 13.9 GW, Delaney–Sun Valley–Redhawk 13.6 GW, South of Phoenix 10.1 GW, Hoodoo Wash–Yuma 9.4 GW, Phoenix Metro 8.4 GW, Gila Bend 7.9 GW, NSTS (Navajo South) 7.6 GW, Northern 69 kV 0.2 GW. [QUEUE]
- Largest pending POIs: Delaney 500 kV 4.3 GW (10 requests), Cholla 500 kV 3.8 GW (14), Four Corners 345 kV 3.5 GW (3), TS30 500 kV 3.0 GW (4, a new substation), Saguaro 500 kV 2.7 GW, Hoodoo Wash 500 kV 2.0 GW, Watermelon 500 kV 1.9 GW, Panda 230 kV 1.9 GW. [QUEUE]
- By status: 25.1 GW in Phase 1 cluster study, 8.9 GW with a draft Transition-cluster LGIA, 5.4 GW in the Transition Phase 4 facilities study, 1.9 GW in design and 1.9 GW in construction. [QUEUE]
- Attrition candidates: 10 requests (5.5 GW) are in customer-initiated suspension, led by 3.5 GW of wind at Four Corners 345 kV (Q318–Q320); 56 pending requests (17.2 GW) show a projected in-service date before 2026 and are not in design or construction (e.g. Q274 1,000 MW on the Cedar Mountain–Moenkopi 500 kV line, Q466 1,000 MW at North Gila 500 kV). [QUEUE]
- New entries are accelerating: requests still in the queue from 2026 submissions total 25.0 GW (55), against 9.2 GW from 2025 and 5.0 GW from 2024. [QUEUE]
- 71 cluster system impact and facilities study reports (Jun 2024 to Oct 2026) cover the Transition, 2024 and 2025 clusters. Upgrade cost in the latest report of each cycle: South of Phoenix $1.34B, Four Corners–Cholla $1.18B, Gila Bend $0.43B, Hoodoo Wash–Yuma $0.10B; Phoenix Metro, Delaney–Sun Valley–Redhawk and NSTS under $0.06B each. [QUEUE]
- Largest upgrades: new Sitgreaves–Pinnacle Peak 500 kV line $1.0B (Four Corners–Cholla 2025 Ph1, first triggered by Q609 at 700 MW); Saguaro–Bianco–Independence 500 kV line with two 500/230 kV transformers $967M (South of Phoenix 2025 Ph1, Q578 at 300 MW); Saguaro–Q618 230 kV rebuild $202M; second Jojoba–Watermelon 500 kV line $180M (Gila Bend 2025 Ph2). [QUEUE]
- Attrition history: of requests queued in 2020 or earlier, 65% of MW withdrew, 22% are still active, 7% reached operation and 6% are suspended (LBNL, data through 2025). [S15]
- Context: the non-ISO West holds 957 active requests and 567 GW, the largest region nationally; US active queue capacity fell 10% in 2025. [S16]

## Historical load and generation dispatch (EIA)

- AZPS annual demand (EIA-930): 30.8 TWh in 2021, 32.0 in 2022, 33.5 in 2023, 35.9 in 2024 and 38.8 TWh in 2025; peaks 7.6 GW (2021) to 8.5 GW (2025), always on summer evenings (17:00-19:00). [EIA]
- 2026 to date: peak 8,982 MW on 2 August 2026 at 19:00; APS's own figures give an all-time peak of 8,631 MW on 7 August 2025 and a 2026 forecast peak of 8,648 MW with a 1,326 MW reserve margin. [EIA] [S11] [S9]
- 2025 generation in the AZPS balancing authority: gas 39%, coal 25%, solar 20%, batteries 8%, wind 7%. EIA-930 reports no nuclear for AZPS, although APS owns 1,146 MW of Palo Verde. [EIA] [S12]
- AZPS is a large net importer (-14.8 TWh in 2025), almost all from SRP (-30.1 TWh), while exporting to WALC (+6.5 TWh) and CAISO (+6.4 TWh). [EIA]
- At the peak hour of the 10 highest-demand days of the last 12 months: gas 1.9 GW, solar 1.5 GW, batteries 1.1 GW, wind 1.0 GW, coal 0.8 GW. [EIA]
- 2026 IRP load forecast: sales from 35.7 TWh (2026) to 59.8 TWh (2041), +67%; peak 11,700 MW in 2041 (2.0%/yr); data centres and large industrial are 76% of the growth. [S11]
- Near-term guidance: retail sales +4-6% in 2026 and +5-7% a year through 2030; about 19 GW of uncommitted large-load requests; a first 1.2 GW large-customer subscription ramping from 2028. [S7] [S11]
- Phoenix load pocket (APS+SRP): peak 13,906 MW in 2024, forecast 17,726 MW in 2030 and 19,650 MW in 2035; import at peak ~11.4 GW in 2030 with ~6.4 GW of local (RMR) generation needed at peak. [S14]

## Integrated resource plans (IRP)

- The 2026 IRP is due at the ACC on 30 October 2026 (deadline extended); the 2023 IRP was acknowledged on 8 October 2024. [S9] [S7]
- Draft 2026 IRP base case, net nameplate additions 2026-2041: gas 4,020 MW, solar 5,494 MW, batteries 3,098 MW, customer-based 2,988 MW, wind 73 MW; coal -970 MW. [S8]
- Four Corners: an agreement with NTEC allows operation beyond 2031 and the Preferred Plan will include it; running to 2038 lowers the 2026-2041 revenue requirement by ~$1.0B. [S8] [S7]
- Gas build-out: Cholla two-unit coal-to-gas conversion (380 MW, 2029); Desert Sun CTs (700 MW) moved to the existing Saguaro site, COD 2031, within an up-to-2,000 MW flexible gas programme; new gas pipeline by late 2029; two 600 MW combined-cycle tolls extended to 2038 and 2046. [S9] [S10] [S7]
- Already procured (2023 and 2024 RFPs): 8,460 MW (storage 3,606, solar 2,649, gas 1,705, wind 500) with CODs 2026-2030; summer 2026 additions included Agave, Beehive, Desert Bloom, Justice and Pluto batteries, Catclaw and Maricopa Energy Center solar+storage, Ironwood and Papago solar, West Camp wind (500 MW) and the Sundance CT expansion. [S10] [S9]
- 2025 all-source RFP: at least 1 GW for 2028-2031 and beyond; 244 proposals, 51 shortlisted. [S10] [S8]
- The 2026 IRP co-optimises transmission with generation (PLEXOS, zonal); an early solution selected 8 new transmission projects ($1.7B), and all cases rely on substantial transmission expansion. [S9] [S8]
- Resource adequacy moves to perfect-capacity accreditation with a 0.1 LOLE target; pipeline limits push reliability risk outside the summer peak. Palo Verde licence renewal is planned (operation into the 2060s). [S13] [S7]

## Ten-year transmission plans

- 2026-2035 Ten-Year Plan (filed 30 January 2026): ~72 mi new 500 kV, ~190 mi new 230 kV, ~578 mi of 345 kV rebuilds, 34 new transformers, $6.2B; the 2025 plan had ~32 mi of new 500 kV. [S14] [S1]
- Renames that affect case bus names: TS21 is now Independence, TS22 Wafer, TS33 Helios; Runway Additional is Diamond. [S14]
- Major dated projects: Wafer 500/230 kV (3 transformers, 2028); Bagdad 230 kV via Mead Phoenix Q01 500/230 kV (2028); Independence 500/230 kV substation (2030) and transformers (2031); Jojoba-Independence-Rudd 500 kV (2031, with SRP); Panda-Freedom 230 kV #2 (2030) and #1 upgrade (2031); Desert Sun 230 kV (2030) and 500 kV (2031) interconnections; Four Corners-Cholla-Pinnacle Peak 345 kV rebuilds, 500 kV-capable (2038). [S14] [S2]
- Several 230 kV projects exist to serve large-load customers: Contrail (2026), Diamond (2027), Wafer (2028), Bagdad (2028). [S14]
- New undated 500 kV concepts with no CEC: Texas Hill, TS37, Morgan-Wafer #2, Jojoba-Independence #2, Watermelon-Jojoba #2 and #3, Independence-Saguaro. Removed: Quartzsite-Colorado River 500 kV and Komatke substation. [S14]
- Placed in service in 2025: Goodyear, Parkway, Broadway and Dromedary 230 kV; Ironwood 500 kV interconnection; West Camp gen-tie with the Sitgreaves 345 kV switchyard. [S14] [S2]
- APS transmission capex: $550M (2026), $695M (2027), $860M (2028). [S7]
- Modelling caution: ratings for most new 500 kV projects are TBD, and the Technical Study Report (congestion and load-growth pockets) is CEII. [S14]

## Where sources disagree

- Queue size: LBNL counts 149 active requests / 48.4 GW at the end of 2025 [S15]; the APS posting of 13 Oct 2026 lists 198 requests / 71.0 GW not in service [QUEUE]. Most of the difference is the 2026 cluster (24.8 GW entered in 2026). Use the posting.
- Cotton Transmission Corridor (Jojoba-Rudd + Panda-Freedom): the APS STX web page says 2032 [S4]; the 2026 ten-year plan says 2030-2031 [S14]. Use the plan.
- 2025 peak: APS reports 8,631 MW on 7 August 2025 [S11]; EIA-930 AZPS balancing-authority data shows 8,499 MW that day [EIA]. Utility vs balancing-authority accounting and EIA adjustments differ.
- Long-term peak: the 2023 IRP forecast 13,119 MW in 2038 (2.4%/yr) [S12]; the 2026 IRP forecast is 11,700 MW in 2041 (2.0%/yr) [S11], although energy growth is higher.
- Four Corners: exit in 2031 in the 2023 IRP [S12] and the 2026 IRP base case [S8]; operation beyond 2031 in the Preferred Plan direction [S8][S7].
- Phoenix RMR study: 2035 import at peak is 11,432 MW in Table ES1 but 13,432 MW in Table 3 (likely a typo in ES1) [S14].

## Gaps to fill

- Queue POIs confirmed to case buses (0 of 123): needed before queue requests can be modelled.
- The 2026 IRP filing itself (due 30 October 2026).
- ACC 13th Biennial Transmission Assessment report (Decision 81541) and the WestConnect regional plan.
- The CEII Technical Study Report and system ratings (protective agreement).
- The EIA-860M generator list: set an EIA API key, then Update EIA data.
- Where the committed data-centre loads connect (substations / buses).

## Sources

- [EIA] EIA data for balancing authority AZPS
- [QUEUE] APS interconnection queue: Active Queue posting 2026-10-13 and cluster study reports
- [S1] Arizona Public Service Company 2025 to 2034 Ten-Year Transmission System Plan
- [S2] APS 2026 Annual Progress Report for Planning Coordination
- [S3] APS 2025 Annual Progress Report for Planning Coordination
- [S4] Strategic Transmission Expansion (STX)
- [S7] Pinnacle West Capital Corp / APS Form 10-Q for quarter ended June 30, 2026
- [S8] APS RPAC Meeting 9/30/2026 - Draft IRP Case Results
- [S9] APS RPAC Meeting 7/22/2026 - Cholla conversion, 2026 IRP Base Case, Summer Preparedness, IRP filing extension
- [S10] APS RPAC Meeting 4/15/2026 - ASRFP update, 2026 IRP market prices, RA follow-up, 2026 IRP scenarios
- [S11] APS RPAC Meeting Nov 14, 2025 - ASRFP update, Large Customer Subscription Program, 2026 IRP resource pricing, 2026 IRP load forecast
- [S12] Arizona Public Service Company 2023 Integrated Resource Plan (Public)
- [S13] APS RPAC Meeting 2/13/2026 - Resource Adequacy Update, Resource Costs follow-up (E3), 2026 IRP Timeline
- [S14] Arizona Public Service Company 2026 to 2035 Ten-Year Transmission System Plan (ACC filing, with Attachments B, C, E, F)
- [S15] Queued Up 2026 Edition: interconnection queue data file (data through end of 2025), APS records
- [S16] Queued Up: 2026 Edition - Characteristics of Power Plants Seeking Transmission Interconnection As of the End of 2025
