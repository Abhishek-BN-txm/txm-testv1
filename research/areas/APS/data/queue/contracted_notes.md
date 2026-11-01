# APS queue: contracted-project candidates (review before adding to the repo)

Built 2026-10-23 for the study-desk "contracted by APS" highlight (Interconnection, ladder charts, map colours). Nothing here has been written to the repo.

## Files
- `contracted.csv`: 60 queue rows tied to a contract. By class: aps_owned 24, aps_ppa 30, other_offtaker 6. By confidence: high 19, medium 36, low 5. No rows are classed aps_selected or aps_likely; see Gaps.
- `unmatched.csv`: 18 contracted resources (or groups of them) with no queue position.
- `scripts/build_contracted.py` in the scratchpad regenerates both CSVs.

## Method
1. **Queue master.** The master list is `research/areas/APS/data/queue/parsed/active_queue_20261013_clean.csv` (240 rows, including in-service and SP surplus requests). `export/generators.csv` has only the 219 study-area rows. **30 of the 60 matched IDs are not in generators.csv**: they are in-service or existing plants, mostly in phx, gila and hdw. The UI can only highlight them if it reads the active-queue file, or the matches are joined to it.
2. **Contracted resources, from these sources:**
   - Pinnacle West FY2025 10-K resource tables. This is the controlling list of owned, PPA and BESS-PPA resources with COD.
   - APS 2023 IRP Table 2-4 and Attachment D.1(a)(1). These carry the 2022 ASRFP slate: Westwing I/II, Scatterwash, Sunstreams 3/4, Serrano (footnote 6: 170 PV / 213.75 ESS), Yuma (footnote 7: 70 PV / 67 ESS), Harquahala, CO Bar C.
   - RPAC decks from Jul, Apr and Sep 2026 (repo digests S8, S9, S10). These give the summer-2026 additions list (which includes Pluto 75 MW), the 2024 ASRFP gas tolls, the Desert Sun relocation and the 2025 ASRFP shortlist.
   - APS Saguaro and Desert Sun project pages, developer releases, and EIA-860M plant coordinates (via InfraSure and interconnection.fyi).
3. **Matching.** Matches use POI substation, MW (exact solar/BESS split where the APS study lists it), technology, county, ISD/COD and shared gen-ties. Two kinds of evidence were treated as strongest:
   - The APS cluster-study queue-ahead lists, which give the PV/BESS split per Q and CAISO cross-references.
   - EIA plant coordinates near a known substation.

   No match was forced. Ambiguous alternates are written in `note`.
4. **Classes.**
   - `aps_owned`: existing APS plants, the approved Redhawk expansion, and the proposed but unapproved Cholla conversion, Desert Sun and Saguaro expansion (`contract_status=announced`). The UI may want to style the announced ones differently.
   - `aps_ppa`: PPAs, tolls and storage agreements listed in the 10-K.

## Changes from the existing repo file `data/queue/context/aps_procured_resources.csv`
- **Papago Solar + Papago Storage = Q248, not Q276.** The 2025 Delaney cluster report lists Q248 as 150 MW SPV + 300 MW BESS at Delaney (in service). That is exactly Papago Solar (150) plus Papago Storage (300; Recurrent, online July 2025, tolled to APS).
- **Q276 = Harquahala Sun 1.** Q276 shares Copia's gen-tie with Q389, Q437 and Q475. Harquahala Sun 1 is 150 MW and its offtaker is not disclosed; the 10-K does not list it. It is left out of contracted.csv.
- **Harquahala Sun 2 = Q389** (300/300, in service, CAISO 1678). This match is new.
- **Desert Sun = Q629**, not Q629+Q638. APS now describes the Gila Bend site as up to ~1,280 MW for 2031, and Q629 is 1,255 MW with an ISD of 12/31/31. Part of the original plan moved to Saguaro.
- **Saguaro expansion (720 MW, 2030) = Q632** (695.4 MW ERIS, ISD 12/31/30). This match is new.
- **Cholla conversion (~380 MW, 2029) = Q626 + Q627** (111.5 + 279 MW of gas at Cholla 230/500 kV, ISD 7/1/28). This match is new.
- **Pluto (75 MW) = Q268 at Sarival 69 kV.** EIA coordinates put the plant on Sarival Ave in Goodyear. This match is new.
- **White Tank = Q358 (high).** The developer states the POI is APS Colter substation.
- **Justice = Q357, upgraded.** EIA coordinates are next to the Freedom substation.
- **Desert Bloom = Q299, upgraded.** The plant is 1.1 km from the Westwing BESS.
- **Beehive = Q278 (Raceway).** The plant is at Lake Pleasant, 230 kV. This replaces "Q278 or Q427".
- **CO Bar C = Q274**, the 1 GW CO Bar complex. Enlight states the complex holds a 1 GW AC LGIA. Offtake is shared: APS for CO Bar 1/C, SRP for CO Bar 4 and 5.
- **2022 ASRFP and older resources added:** AES Westwing = Q292; Scatter Wash = Q314; Serrano = Q239 (plus Q575 grid charging); Yuma Solar Energy = Q236/272; El Sol = Q229; Chevelon Butte = Q273; Solana = Q44; Perrin Ranch = Q113; Ajo = Q77; and the AZ Sun BESS retrofits (Q300–Q307, SPI01, SPI02, Q395).

## Conflicts and doubts
- **SPI01 and SPI02.** The queue file spells these "SPI01" and "SPI02", probably a misread of SP101 and SP102. The CSV keeps the spelling from the file so the join works.
- **AZ Sun retrofits.** Q300 and Q305 (two 17 MW units at Cotton Center 12 kV) are assigned to Cotton Center and Paloma, but which is which is a guess. Q301 (Gillespie 12 kV, 10 MW) is assigned to Desert Star BESS with low confidence.
- **Q376 = Agave.** Q376's 400 MW of BESS exceeds Agave's 150 MW.
- **Q229 vs Q289.** Both are 50 MW storage at El Sol 69 kV. The El Sol PPA plant could be either one.
- **Q274 and Q275.** In both cases APS contracts only part of a 1 GW request: CO Bar C is 206 of 1,000 MW, and Snowflake A is 475 of 1,000 MW.
- **Q273.** The queue shows 477 MW against 454 MW contracted.
- **Hashknife.** The split across Q230, Q271 and Q270 is inferred.
- **Gas tolls.** The 10-K says the 570 MW toll extends at 600 MW to 2034. The Q2-2026 10-Q says "two existing gas tolls extended at 600 MW each through 2038 and 2046". This was not reconciled; neither plant has a queue entry.
- **Other-offtaker rows.**
  - Q332 (Dateland complex): only the Sierra Pinta 112.5 MW BESS is known to be contracted (PG&E RA).
  - Q6 (Dry Lake, SRP): identified by location and size only.
  - Q196 and SP107 are the same Babbitt Ranch plant.
- **Queue status vs reality.** Several plants are operating but still show "Pending LGIA Amended & Filed" or "COD-Pending Refund": Q268, Q357, Q314.

## Gaps
- **2025 ASRFP awards are not public.** 51 proposals were shortlisted and contracts are expected Jul–Dec 2026. Likely beneficiaries are 2025- and 2026-cluster storage and gas requests, but no names are out, so nothing is classed `aps_selected`.
- **2024 ASRFP.** The only public results are the two existing CC tolls.
- **The 2026 IRP is due 10/30/2026.** Re-run this check after it is filed and after any APS award release.
- **Projects identified but with no disclosed offtaker** (left out of contracted.csv):
  - Q288 = RWE Yellow Bell (200 MW, Panda 230 kV)
  - Q276 = Harquahala Sun 1
  - Q437 and Q475 = Harquahala Sun III/IV (Copia)
  - Q567 = a later Copia phase
  - Q310 = Hoodini (CAISO 1663)
  - Q334 (Sun Valley, 500/350 MW)
  - Q474 (Delaney, 1,000 MW)
  - Q227 (Hoodoo Wash, 500 MW)
  - Q289, Q291, Q280, Q297, Q315, Q313 (smaller storage and hybrid requests)
- **Dual queues.** Several Hoodoo Wash and Delaney requests are also in the CAISO queue (Q310, Q332, Q389, Q442), which hints at California buyers. Dual queueing is not evidence of a contract.
- **Sources not fetched:** ACC docket filings for PPA/BTA approvals and independent-monitor RFP reports. The 10-K and IRP tables were used instead.

## Key sources
- Pinnacle West FY2025 10-K: https://www.sec.gov/Archives/edgar/data/764622/000076462226000011/pnw-20251231.htm
- APS 2023 IRP (repo `research/areas/APS/files/IRP_APS_2023_IRP_PUBLIC.pdf`)
- RPAC decks (repo `files/IRP_RPAC_2026-0{4,7,9}_Presentation.pdf`; digests S8, S9, S10)
- APS Delaney-Sun Valley-Redhawk 2025 Cluster Phase 2A report (repo `data/queue/raw`)
- Saguaro expansion: https://www.aps.com/en/About/Construction-and-Power-Line-Siting/Reliability-upgrades/Saguaro_Power_Plant_Expansion_Project
- Desert Sun: https://www.aps.com/en/About/Construction-and-Power-Line-Siting/Reliability-upgrades/Desert_Sun_Power_Plant_Project
- Copia / APS PPAs: https://www.copiapower.com/news/copia-power-and-arizona-public-service-aps-enter-into-20-year-power-purchase-agreements
- Recurrent Papago Storage: https://recurrentenergy.com/?p=20077
- Enlight CO Bar: https://www.clenera.com/news/enlight-clenera-reach-final-development-milestones-for-co-bar-its-largest-project-to-date
- Per-row URLs are in the CSVs.
