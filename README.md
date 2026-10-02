# Nature Exposure Snapshot

A quick first check of whether planned water company work could affect
protected nature, for sites in and around Cambridgeshire and Peterborough.

You describe the work and where it is. The tool checks each site against
public data on protected sites, ancient woodland, priority habitat and
Natural England's SSSI Impact Risk Zones, and gives it an assurance level
from A1 (nothing found) to A4 (protected site involved and a site visit
needed), with the reasons in plain English.

It's indicative screening from public data, not a regulatory decision.

## How it works

1. **Describe the work** in the chat, or fill in a five-question form. The
   AI (Claude Sonnet 5.5) asks for the type of work and four yes/no
   questions. It never suggests a level.
2. **Say where it is**, in one of three ways:
   - give a **postcode** in the chat: a pin appears to check, drag and
     confirm (postcode-level accuracy);
   - **draw** points, lines (pipelines) or outlines on the map;
   - **upload or paste coordinates** (CSV or GeoJSON). This gives the most
     accurate results.

   For storm overflow work, you can also pick from the 461 Anglian Water
   storm overflows on the map, or use them all.
3. **Confirm**, and each site is screened:
   - points are checked as a 50 m circle;
   - polygons are checked as drawn;
   - pipelines are checked as a corridor 15 m either side, in 50 m
     sections. Each stretch is coloured by its level, and the route's level
     is its highest section;
   - anything outside the screened area is shown grey and dashed, with a
     warning saying how much wasn't checked.

The rules (levels, buffers, which risk-zone values trigger for each work
type) are in [`app/rules.yaml`](app/rules.yaml). The reasoning behind each
choice is in [`DECISIONS.md`](DECISIONS.md).

## Running it

Needs Python 3.11.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env` file in the project root (it's ignored by Git):

```
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=your-key-here
```

Without a key the chat isn't available, and the page offers the
five-question form instead. `LLM_PROVIDER=gemini` with `GEMINI_API_KEY`
also works. `INTAKE_MESSAGE_CAP` (default 20) limits chat messages per
browser session.

Start the app and open http://localhost:8000:

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

### Rebuilding the data

The cleaned data in `data/clean/` is committed. To rebuild it, put the raw
downloads in `data/raw/` (see [`DECISIONS.md`](DECISIONS.md) for the
sources) and run:

```bash
.venv/bin/python scripts/make_aoi.py
.venv/bin/python scripts/prepare_data.py
```

### Tests

```bash
.venv/bin/python -m pytest
```

The tests don't call the AI or postcodes.io; both are faked.

## Uploading coordinates

**CSV**: a header row, then one site per row, with one of:

| Columns | Meaning |
|---|---|
| `easting`, `northing` | British National Grid, in metres |
| `lat`, `lon` | latitude and longitude |
| `grid_ref` | an OS grid reference such as `TL2709096270` |

Optional columns: `name`, and `route`: rows with the same route value are
joined, in order, into a line.

```
name,route,easting,northing
Ely sewer,R1,553000,280000
Ely sewer,R1,554000,280500
Pumping station,,552400,279800
```

**GeoJSON**: points, lines or polygons, in lat/long or British National
Grid.

Up to 2,000 sites and 5 MB per upload. A file with any bad row is
rejected, with the reason for each row.

## API

| Endpoint | What it does |
|---|---|
| `POST /intake` | `{history, sites_placed}` → the next chat turn: a question, or a summary to confirm |
| `POST /snapshot` | intake + sites as GeoJSON (`crs` `EPSG:4326` or `EPSG:27700`) → each site's level, reasons, sections and warnings, plus counts per level |
| `GET /geocode?postcode=` | a UK postcode or district → a point to confirm (postcodes.io) |
| `GET /layers/{protected,irz}` | background map layers |
| `GET /sites/examples` | the 461 Anglian Water storm overflows |

## Data

All public data, clipped to the screened area (British National Grid
488000–585000 E, 225000–325000 N):

- SSSIs, SSSI units, SACs, SPAs, Ramsar sites, SSSI Impact Risk Zones,
  Priority Habitat Inventory and the Ancient Woodland Inventory: © Natural
  England copyright. Contains Ordnance Survey data © Crown copyright and
  database right 2026.
- Storm overflows: EDM 2025 Storm Overflow Annual Return, © Environment
  Agency copyright and/or database right 2026.
- Postcode lookups: postcodes.io (Ordnance Survey and ONS data).
- Map tiles: © OpenStreetMap contributors.

## Limitations

- Results are only as good as the public data, which is updated on its own
  schedule (the risk zones every two months).
- One set of intake answers applies to every site in a screening.
- Postcode pins mark the postcode's centre until moved.
- Direction of effect (upstream or downstream) isn't assessed.
- Chat message counts are kept in memory, so they reset when the server
  restarts.

## With more time

- **Place-name search with OS Names.** Geocoding towns, villages, roads
  and rivers ("the works by the River Lark near Mildenhall") with Ordnance
  Survey's OS Names API, so the map can zoom to vague locations. It needs a
  free OS Data Hub key. Today only postcodes are looked up.
- More accurate coordinate conversion using the OSTN15 grid shift (to
  centimetres rather than a few metres).
- Vector tiles for the risk-zone layer instead of simplified GeoJSON.
- Per-site intake answers, and more work types (e.g. reservoirs).
