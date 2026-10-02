"""HTTP API for the nature exposure snapshot.

GET  /                the single-page frontend (app/static/)
POST /intake          chat history -> next intake turn
POST /snapshot        confirmed intake + sites (GeoJSON points, lines, polygons) -> levels
GET  /geocode         a UK postcode -> a point to confirm (postcodes.io)
GET  /layers/{name}   background map layers: protected sites, risk zones
GET  /sites/examples  the example portfolio (Anglian Water storm overflows)

Run locally with:  .venv/bin/uvicorn app.main:app --reload
"""

import json
import os
import re
import secrets
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import httpx
import shapely
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from shapely.geometry import mapping

from app import sites as site_shapes
from app.intake import FULL_POSTCODE, POSTCODE_DISTRICT, next_turn
from app.llm import LLMError
from app.scoring import CLEAN_DIR, ROOT, load_layers, load_rules, validate_intake
from app.sites import BNG, NOT_SCREENED, WGS84, SiteError

AOI_ZIP = ROOT / "data" / "aoi" / "cambs_aoi.zip"
STATIC_DIR = Path(__file__).resolve().parent / "static"

POSTCODES_IO = "https://api.postcodes.io"
LAYER_SIMPLIFY_M = {"protected": 10, "irz": 25}

# Each browser session may send at most this many chat messages to the AI.
# Sessions are identified by a random cookie; counts are kept in memory, so
# they reset when the server restarts.
INTAKE_MESSAGE_CAP = int(os.environ.get("INTAKE_MESSAGE_CAP", "20"))
SESSION_COOKIE = "snapshot_session"
_session_counts: defaultdict[str, int] = defaultdict(int)
_session_lock = threading.Lock()

state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the rules, data layers and example sites once, not per request.
    load_rules()
    load_layers()
    state["aoi"] = gpd.read_file(f"zip://{AOI_ZIP}").to_crs(BNG).union_all()
    state["examples"] = _example_sites()
    state["layers"] = {name: _layer_json(name) for name in LAYER_SIMPLIFY_M}
    state["index_html"] = _index_html()
    yield
    # State is kept on shutdown: startup reloads it, and clearing it would
    # break other app instances running in the same process (as in tests).


app = FastAPI(title="Nature Exposure Snapshot", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ------------------------------------------------------------------ models


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class IntakeRequest(BaseModel):
    history: list[ChatMessage]
    sites_placed: int = Field(0, ge=0)  # sites the user has on the map so far


class SnapshotRequest(BaseModel):
    intake: dict[str, Any]
    sites: dict[str, Any]  # GeoJSON FeatureCollection: points, lines and/or polygons
    # Coordinates are lat/long by default. A feature may carry its own "crs"
    # member, e.g. "EPSG:27700" for uploaded British National Grid sites.
    crs: Literal["EPSG:4326", "EPSG:27700"] = WGS84


# --------------------------------------------------------------- endpoints


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return state["index_html"]


@app.post("/intake")
def intake(body: IntakeRequest, request: Request, response: Response) -> dict:
    history = body.history
    if not history or history[0].role != "user":
        raise HTTPException(422, "history must start with the user's description of the work")

    session = request.cookies.get(SESSION_COOKIE) or secrets.token_urlsafe(16)
    response.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="lax")
    # Error responses don't include cookies set on `response`, so pass it on.
    cookie = {"set-cookie": response.headers["set-cookie"]}
    user_messages = sum(m.role == "user" for m in history)
    with _session_lock:
        over_cap = _session_counts[session] >= INTAKE_MESSAGE_CAP or user_messages > INTAKE_MESSAGE_CAP
        if not over_cap:
            _session_counts[session] += 1
    if over_cap:
        raise HTTPException(
            429,
            f"This session has reached its limit of {INTAKE_MESSAGE_CAP} chat messages. "
            "Please use the form to finish.",
            headers=cookie,
        )
    try:
        return next_turn([m.model_dump() for m in history], sites_placed=body.sites_placed)
    except LLMError as exc:
        raise HTTPException(503, f"Intake assistant unavailable: {exc}", headers=cookie) from None


@app.post("/snapshot")
def snapshot(request: SnapshotRequest) -> dict:
    try:
        validate_intake(request.intake)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    cfg = site_shapes.settings()
    features = request.sites.get("features")
    if request.sites.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise HTTPException(422, "sites must be a GeoJSON FeatureCollection")
    if not features:
        raise HTTPException(422, "sites has no features")
    if len(features) > cfg["max_sites"]:
        raise HTTPException(422, f"Too many sites: {len(features)}. The limit is {cfg['max_sites']} per screening.")

    plans = []
    for i, feature in enumerate(features):
        feature = feature or {}
        try:
            geom = site_shapes.read_geometry(feature.get("geometry"), feature.get("crs") or request.crs)
        except SiteError as exc:
            raise HTTPException(422, f"Feature {i}: {exc}") from None
        plans.append(site_shapes.plan_site(geom, state["aoi"]))

    sections = sum(len(p.sections) for p in plans)
    if sections > cfg["max_sections"]:
        km = sections * cfg["section_length_m"] / 1000
        limit = cfg["max_sections"] * cfg["section_length_m"] / 1000
        raise HTTPException(422, f"Routes total about {km:.0f} km; the limit is about {limit:.0f} km per screening.")

    counts = {level: 0 for level in load_rules()["levels"]} | {NOT_SCREENED: 0}
    out = []
    for i, (feature, plan) in enumerate(zip(features, plans)):
        result = site_shapes.screen(plan, request.intake)
        counts[result["level"] or NOT_SCREENED] += 1
        properties = {
            "id": _feature_id(feature, i),
            **{k: v for k, v in (feature.get("properties") or {}).items() if k not in result},
            "kind": plan.kind,
            **result,
        }
        out.append({"type": "Feature", "geometry": site_shapes.to_lat_long(plan.geometry), "properties": properties})

    return {
        "type": "FeatureCollection",
        "features": out,
        "summary": {"sites": len(features), "counts": counts, "sections_screened": sections},
    }


@app.get("/geocode")
def geocode(postcode: str) -> dict:
    """Look up a UK postcode (or postcode district, for zooming) with postcodes.io.

    Returns a point for the user to confirm or move. The AI never produces
    coordinates; only this lookup does.
    """
    query = re.sub(r"\s+", " ", postcode.strip().upper())
    if FULL_POSTCODE.match(query):
        kind, path = "postcode", f"/postcodes/{query.replace(' ', '')}"
    elif POSTCODE_DISTRICT.match(query):
        kind, path = "district", f"/outcodes/{query}"
    else:
        raise HTTPException(422, f"{postcode!r} isn't a UK postcode or postcode district")

    result = _postcodes_io(path)
    if result is None:
        raise HTTPException(404, f"Postcode {query} wasn't found")
    lon, lat = result["longitude"], result["latitude"]
    if lon is None or lat is None:
        raise HTTPException(404, f"Postcode {query} has no location")
    easting, northing = result.get("eastings"), result.get("northings")
    in_area = easting is not None and state["aoi"].contains(shapely.Point(easting, northing))
    return {
        "query": query,
        "kind": kind,
        "postcode": result.get("postcode") or result.get("outcode") or query,
        "latitude": lat,
        "longitude": lon,
        "in_area": bool(in_area),
        "accuracy": (
            "Postcode-level accuracy: the pin marks the centre of the postcode, not your exact site. "
            "Drag it to the right spot."
            if kind == "postcode" else
            "Postcode district: use this to find the area, then place your site on the map."
        ),
    }


@app.get("/layers/{name}")
def layer(name: str) -> Response:
    if name not in state["layers"]:
        raise HTTPException(404, f"No layer called {name!r}. Layers: {sorted(state['layers'])}")
    return Response(state["layers"][name], media_type="application/json")


@app.get("/sites/examples")
def sites_examples() -> dict:
    return state["examples"]


# ----------------------------------------------------------------- helpers


def _feature_id(feature: dict, index: int) -> str:
    fid = feature.get("id", (feature.get("properties") or {}).get("id"))
    return str(fid) if fid is not None else str(index)


def _index_html() -> str:
    """The page, with the settings the browser needs from rules.yaml filled in."""
    rules = load_rules()
    config = {
        "work_types": {
            key: {"label": spec["label"], "triggers": spec["triggers"]}
            for key, spec in rules["work_types"].items()
        },
        "intake_triggers": {key: spec["triggers"] for key, spec in rules.get("intake_triggers", {}).items()},
        "all_consultations_values": next(r for r in rules["rules"] if r["check"] == "irz_all_consultations")["values"],
        "site_geometry": rules["site_geometry"],
        # The screened area's outline, so the map can show it.
        "aoi": site_shapes.to_lat_long(state["aoi"].segmentize(1_000)),
    }
    # Escape "</" so the JSON can't close its <script> tag.
    payload = json.dumps(config).replace("</", "<\\/")
    return (STATIC_DIR / "index.html").read_text().replace("{{CONFIG}}", payload)


@lru_cache(maxsize=1024)
def _postcodes_io(path: str) -> dict | None:
    """GET a postcodes.io result; None if not found. Cached, as postcodes rarely move."""
    try:
        reply = httpx.get(POSTCODES_IO + path, timeout=5)
    except httpx.HTTPError:
        raise HTTPException(502, "The postcode service (postcodes.io) couldn't be reached. Try placing the site on the map.") from None
    if reply.status_code == 404:
        return None
    if reply.status_code != 200:
        raise HTTPException(502, f"The postcode service returned an error ({reply.status_code}).")
    return reply.json().get("result")


def _layer_json(name: str) -> bytes:
    """A background layer as compact lat/long GeoJSON, simplified for display only."""
    layers = load_layers()
    if name == "protected":
        parts = [
            layers["sssi"].assign(designation="SSSI", site=layers["sssi"]["name"]),
            layers["european"],
        ]
        gdf = gpd.GeoDataFrame(
            [row for part in parts for row in part[["designation", "site", "geometry"]].to_dict("records")],
            geometry="geometry", crs=BNG,
        )
        columns = ["designation", "site"]
    else:
        gdf = layers["irz"][["id", "irzcode", "geometry"]]
        columns = ["irzcode"]

    simplified = gdf.geometry.simplify(LAYER_SIMPLIFY_M[name], preserve_topology=True).to_crs(WGS84)
    simplified = shapely.set_precision(simplified.values, 1e-5)
    features = [
        {"type": "Feature", "geometry": mapping(geom), "properties": {c: row[c] for c in columns}}
        for geom, (_, row) in zip(simplified, gdf.iterrows())
        if not geom.is_empty
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")).encode()


def _example_sites() -> dict:
    overflows = gpd.read_file(CLEAN_DIR / "storm_overflows.gpkg").sort_values("unique_id").to_crs(WGS84)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": row.unique_id,
                "geometry": mapping(row.geometry),
                "properties": {"id": row.unique_id, "name": row.site_name},
            }
            for row in overflows.itertuples()
        ],
    }
