# Data dictionary

## storm_overflows.gpkg

Anglian Water storm overflows from the EDM 2025 Storm Overflow Annual Return
(`data/raw/EDM_2025_Storm_Overflow_Annual_Return.zip`, sheet "All WaSC"),
clipped to the AOI. Column names are shortened from the spreadsheet headings
by `EDM_COLUMNS` in `scripts/prepare_data.py`. Line breaks in the original
headings are shown here as single spaces.

| Column | Original spreadsheet heading |
|---|---|
| `unique_id` | Unique ID |
| `company` | Water Company Name |
| `site_name` | Site Name (EA Consents Database) |
| `site_name_operational` | Site Name (WaSC operational) [optional] |
| `permit_ref` | EA Permit Reference (EA Consents Database) |
| `permit_ref_supplementary` | WaSC Supplementary Permit Ref. [optional] |
| `activity_ref` | Activity Reference on Permit |
| `asset_type` | Storm Discharge Asset Type |
| `outlet_ngr` | Outlet Discharge NGR (EA Consents Database) |
| `wfd_waterbody_id` | WFD Waterbody ID (Cycle 3) (discharge outlet) |
| `wfd_catchment` | WFD Waterbody Catchment Name (Cycle 3) (discharge outlet) |
| `receiving_water` | Receiving Water / Environment (common name) (EA Consents Database) |
| `shellfish_water` | Shellfish Water(s) (only populate for storm overflow with a Shellfish Water EDM requirement) |
| `bathing_water` | Bathing Water(s) (only populate for storm overflow with a Bathing Water EDM requirement) |
| `treatment_method` | Treatment Method on Permit (over & above Storm Tank settlement / screening) |
| `spill_duration_total` | Total Duration (hh:mm:ss) all spills prior to processing through 12-24h count method |
| `spill_count` | Counted spills using 12-24h count method |
| `spill_count_lt_avg` | Long-term average spill count |
| `data_start_year` | Data start - calendar year |
| `edm_operational_pct` | EDM Operation - % of reporting period EDM operational |
| `edm_low_reason` | EDM Operation - Reporting % - Primary Reason <90% |
| `edm_low_action` | EDM Operation - Action taken / planned - Status & timeframe |
| `high_spill_reason_year` | High Spill Frequency - Operational Review - Single year reason |
| `high_spill_reason_lt_avg` | High Spill Frequency - Operational Review - Long-term average reason |
| `investigation` | Investigation activity for the reporting period |
| `improvement` | Improvement activity for the reporting period |

Added by `prepare_data.py`:

| Column | Description |
|---|---|
| `easting` | Easting (m, EPSG:27700) converted from `outlet_ngr`. 10-figure references give the point exactly; shorter ones use the centre of the grid square. |
| `northing` | Northing (m, EPSG:27700) converted from `outlet_ngr`, as above. |
