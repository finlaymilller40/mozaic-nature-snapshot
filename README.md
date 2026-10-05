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

1. **Describe the project**, either by chatting with the assistant or by
   filling in a short form. The assistant talks through the project in your
   terms, works out what it can from your description and checks it with
   you. Either way, you then choose how to show where the work is:
   - **upload or paste coordinates** (most accurate);
   - give a **postcode**: a pin appears on a map to check and drag;
   - **draw it** in a map pop-up. The shape follows the type of work: a
     route for pipes, a point for outfalls, an area for works.

   If the chat isn't available, the page switches to the form.
2. **See the results** on a full-screen map with a summary alongside:
   - the work is drawn in parts coloured by level: a pipe's stretches from
     A to B, or the parts of a site that overlap different protected areas;
   - the protected areas and Natural England risk zones it touches are
     shown dimly, each with a box explaining what it is and why it matters;
   - the summary lists every part with its level and reason, says what
     each level means, and explains why A1 parts only need a desk check;
   - anything outside the screened area is grey and dashed, with a warning.
3. **Let the AI explain it.** Two tabs next to the map:
   - **Report:** Claude writes a short report from the results (what was
     found, where, why it matters, next steps), checked against the
     screening so it can't invent sites or levels. Downloadable.
   - **Ask:** a results assistant that answers questions like "Which stretch
     is the problem?", "What if we didn't need to pump out groundwater?" or
     "How far is the nearest SSSI?" by calling the screening code as tools.
     Parts it mentions are links that highlight them on the map.

| Level | What to do |
|---|---|
| A1 | Nothing found. A desk check to confirm is enough. |
| A2 | An ecologist should review it from records. |
| A3 | Specialist assessment needed, likely with Natural England input. |
| A4 | Specialist assessment plus a site survey. |

The rules (levels, buffers, which risk-zone values trigger for each work
type, the shape to draw for each) are in [`app/rules.yaml`](app/rules.yaml).
The reasoning behind each choice is in [`DECISIONS.md`](DECISIONS.md).

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
| `POST /intake` | `{history, sites_placed}` → the next chat turn: a question (with any answers so far, how the user wants to give the location, and postcodes to look up), or a summary to confirm |
| `POST /snapshot` | intake + sites as GeoJSON (`crs` `EPSG:4326` or `EPSG:27700`) → each site's level, its parts (each with level and reasons), the protected features it touches with outlines, and warnings, plus counts per level |
| `POST /report` | the `/snapshot` request → an AI-written report, checked against the results |
| `POST /ask` | the `/snapshot` request + `history` → the results assistant's answer and the parts it cites |
| `GET /geocode?postcode=` | a UK postcode or district → a point to confirm (postcodes.io) |

## Data

All public data, clipped to the screened area (British National Grid
488000–585000 E, 225000–325000 N):

- SSSIs, SSSI units, SACs, SPAs, Ramsar sites, SSSI Impact Risk Zones,
  Priority Habitat Inventory and the Ancient Woodland Inventory: © Natural
  England copyright. Contains Ordnance Survey data © Crown copyright and
  database right 2026.
- Storm overflows (used as a test set): EDM 2025 Storm Overflow Annual
  Return, © Environment Agency copyright and/or database right 2026.
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
- Per-site intake answers, and more work types (e.g. reservoirs).
