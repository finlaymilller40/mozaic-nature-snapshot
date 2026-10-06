# Technical notes

Reference detail behind the README: the screened area, the data and how it
was prepared, Natural England's Impact Risk Zone codes, the screening rules,
how sites are screened, uploads, the API, the project layout and the tests.

The reasoning behind each design choice, with dates, is in
[`DECISIONS.md`](../DECISIONS.md). How the risk-zone code was decoded is in
[`irz_code_mapping.md`](irz_code_mapping.md). The rules themselves are in
[`app/rules.yaml`](../app/rules.yaml).

Figures about the data (row counts, zone counts, distances, regression
counts) are from the data downloaded and prepared on 2026-10-02.

## Contents

1. [The screened area](#the-screened-area)
2. [The data](#the-data)
3. [Natural England's Impact Risk Zone codes](#natural-englands-impact-risk-zone-codes)
4. [Assurance levels and the decision tree](#assurance-levels-and-the-decision-tree)
5. [Work types and the risk-zone values they trigger](#work-types-and-the-risk-zone-values-they-trigger)
6. [How sites are screened](#how-sites-are-screened)
7. [Worked examples](#worked-examples)
8. [Rebuilding the data](#rebuilding-the-data)
9. [Uploading coordinates](#uploading-coordinates)
10. [API](#api)
11. [Project layout](#project-layout)
12. [Tests](#tests)
13. [Repository notes](#repository-notes)

---

## The screened area

A rectangle in British National Grid (EPSG:27700): easting 488,000–585,000 and
northing 225,000–325,000. That is 97 km × 100 km, 9,700 km², covering
Cambridgeshire and Peterborough with a margin on every side so features near
the county boundary aren't clipped.

- A simple rectangle is easy to reproduce and to upload to other tools.
- British National Grid uses metres, so the extent and area are exact.
- A WGS84 (latitude/longitude) copy is also kept. Its edges were densified
  with a vertex every 1 km before reprojecting, because a rectangle in
  British National Grid is not a rectangle in latitude/longitude. The WGS84
  copy still measures 9,700 km² when converted back.
- Files: `data/aoi/cambs_aoi.zip` and `data/aoi/cambs_aoi_wgs84.zip`, made by
  `scripts/make_aoi.py`.

---

## The data

All layers are reprojected to British National Grid, clipped to the screened
area and saved as GeoPackages in `data/clean/` by `scripts/prepare_data.py`.

| Layer | File | Rows | Size | What it is |
|---|---|---|---|---|
| SSSIs | `sssi.gpkg` | 279 | 3.4 MB | Sites of Special Scientific Interest (about 45,500 ha in the area) |
| SSSI units | `sssi_units.gpkg` | 1,056 | 5.5 MB | The management units of each SSSI, with their condition |
| SACs | `sac.gpkg` | 46 polygons, 15 sites | 0.5 MB | Special Areas of Conservation |
| SPAs | `spa.gpkg` | 79 polygons, 6 sites | 1.2 MB | Special Protection Areas |
| Ramsar sites | `ramsar.gpkg` | 29 polygons, 9 sites | 0.6 MB | Wetlands of international importance |
| Ancient woodland | `ancient_woodland.gpkg` | 2,159 | 7.5 MB | 2,114 woods from the revised inventory, 45 from the original |
| Priority habitat | `priority_habitat.gpkg` | 33,131 | 21.5 MB | Priority Habitat Inventory (about 71,900 ha) |
| Impact Risk Zones | `irz.gpkg` | 8,749 | 13.0 MB | Natural England's SSSI Impact Risk Zones, with the 13-digit code split into one column per category |
| Storm overflows | `storm_overflows.gpkg` | 461 | 0.3 MB | Anglian Water storm overflows from the EDM 2025 annual return. Used as a test set, not shown in the app |

European sites in the area include the Ouse Washes, Nene Washes, Fenland,
Breckland, Wicken Fen, Chippenham Fen, Woodwalton Fen, Portholme, Orton Pit,
Devils Dyke, Eversden and Wimpole Woods, Upper Nene Valley Gravel Pits,
Rutland Water and parts of The Wash.

SSSI unit condition in the area: 343 favourable, 365 unfavourable
recovering, 131 unfavourable no change, 208 unfavourable declining, 7
destroyed, 1 part destroyed and 1 not assessed.

### Choices made while preparing the data

Details in `DECISIONS.md`.

- **Ancient woodland: the revised inventory wherever it has coverage.** The
  revised Ancient Woodland Inventory covers most of the area but not the
  north-east corner (around Castle Rising, Congham and Reffley woods, which
  appears to be West Norfolk) or part of the western edge (around Hollington
  and Thickthorn woods). The data has no coverage boundary, so coverage is
  taken as land within 3 km of a revised wood. Original-inventory woods whose
  centre lies outside that are added with `source = "original"`. Inside
  coverage, adding original woods back would bring back woods the revision
  deliberately removed. The 3 km line is a judgement; county boundaries for
  the revision projects would be cleaner.
- **Priority habitat slimmed down.** The clipped file was 77.9 MB, over
  GitHub's 50 MB warning size. It now keeps only `uid`, `mainhabs`,
  `addhabs`, a recomputed `area_ha` and the geometry, with shapes simplified
  at a 1 m tolerance (keeping topology) and repaired. Vertices fell from 3.69
  to 0.86 million and total area changed by 0.02%. `area_ha` is recomputed
  because the source `areaha` is the area before clipping. The download has
  no confidence column, so none is kept.
- **Risk-zone codes decoded** into 13 columns (next section).
- **Storm overflow columns renamed** from long spreadsheet headings to short
  snake_case names (for example `spill_count`, `outlet_ngr`). The lookup is
  `EDM_COLUMNS` in `scripts/prepare_data.py`, listed in
  [`data_dictionary.md`](data_dictionary.md). The script stops with an error
  if the spreadsheet gains a column the lookup doesn't cover, so new columns
  aren't silently mislabelled. Outlet grid references are converted to
  easting/northing (10-figure references exactly; shorter ones use the
  centre of the grid square).

Natural England updates the Impact Risk Zones every two months, so results
reflect the 2026-10-02 snapshot.

---

## Natural England's Impact Risk Zone codes

Natural England publishes SSSI Impact Risk Zones (IRZs) to tell planners when
a development might affect an SSSI and Natural England should be consulted
([dataset](https://www.data.gov.uk/dataset/5ae2af0c-1363-4d40-9d1a-e5a1381449f8/sssi-impact-risk-zones-england1),
[user guidance v5.4](https://magic.defra.gov.uk/Metadata_for_magic/SSSI%20IRZ%20User%20Guidance%20MAGIC.pdf)).
The download has no per-category columns. Each zone's rules are packed into
a **13-digit code** (`irzcode`) inside a link to Natural England's online IRZ
tool, for example:

```
https://irz.geodata.org.uk/IRZ/step2.html?irzcode=0101254322231&notes=&location=...
```

**Each digit position is one category of development**, in the same order as
Appendix 3 of the IRZ User Guidance. **Each value says which developments of
that category Natural England wants to be consulted on in that zone.** `0`
means the category doesn't trigger consultation in that zone; higher values
mean only larger, or more specific kinds of, development trigger it.

The guidance lists the categories but doesn't describe the code. The code was
decoded from the IRZ web tool's own page code and advice templates on
2026-10-02: the tool splits `irzcode` into single digits, pairs them in order
with 13 section keys, and loads the advice text for each non-zero digit from
a template. The templates for every category and value that occur in our
area (41 in all) were downloaded and summarised. The pages weren't opened in
a browser, because a code starting with 1 makes the page post a
"consultation needed" record to Natural England's server. Full details and
nine examples are in [`irz_code_mapping.md`](irz_code_mapping.md). In all
8,749 zones, the decoded columns match the code's digits.

### The 13 positions

| # | Category | Used by our rules |
|---|---|---|
| 1 | All Consultations | ✓ |
| 2 | Infrastructure | ✓ |
| 3 | Wind & Solar Energy | |
| 4 | Minerals, Oil and Gas | |
| 5 | Rural Non-Residential | ✓ |
| 6 | Residential | |
| 7 | Rural Residential | |
| 8 | Air Pollution | ✓ |
| 9 | Combustion | ✓ |
| 10 | Waste | |
| 11 | Composting | ✓ |
| 12 | Discharges | ✓ |
| 13 | Water Supply | ✓ |

### What the values mean (categories we use)

Summarised from Natural England's templates as they were on 2026-10-02.
Only values that occur in our area are listed. Values that never occur here
(for example Infrastructure 2 or Discharges 1) weren't fetched, so their
meaning isn't known to us.

| Category | Value → developments that trigger consultation |
|---|---|
| All Consultations | 1 all planning applications · 2 all except householder applications · 3 non-householder applications outside settlements affecting lighting, greenspace, farmland, semi-natural habitat, woodland, water, rural buildings or linear features · 4 as 3, without greenspace, farmland and semi-natural habitat |
| Infrastructure | 1 pipelines, cables, pylons, any new transport, aviation · 3 aviation only |
| Rural Non-Residential | 2 over 1,000 m² floorspace or over 0.2 ha footprint, outside settlements · 3 footprint over 1 ha, outside settlements |
| Air Pollution | 1 anything causing air pollution or dust · 2 anything causing air pollution · 3 industrial and agricultural processes, livestock buildings over 500 m², slurry over 200 m², manure over 250 t · 4 as 3 with slurry over 750 m² and manure over 3,500 t · 5 livestock, slurry and manure only, at the value-4 thresholds · 6 livestock over 500 m², slurry over 4,000 m² |
| Combustion | 1 all combustion · 2 over 20 MW energy input · 3 over 50 MW |
| Composting | 1 any composting · 2 over 500 t a year · 3 over 75,000 t a year |
| Discharges | 2 any discharge to ground or surface water · 3 over 2 m³/day · 4 over 5 m³/day · 5 over 20 m³/day |
| Water Supply | 1 developments needing their own water supply, or over 1,000 m² new floorspace · 2 1,000 m² or more new floorspace only |

The full table for all 13 categories is in
[`irz_code_mapping.md`](irz_code_mapping.md).

### Direction of the values

In every category we use, **a higher value means fewer developments trigger
consultation**; no reversals were found. It narrows in two ways:

- **by size**: Combustion, Composting, Discharges and Rural Non-Residential
  raise a threshold;
- **by type**: Infrastructure 3 is aviation only, All Consultations narrows
  from all applications to specific kinds, and Water Supply 2 drops the
  "own water supply" case.

Air Pollution mixes both. Values aren't consecutive (Discharges starts at 2;
Infrastructure has only 1 and 3 here), so they are an ordered scale of how
demanding a zone is, not numbers. The code never assumes the direction:
`rules.yaml` lists the exact trigger values for each work type.

### Distance

Distance isn't in the code or the data. It only shows in where Natural
England draws the zones; the download carries just each zone's outline and
its link. The site-specific notes in the links (`notes=…`) aren't decoded.

From a 250 m grid of points over the area:

- **The zones tile the whole screened area, with no gaps and no overlaps.**
  They aren't separate rings around each SSSI: every point falls in exactly
  one zone, which carries one combined code. 5.4% of the area is in zones
  whose code is all zeros (40 zones).
- **Lower values sit closer to SSSIs** in every category we use. Median
  distance to the nearest SSSI:

| Category | Value → median distance to nearest SSSI |
|---|---|
| All Consultations | 1: 0.0 km · 2: 0.0 km · 3: 1.8 km · 4: 2.2 km |
| Infrastructure | 1: 1.3 km · 3: 3.2 km |
| Rural Non-Residential | 2: 1.0 km · 3: 1.8 km |
| Air Pollution | 1: 0.1 · 2: 0.4 · 3: 1.2 · 4: 2.9 · 5: 3.1 · 6: 5.9 km |
| Combustion | 1: 0.3 km · 2: 1.2 km · 3: 3.4 km |
| Composting | 1: 0.3 km · 2: 0.8 km · 3: 1.5 km |
| Discharges | 2: 0.2 km · 3: 0.7 km · 4: 1.6 km · 5: 2.8 km |
| Water Supply | 1: 0.6 km · 2: 1.4 km |

- A typical point in the area is 2.4 km from an SSSI, and 95% of the area
  is within 7.9 km of one.

Caveats: the SSSI and IRZ layers are both clipped to the area, so SSSIs just
outside it are missing and distances near the edges are overstated. Zones
also respond to SAC, SPA and Ramsar sites and to each site's sensitivity, so
the ranges overlap and distance alone doesn't predict a value. Residential
(which the rules don't use) breaks the pattern: its value-1 zones have a
median distance of 1.2 km, further out than its higher values.

### What a zero means

A zero means that category doesn't trigger consultation in that zone. It
doesn't mean no consultation is needed at all: the project may still be
caught by All Consultations or by another category it falls under. The risk
zones only cover risks to SSSIs; European sites and ancient woodland apply
regardless, which is why the rules check those layers directly.

---

## Assurance levels and the decision tree

| Level | Meaning (as shown in the app) |
|---|---|
| A1 | Nothing found. A desk check to confirm is enough. |
| A2 | An ecologist should review this from records. |
| A3 | Specialist assessment needed, likely with Natural England input. |
| A4 | Specialist assessment plus a site survey. |

Each site, section or piece is checked against these rules, in this order
(`rules` in `app/rules.yaml`). **The highest level found wins, and every
reason is listed.**

| # | Rule | Level | May escalate to |
|---|---|---|---|
| 1 | Inside a SAC, SPA or Ramsar site | A3 | A4 |
| 2 | Inside an SSSI | A3 | A4 |
| 3 | Within 15 m of ancient woodland (Natural England and Forestry Commission standing advice) | A3 | A4 |
| 4 | Risk zone All Consultations value is 1 or 2 | A3 | A4 |
| 5 | Abstraction or dewatering is "yes", plus any risk-zone trigger (rule 6's triggers, including those added by the intake answers) | A3 | – (note: a groundwater specialist may be needed) |
| 6 | A risk-zone trigger for the work type (or added by an intake answer) | A2 | A3 |
| 7 | Overlaps priority habitat | A2 | A3 |
| 8 | Nothing found ("confirm remotely") | A1 | – |
| 9 | The intake must have a yes or no for every question; "unknown" isn't accepted | – | – |
| 10 | A physical survey is required: an A3 becomes A4 | A4 | – |

How the result is put together (`score_site` in `app/scoring.py`):

- Every rule that applies adds a reason. The level is the highest of them.
- "May escalate to" is the highest escalation among the reasons at that top
  level. It tells the user the level could rise on closer review.
- Rule 10 only applies when the result is A3. An A2 stays A2 even if a
  survey is required.
- **Context only, never changes the level:** the condition of the nearest
  SSSI unit (with its distance and assessment date), whether a triggered risk
  zone is linked to a European site, and direction of effect (upstream or
  downstream), which isn't available from the data and is left for the
  assessor.
- Rule 9 is enforced on every request: a missing or "unknown" answer is
  rejected with a message listing what's missing. Answers accept `yes`, `y`,
  `true` and `no`, `n`, `false`.

### All Consultations: how each value is treated

| Value | Treatment |
|---|---|
| 1 or 2 | **Always A3** (may rise to A4), whatever the work type (rule 4) |
| 3 or 4 | **A2, only if the work goes beyond the existing site** (rule 6, through the `beyond_site` intake answer). Rises to A3 if abstraction or dewatering is also "yes" (rule 5) |
| 0 | Nothing from this category |

Rationale: values 1 and 2 mean Natural England wants to see almost any
planning application in the zone, so the type of work doesn't matter.
Values 3 and 4 only cover applications outside settlements that affect
farmland, woodland, water, linear features and similar, which work inside an
existing operational site normally doesn't touch.

---

## Work types and the risk-zone values they trigger

Which risk-zone values count as a trigger for each type of work (rule 6).
Values assume typical water company scale. From `work_types` in
`app/rules.yaml`.

| Work type | Shape to draw | Risk-zone category (position) | Trigger values | What that means |
|---|---|---|---|---|
| New pipeline / sewer | line | Infrastructure (2) | 1 | Pipelines, cables, pylons, transport |
| Pipe upgrade / refurbishment | line | – | none | The guidance excludes "upgrades and refurbishment of existing network" from Infrastructure 1 |
| Storm overflow / outfall / works discharge | point | Discharges (12) | 2–5 | Any discharge, or over 2, 5 or 20 m³/day |
| Treatment works with combined heat and power | area | Combustion (9) | 1 | All combustion |
| Sludge / anaerobic digestion | area | Composting (11); Air Pollution (8) | Composting 1–2; Air Pollution 1–4 | Any composting or over 500 t a year; air pollution up to industrial processes |
| Storm tank / larger works | area | Rural Non-Residential (5) | 2; and 3 if the site is 1 ha or more | Over 0.2 ha footprint or 1,000 m² floorspace; footprint over 1 ha |
| New or increased abstraction | point | Water Supply (13) | 1 (approximation) | Own water supply, or over 1,000 m² new floorspace |

Intake answers can add triggers on top of the work type (`intake_triggers`):

| Answer | Adds |
|---|---|
| Work beyond the existing site = yes | All Consultations 1–4 |
| Abstraction = yes | Water Supply 1 (approximation) |

### Assumptions

- **Discharges:** storm overflows trigger on any value from 2 to 5, whatever
  the discharge volume. Water company discharges are assumed to exceed
  20 m³/day; this isn't checked.
- **Combustion:** CHP is only flagged where the value is 1 (all
  combustion). In value 2 or 3 zones (over 20 or 50 MW) it isn't, on the
  assumption that water company CHP is under 20 MW. In the area, 2,437 zones
  have Combustion 1, 2,972 have 2 and 1,505 have 3.
- **Water Supply stands in for abstraction.** Natural England's category is
  about developments needing their own water supply, not abstraction as such.
  It's marked as an approximation in `rules.yaml` and `DECISIONS.md`.
- **Sludge and anaerobic digestion** use Air Pollution 1–4, treating AD as an
  industrial process that emits ammonia. This is a cautious reading; values
  3–4 cover industrial processes.
- **Pumping stations aren't a work type.** They were removed from the
  Rural Non-Residential row because most are under its 0.2 ha footprint and
  1,000 m² floorspace thresholds, so listing them would flag most pumping
  station projects that the guidance wouldn't. A large pumping station fits
  "Storm tank / larger works", and any pumping station on new land is caught
  by the "beyond the existing site" question.
- **"Outside settlements"** (part of the Rural Non-Residential and All
  Consultations 3–4 wording) isn't checked; the app has no settlement
  boundaries.

---

## How sites are screened

All measuring is done in British National Grid metres (`app/sites.py`).
Settings are under `site_geometry` in `app/rules.yaml`.

**Points** (outfalls, abstraction points) are screened as a circle of
**50 m radius**, split into pieces like an area (below).

**Lines** (pipelines) are screened as a **corridor 15 m either side** of the
route (30 m wide), cut into **50 m sections** along the centreline. A
remainder under 25 m joins the last section. Each section is scored with the
same rules as any site. Neighbouring sections with the same level and the
same possible escalation merge into **stretches**, which the map colours,
and each stretch lists all the reasons that apply along it. The pipeline's
overall level is its highest stretch, and each reason says where along the
route it applies (for example "0.80–0.95 km").

- Distances along the route always count the whole route, including any part
  outside the screened area.
- Area-based rules (Rural Non-Residential 3, sites of 1 ha or more) use the
  whole route's corridor area, so cutting it into sections doesn't switch
  them off.
- Rule 10 (survey required) applies to each section, so a section can be A4.
- Up to 1,200 sections per screening (about 60 km of route). A 50 km route
  (1,000 sections) screens in about 1.6 seconds.
- "Inside" a site means the **corridor** overlaps it, even if the centreline
  doesn't. The AI's facts sheet and the assistant's instructions say so
  explicitly.

**Areas** (works, storm tanks, AD plants) are cut wherever the boundary of a
European site, SSSI, priority habitat polygon, risk zone or the 15 m buffer
around ancient woodland crosses them. Each piece is checked using a copy
shrunk by **5 cm**, so a piece that only touches a feature along its edge
doesn't count as overlapping it. Pieces with the same level and possible
escalation merge into parts. Area-based rules use the whole site's area, not
one piece. Above 400 pieces, the area is scored as one piece.

Parts merge by level rather than by identical reasons because merging only
identical reason sets left thin slivers where neighbouring protected sites'
boundaries are digitised slightly differently.

**Sites partly outside the screened area:** the part inside is screened; the
part outside is shown grey and dashed with a warning such as "3.0 km of this
route is outside the screened area and has not been checked" or "200.0 ha of
this site is outside…". This applies to lines, polygons and the 50 m circle
around points. A point within 1 m of the area counts as inside, to allow for
rounding when converting coordinates. Slivers under 1 m (or 1 m²) are
ignored. Sites wholly outside are "not screened".

**Limits per request:** 2,000 sites and 1,200 route sections. Uploads are
limited to 5 MB.

**Map display:** the protected features returned with the results are
simplified for drawing only (10 m for SSSIs and European sites, 3 m for
woodland and habitat, 20 m for risk zones). Screening always uses the full
shapes.

**Coordinates:** uploaded British National Grid coordinates are sent to the
server unconverted (`crs: EPSG:27700`), so the only conversion used for
screening is the server's (pyproj, a standard transformation accurate to a
few metres). The browser converts only for display.

---

## Worked examples

### A risk zone decoded: `0101254322231`

97 zone polygons in the area carry this code (about 62 km² in all).

| Position | Digit | Category | Meaning |
|---|---|---|---|
| 1 | 0 | All Consultations | Not triggered |
| 2 | 1 | Infrastructure | Pipelines, cables, pylons, transport |
| 3 | 0 | Wind & Solar | Not triggered |
| 4 | 1 | Minerals, Oil and Gas | All quarries and oil & gas |
| 5 | 2 | Rural Non-Residential | Over 1,000 m² floorspace or 0.2 ha footprint |
| 6 | 5 | Residential | 100 or more units |
| 7 | 4 | Rural Residential | 50 or more units, outside settlements |
| 8 | 3 | Air Pollution | Industrial and larger agricultural |
| 9 | 2 | Combustion | Over 20 MW |
| 10 | 2 | Waste | Landfill only |
| 11 | 2 | Composting | Over 500 t a year |
| 12 | 3 | Discharges | Over 2 m³/day |
| 13 | 1 | Water Supply | Own water supply, or over 1,000 m² floorspace |

Screened at a point inside one of these zones (BNG 521679, 283918) where no
other protected feature applies:

| Work | All answers "no" | Dewatering "yes" | Beyond the site "yes" |
|---|---|---|---|
| Storm overflow | **A2** (Discharges 3 is in 2–5) | **A3** (rule 5) | A2 (All Consultations is 0 here) |
| New pipeline | **A2** (Infrastructure 1) | **A3** (rule 5) | A2 |

A treatment works with CHP here would not be flagged by its work type,
because Combustion is 2 (over 20 MW) and the rules only trigger on 1.

### A pipeline across the Ouse Washes

A 2.24 km route from BNG 542000, 279000 to 544000, 280000, as a new pipeline
that goes beyond the existing site and needs dewatering, screens as **A3
overall**, in two stretches:

- **0.00–0.95 km: A3, may rise to A4.** Inside the Ouse Washes SAC, SPA,
  Ramsar site and SSSI, in a risk zone with All Consultations 1, with
  dewatering plus risk-zone triggers (rule 5), and overlapping priority
  habitat.
- **0.95–2.24 km: A3.** Only through rule 5: dewatering plus risk-zone
  triggers (All Consultations 3, because the work goes beyond the site, and
  Infrastructure 1 for the new pipeline). It has no escalation, so it stays a
  separate stretch from the first.

The tests use this route (`tests/test_ai_features.py`).

### All 461 Anglian Water storm overflows

Screened as storm overflow work with every answer "no": 126 A1, 307 A2,
28 A3, 0 A4. This is a regression test (`tests/test_api.py`), so any rule or
data change that moves these counts is caught.

---

## Rebuilding the data

The cleaned data in `data/clean/` and the area files in `data/aoi/` are
committed, so this is only needed to refresh them. Put the raw downloads in
`data/raw/` (not committed; about 890 MB) and run:

```bash
.venv/bin/python scripts/make_aoi.py
.venv/bin/python scripts/prepare_data.py
```

`prepare_data.py` finds each raw file by the start of its name, and prints a
summary of each output when it finishes:

| Raw file (name starts with) | Size used here | Output |
|---|---|---|
| `SSSI_Impact_Risk_Zones_England` | 17 MB (JSON) | `irz.gpkg` |
| `Sites_of_Special_Scientific_Interest_England` | 5 MB | `sssi.gpkg` |
| `SSSI_Units_England` | 504 MB (GeoJSON) | `sssi_units.gpkg` |
| `Special_Areas_of_Conservation_England` | 3 MB | `sac.gpkg` |
| `Special_Protection_Areas_England` | 2 MB | `spa.gpkg` |
| `Ramsar_England` | 1 MB | `ramsar.gpkg` |
| `Priority_Habitat_Inventory_England` | 96 MB | `priority_habitat.gpkg` |
| `Ancient_Woodland_Inventory_Revised_England` | 10 MB | `ancient_woodland.gpkg` (revised inventory) |
| `Ancient_Woodland_England` | 245 MB (GeoPackage) | `ancient_woodland.gpkg` (original inventory, outside revised coverage) |
| `EDM_` | 6 MB (zip, sheet "All WaSC") | `storm_overflows.gpkg` |

The Natural England layers are published on data.gov.uk and Natural
England's Open Data Geoportal; the storm overflow return is published by the
Environment Agency.

---

## Uploading coordinates

**CSV**: a header row, then one site per row, with one of:

| Columns | Meaning |
|---|---|
| `easting`, `northing` | British National Grid, in metres |
| `lat`, `lon` | latitude and longitude |
| `grid_ref` | an OS grid reference such as `TL2709096270` (also accepted as `gridref`, `grid_reference`, `ngr` or `os_grid_ref`) |

Optional columns: `name`, and `route`: rows with the same route value are
joined, in order, into a line (a route needs at least two rows).

```
name,route,easting,northing
Ely sewer,R1,553000,280000
Ely sewer,R1,554000,280500
Storm overflow,,552400,279800
```

**GeoJSON**: points, lines or polygons, in lat/long or British National Grid
(detected from the values).

The coordinate system is taken from the column names or values and checked
against plausible ranges (eastings and northings must be British National
Grid values; lat/long must be in Great Britain). Any bad row rejects the
whole upload, with the reason for each row. Limits: 2,000 sites and 5 MB.

---

## API

| Endpoint | What it does |
|---|---|
| `GET /` | The single-page app, with the settings it needs from `rules.yaml` and which AI features are available |
| `POST /intake` | `{history, sites_placed}` → the next chat turn: a question (with the answers known so far, how the user wants to give the location, and any postcodes or districts to look up), or a summary to confirm. 503 if the AI is unavailable; 429 past the session cap |
| `POST /snapshot` | `{intake, sites, crs}`: the confirmed answers and the sites as a GeoJSON FeatureCollection (`EPSG:4326` by default, or `EPSG:27700`, per request or per feature) → each site's level, may-escalate-to, parts (each with level and reasons), the protected features it touches with outlines, context and warnings, plus counts per level |
| `POST /report` | The `/snapshot` request → a report checked against the results (`source: "ai"`), or the facts-only report (`source: "facts"`) if the AI isn't available |
| `POST /ask` | The `/snapshot` request plus `history` ending with the question → `{answer, refs}`: the assistant's answer and the parts it cites. Claude only |
| `GET /geocode?postcode=` | A UK postcode or district → a point to confirm (postcodes.io), whether it's in the screened area, and an accuracy note. Results are cached |

Interactive API docs are at http://localhost:8000/docs when the app is
running.

### Settings

From `.env` (see `.env.example`). Empty values count as not set.

| Setting | Default | What it does |
|---|---|---|
| `LLM_PROVIDER` | `claude` | `claude` or `gemini` |
| `LLM_MODEL` | `claude-sonnet-5-5` (or `gemini-flash-latest`) | Override the model |
| `ANTHROPIC_API_KEY` | – | Key for Claude |
| `GEMINI_API_KEY` | – | Key for Gemini (with `LLM_PROVIDER=gemini`) |
| `INTAKE_MESSAGE_CAP` | `20` | AI messages per browser session (chat, report and assistant together; only successful calls count) |

---

## Project layout

```
app/
  main.py         FastAPI app: endpoints, page config, session cap
  rules.yaml      levels, rules, work types and triggers, geometry settings
  scoring.py      loads data layers and rules; scores one site (the rules engine)
  sites.py        points, routes and areas: buffers, sections, pieces, merging, outside-area warnings
  screening.py    reads the submitted sites and screens a whole project
  intake.py       the intake conversation: schema from rules.yaml, prompt, checks
  llm.py          provider wrapper (Claude, Gemini), settings, availability, tool loop
  explain.py      plain-English facts sheet for the AI
  report.py       AI report, its checks and the facts-only fallback
  assistant.py    results assistant and its tools
  static/         index.html, app.js, style.css (no build step)
data/
  aoi/            the screened area (British National Grid and WGS84)
  clean/          prepared layers used by the app (committed)
  raw/            raw downloads (not committed)
docs/
  technical_notes.md    this file
  irz_code_mapping.md   how the 13-digit risk-zone code was decoded
  data_dictionary.md    storm overflow column names
scripts/
  make_aoi.py           builds data/aoi/
  prepare_data.py       builds data/clean/ from data/raw/
tests/            121 tests
DECISIONS.md      every design decision, dated, with the reasoning
```

About 5,900 lines in all: roughly 2,300 of Python in `app/`, 2,000 of HTML,
CSS and JavaScript, 1,200 of tests and 350 of scripts.

Front-end libraries (from CDNs): Leaflet 1.9.4, Leaflet-Geoman 2.17.0 and
proj4js 2.11.0. Map tiles: OpenStreetMap. Back end: FastAPI, GeoPandas,
Shapely, pyproj, and the Anthropic and Google GenAI SDKs (full list in
`requirements.txt`).

---

## Tests

```bash
.venv/bin/python -m pytest
```

121 tests in five files. They don't call the AI or postcodes.io (both are
faked), so no key or internet connection is needed. Tests that need
`data/clean/` are skipped if it hasn't been built.

| File | Covers |
|---|---|
| `tests/test_scoring.py` | Every rule, each work type's triggers (including CHP, sludge/AD and abstraction), escalation, the survey step, intake validation, and checks against the real data |
| `tests/test_api.py` | The endpoints: the 461-overflow regression counts, points on the area's edge, polygons and routes partly outside, route sections and merging, mixed coordinate systems, request limits, postcode lookups, the session cap (only successes count), the page config and which AI features it reports |
| `tests/test_intake.py` | The intake conversation: schema, recorded answers, postcodes only if typed, no level mentions, the location step |
| `tests/test_llm.py` | The provider wrapper with a fake Anthropic client: tool calls, retries, key redaction, empty settings counting as not set, availability for each provider and key, and that the app starts with `.env.example`'s empty values |
| `tests/test_ai_features.py` | The facts sheet, report checks, retries and fallbacks, the assistant's tools and level checks, the tool loop, and the report and ask endpoints with and without AI |

---

## Repository notes

- `data/clean/` is committed (about 54 MB in all; the largest file is
  `priority_habitat.gpkg` at 21.5 MB). No file in the repository or its
  history is over 50 MB.
- The history was rewritten before the first push to remove an earlier
  77.9 MB version of `priority_habitat.gpkg`
  (`git filter-repo --strip-blobs-bigger-than 50M`). The current files were
  unchanged.
- No API key has ever been committed. `.env` is ignored, and the whole
  history was checked before publishing.
