# Data sources and provenance (scadavt demand model)

Accessed: 2026-10-13. All downloads are in `data/raw/<source>/`. Nothing downloaded was executed.

## 1. Weekly per-person water use, Melbourne (PRIMARY, used for `consumption_per_person.csv`)

- **Publisher:** Melbourne Water Corporation (Victoria, Australia), "Water storage levels" web app.
- **App page:** https://www.melbournewater.com.au/water-and-environment/water-management/water-storage-levels
- **Definitions page:** https://www.melbournewater.com.au/node/118 ("Calculating water storage data")
- **Exact data URL** (one CSV per week; this is the "download CSV" link under the weekly water-use table in the app):
  `https://api.melbournewater.com.au/water-storage/levels/usage/week/file?searchDate=YYYY-MM-DD`
  (the request was sent with header `Origin: https://www.melbournewater.com.au`, as the web app sends it). Every Thursday from 2022-01-06 to 2026-10-08 was requested: 249 requests, all HTTP 200.
- **Raw files:** `raw/melbourne_water_weekly_use_per_person/csv/week_ending_YYYY-MM-DD.csv`, with checksums in `SHA256SUMS_csv.txt` and a log in `download_log.tsv`. Saved copies of the app page, the definitions page, the copyright page and two weekly report JSONs are in `provenance/`.
- **Raw file format:** `Daily water use,Daily average for week ending <d> (L),...` / `"Water use per person",<value>,`
- **Units:** litres per person per day, as a daily average over the 7 days to Thursday 08:00 (the stated week-ending date). Values are integers.
- **What it covers (publisher's wording):** "Melbourne's daily water use: includes households, business and some non-metropolitan areas, presented as a total (daily data), or as litres per person (calculated each week)." The publisher does not describe it as residential only, and the population denominator is not published.
- **Clean file:** `data/consumption_per_person.csv` (`date,litres_per_person_per_day`). `date` is the week-ending Thursday. Each value is copied from the source unchanged: no interpolation, no computed values.
  - Weeks: 245, from 2022-01-06 to 2026-10-01. Range 130 to 253 L/p/d.
  - Weeks the source leaves blank, so they are left out: 2023-09-07, 2023-09-28, 2024-02-29, and 2026-10-08 (the current week, not yet published).
  - Cross-check: for week ending 2025-06-26 the CSV gives 151. The weekly report text says "Over the past 7 days, Melburnians used an average of 151 litres of water per person per day" (`provenance/weekly_report_2025-06-26.json`).
  - Context only, not in the CSV (these are means of the weekly values I computed): FY2022/23 166.4, FY2023/24 168.5, FY2024/25 176.5. Melbourne's Annual Water Outlook reports *residential* use of 161, 163 and 169 L/p/d for the same years. The weekly series runs slightly above the annual residential figure, which fits the publisher's note that it includes business use.
  - The same endpoint also returns earlier years (checked back to 2009). The 2009–10 weeks of the original SCADAVT paper can be fetched with `python tools/fetch_consumption.py --from 2009-01-09 --to 2010-12-31 --out data/legacy/melbourne_2009_2010.csv`; they are not redistributed either.
- **Licence:** NOT an open licence. Melbourne Water's copyright and disclaimer page (saved copy in `provenance/`) says: "You must not use, reproduce, copy or transmit by any means any content on this website without Melbourne Water's prior written consent. This excludes fair dealing for the purposes of private study, research, criticism or review as permitted under the Copyright Act 1968." Use in a research paper with citation relies on fair dealing for research. Get written consent from Melbourne Water before redistributing the CSV (for example in a public repository).

## 2. Daily bulk water supplied, Melbourne Water (open licence; downloaded but NOT used)

- **Publisher:** Melbourne Water Corporation, via DataVic.
- **Catalogue:** https://discover.data.vic.gov.au/dataset/water-supply-daily-volume-drawn-from-storage-dams-operated-by-melbourne-water
- **File:** https://data-melbournewater.opendata.arcgis.com/api/download/v1/items/ab54f1679c3d4dd6b0f4030634fce861/csv?layers=0 saved to `raw/datavic_mw_daily_bulk_supply_volume/MWC_Daily_BulkWaterSupply_CompleteRecord.csv` (sha256 9dbce9b8...b112204).
- **Licence:** CC BY 4.0.
- **Units:** ML/day total (`supplied_ML`), not per person.
- **Range:** 1890-12-27 to 2020-02-28. It ends before the period we need, and it gives totals with no population, so it was not used.

## 3. Diurnal (hourly) pattern: no `diurnal_pattern.csv` produced

No peer-reviewed source I could access gives 24 hourly *residential* multipliers as numbers. Most smart-meter end-use studies show them only as figures. Under the "only stated values" rule, no file was produced. What was checked:

- **Gurung et al. (2014), Griffith author manuscript (`raw/gurung2014_smart_meters_demand_curves/`).** Uses SEQ smart-meter end-use data. Its Table 5 gives, as numbers, average-day demand of 220.0 L/p/d, average-day peak hour of 18.9 L/p/h, and an average-day peak-hour factor of **2.06** (modelled). Hervey Bay measured values: AD 190.6 L/p/d, peak hour 16.1 L/p/h, factor 2.02. Use this to check a chosen hourly profile, not as the profile itself. Hourly shapes appear only as figures (Figs 2 and 5).
- **EPANET example networks (`raw/epanet_example_networks/`, OpenWaterAnalytics/EPANET, MIT licence).**
  - Net1 Pattern 1 is 12 two-hourly multipliers: 1.0 1.2 1.4 1.6 1.4 1.2 1.0 0.8 0.6 0.4 0.6 0.8.
  - Net3 Pattern 1, the "General Default Demand Pattern", is 24 hourly multipliers: 1.34 1.94 1.46 1.44 .76 .92 .85 1.07 .96 1.1 1.08 1.19 1.16 1.08 .96 .83 .79 .74 .64 .64 .85 .96 1.24 1.67.
  - These values are numeric and citable, but they are teaching examples, not measured residential demand (Net3 peaks at 01:00). They were therefore not written to `diurnal_pattern.csv`.
- **Not reachable from this environment (403 or bot challenge):** MDPI and Springer full texts, Rizvi et al. 2021 (IWA Water Supply, Dubai residential hourly profiles), and Lucas et al. on ResearchGate. Rizvi et al. 2021 and Beal & Stewart (2011, SEQREUS) are the next to check by hand for a numeric hourly table.

## BibTeX

```bibtex
@misc{MelbourneWater_WeeklyUse_2026,
  author       = {{Melbourne Water Corporation}},
  title        = {Water storage levels: weekly water use per person (daily average for week ending Thursday 8am), week ending 6 Jan 2022 to 1 Oct 2026},
  howpublished = {\url{https://www.melbournewater.com.au/water-and-environment/water-management/water-storage-levels}; CSV via \url{https://api.melbournewater.com.au/water-storage/levels/usage/week/file?searchDate=YYYY-MM-DD}},
  year         = {2026},
  note         = {Litres per person per day; includes households, business and some non-metropolitan areas. Accessed 2026-10-13}
}

@misc{MelbourneWater_CalcData_2026,
  author       = {{Melbourne Water Corporation}},
  title        = {Calculating water storage data},
  howpublished = {\url{https://www.melbournewater.com.au/node/118}},
  year         = {2026},
  note         = {Accessed 2026-10-13}
}

@misc{MelbourneWater_DataVic_BulkSupply,
  author       = {{Melbourne Water Corporation}},
  title        = {Water Supply -- Daily Volume Drawn from storage dams operated by Melbourne Water},
  howpublished = {DataVic, \url{https://discover.data.vic.gov.au/dataset/water-supply-daily-volume-drawn-from-storage-dams-operated-by-melbourne-water}},
  year         = {2025},
  note         = {CC BY 4.0. Last updated 2025-02-10. Accessed 2026-10-13}
}

@article{Gurung2014SmartMeters,
  author  = {Gurung, Thulo Ram and Stewart, Rodney A. and Sharma, Ashok K. and Beal, Cara D.},
  title   = {Smart meters for enhanced water supply network modelling and infrastructure planning},
  journal = {Resources, Conservation and Recycling},
  volume  = {90},
  pages   = {34--50},
  year    = {2014},
  doi     = {10.1016/j.resconrec.2014.06.005},
  note    = {Author manuscript: \url{https://research-repository.griffith.edu.au/items/83102484-c835-5905-aebf-8097fbb44089}. Accessed 2026-10-13}
}

@misc{EPANET_ExampleNetworks,
  author       = {{OpenWaterAnalytics} and {U.S. Environmental Protection Agency}},
  title        = {{EPANET} example networks {Net1.inp} and {Net3.inp}},
  howpublished = {\url{https://github.com/OpenWaterAnalytics/EPANET/tree/master/example-networks}},
  note         = {MIT License. Accessed 2026-10-13}
}

@techreport{Rossman2020EPANET22,
  author      = {Rossman, Lewis and Woo, Hyoungmin and Tryby, Michael and Shang, Feng and Janke, Robert and Haxton, Terranna},
  title       = {{EPANET} 2.2 User Manual},
  institution = {U.S. Environmental Protection Agency},
  number      = {EPA/600/R-20/133},
  year        = {2020}
}
```
