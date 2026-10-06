# SSSI Impact Risk Zone code mapping

The IRZ download (`data/raw/SSSI_Impact_Risk_Zones_England.json`) has no
per-category columns. Each zone's rules are packed into a 13-digit `irzcode`
inside its `irzurl`, for example
`https://irz.geodata.org.uk/IRZ/step2.html?irzcode=0101254322231&...`.

This note records how that code was decoded and verified on 2026-10-02.

## How it was verified

The Appendix 3 table in Natural England's
[SSSI IRZ User Guidance v5.4](https://magic.defra.gov.uk/Metadata_for_magic/SSSI%20IRZ%20User%20Guidance%20MAGIC.pdf) (2 April 2025; also linked from the
[dataset page](https://www.data.gov.uk/dataset/5ae2af0c-1363-4d40-9d1a-e5a1381449f8/sssi-impact-risk-zones-england1)) lists 13 development categories but does not describe the code. The code is
decoded by the IRZ web tool itself:

- `step2.html` splits `irzcode` into single digits and pairs them, in order,
  with 13 section keys:
  `ac, in, ws, og, rn, re, rr, ap, cb, wa, cp, dc, wt`.
- A `0` hides that section. Any other digit `d` loads the advice text from
  `https://irz.geodata.org.uk/templates/<section><d>.html`.
- The first digit also controls routing: `1` sends the user straight to
  "consult Natural England on all planning applications", `2` sends them to a
  householder-application check, and an all-zero code goes to "no
  consultation needed".

We downloaded the template for every section/digit combination that occurs
in our AOI (41 templates). We did not open the pages in a browser: when the
code starts with 1, the page posts a "consultation needed" record to the
site's server, and we did not want to create those records.

## (a) Digit position → category

The positions match the Appendix 3 category order exactly.

| Digit | Section key | Category | Column in `irz.gpkg` |
|---|---|---|---|
| 1 | ac | All Consultations | `irz_all_consultations` |
| 2 | in | Infrastructure | `irz_infrastructure` |
| 3 | ws | Wind & Solar Energy | `irz_wind_solar` |
| 4 | og | Minerals, Oil and Gas | `irz_minerals_oil_gas` |
| 5 | rn | Rural Non Residential | `irz_rural_non_residential` |
| 6 | re | Residential | `irz_residential` |
| 7 | rr | Rural Residential | `irz_rural_residential` |
| 8 | ap | Air Pollution | `irz_air_pollution` |
| 9 | cb | Combustion | `irz_combustion` |
| 10 | wa | Waste | `irz_waste` |
| 11 | cp | Composting | `irz_composting` |
| 12 | dc | Discharges | `irz_discharges` |
| 13 | wt | Water Supply | `irz_water_supply` |

## (b) Digit values

`0` always means the category is not triggered in that zone. Any other value
selects a description of which developments should be referred to Natural
England. The values below are those that occur in our AOI; the wording is
summarised from the templates.

| Category | Value → developments that trigger consultation |
|---|---|
| All Consultations | 1 all planning applications · 2 all except householder applications · 3 non-householder applications outside settlements affecting lighting, greenspace, farmland, semi-natural habitat, woodland, water, rural buildings or linear features · 4 as 3 but without greenspace, farmland and semi-natural habitat |
| Infrastructure | 1 pipelines, cables, pylons, any new transport, aviation · 3 aviation only |
| Wind & Solar | 1 solar > 0.5 ha and all wind turbines · 2 wind turbines only |
| Minerals, Oil and Gas | 1 all quarry applications and oil & gas · 2 new or extended quarries outside settlements, and oil & gas · 3 oil & gas only |
| Rural Non Residential | 2 > 1,000 m² floorspace or > 0.2 ha footprint, outside settlements · 3 footprint > 1 ha, outside settlements |
| Residential | 1 any net gain in units · 2 ≥ 10 units · 4 ≥ 50 units · 5 ≥ 100 units |
| Rural Residential | as Residential (1, 2, 4, 5), outside settlements only |
| Air Pollution | 1 anything causing air pollution or dust · 2 anything causing air pollution · 3 industrial/agricultural, livestock units > 500 m², slurry > 200 m², manure > 250 t · 4 as 3 with slurry > 750 m², manure > 3,500 t · 5 livestock/slurry/manure only, at the 4 thresholds · 6 livestock > 500 m², slurry > 4,000 m² |
| Combustion | 1 all combustion · 2 > 20 MW energy input · 3 > 50 MW |
| Waste | 1 all waste management incl. landfill · 2 landfill only |
| Composting | 1 any composting · 2 > 500 t/yr · 3 > 75,000 t/yr |
| Discharges | 2 any discharge to ground or surface water · 3 > 2 m³/day · 4 > 5 m³/day · 5 > 20 m³/day |
| Water Supply | 1 own water supply, or > 1,000 m² new floorspace · 2 ≥ 1,000 m² new floorspace only |

Within a category, a higher value means fewer developments trigger
consultation. Sometimes that is a higher size threshold (Discharges,
Combustion, Composting, Residential) and sometimes a narrower list of
development types (Infrastructure 3 is aviation only, Waste 2 is landfill
only). So the values are an ordinal scale of how restrictive a zone is, not
a single numeric threshold.

## Worked examples

Nine zones in the AOI, chosen to cover non-zero digits in positions 2, 9, 12
and 13 and a range of other values.

| irzcode | Location (BNG) | Decoded |
|---|---|---|
| 0101254322231 | 532964, 227580 | Infrastructure 1, Oil/Gas 1, Rural non-res 2, Residential 5, Rural res 4, Air 3, Combustion 2, Waste 2, Composting 2, Discharges 3 (> 2 m³/day), Water 1 |
| 0101305322342 | 533050, 228312 | Infrastructure 1, Oil/Gas 1, Rural non-res 3, Rural res 5, Air 3, Combustion 2, Waste 2, Composting 3, Discharges 4 (> 5 m³/day), Water 2 |
| 0101011111151 | 555724, 225344 | Infrastructure 1, Oil/Gas 1, Residential 1, Rural res 1, Air 1, Combustion 1 (all), Waste 1, Composting 1, Discharges 5 (> 20 m³/day), Water 1 |
| 0101000322302 | 534765, 226577 | Infrastructure 1, Oil/Gas 1, Air 3, Combustion 2, Waste 2, Composting 3, Water 2 |
| 0303011211100 | 535493, 239943 | Infrastructure 3 (aviation), Oil/Gas 3, Residential 1, Rural res 1, Air 2, Combustion 1, Waste 1, Composting 1 |
| 0301055530050 | 538846, 243061 | Infrastructure 3, Oil/Gas 1, Residential 5, Rural res 5, Air 5, Combustion 3, Discharges 5 |
| 4300055530000 | 578876, 261954 | All Consultations 4, Infrastructure 3, Residential 5, Rural res 5, Air 5, Combustion 3 |
| 0000055630000 | 529237, 244148 | Residential 5, Rural res 5, Air 6, Combustion 3 |
| 2000000000000 | 533448, 226579 | All Consultations 2 (all except householder) |

## Things that don't match or aren't covered

- **Not every value is used.** Some values never occur in our AOI (for
  example Infrastructure 2, Discharges 1, Residential 3), so they were not
  fetched and are not listed above.
- **Category names differ slightly.** The web templates say "Compost",
  "Discharge" and "Wind and Solar" where Appendix 3 says "Composting",
  "Discharges" and "Wind & Solar Energy". They are the same categories.
- **The `notes` part of the URL is not decoded.** Some URLs carry extra note
  IDs (for example `notes=11604+12403`), which add site-specific advice. They
  are kept in `irzurl` but not split out.
- **The wording can change.** Natural England updates the IRZs every two
  months, and the templates are served live, so the text behind a value can
  change between versions. The table above reflects the templates on
  2026-10-02.
