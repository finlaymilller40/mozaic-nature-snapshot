"""HTTP API for the nature exposure snapshot.

GET  /                the single-page frontend (app/static/)
POST /intake          chat history -> next intake turn
POST /snapshot        confirmed intake + sites (GeoJSON points, lines, polygons) -> levels
POST /report          the same request -> an AI-written report, checked against the results
POST /ask             the same request + a question -> the results assistant's answer
GET  /geocode         a UK postcode -> a point to confirm (postcodes.io)

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

from app import assistant, report
from app.intake import FULL_POSTCODE, POSTCODE_DISTRICT, next_turn
from app.llm import LLMError
from app.scoring import ROOT, load_layers, load_rules, validate_intake
from app.screening import ScreeningError, read_project, screen_project
from app.sites import BNG, WGS84, to_lat_long

AOI_ZIP = ROOT / "data" / "aoi" / "cambs_aoi.zip"
STATIC_DIR = Path(__file__).resolve().parent / "static"

POSTCODES_IO = "https://api.postcodes.io"

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
    # Load the rules and data layers once, not per request.
    load_rules()
    load_layers()
    state["aoi"] = gpd.read_file(f"zip://{AOI_ZIP}").to_crs(BNG).union_all()
    yield
    # State is kept on shutdown: startup reloads it, and clearing it would
    # break other app instances running in the same process (as in tests).


app = FastAPI(title="Nature Exposure Snapshot", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)  # results include map outlines
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
    # Built per request (it's cheap), so edits to the page and the file
    # versions in it show up without restarting the server.
    return _index_html()


@app.post("/intake")
def intake(body: IntakeRequest, request: Request, response: Response) -> dict:
    history = body.history
    if not history or history[0].role != "user":
        raise HTTPException(422, "history must start with the user's description of the work")

    cookie = _use_message(request, response, sum(m.role == "user" for m in history))
    try:
        return next_turn([m.model_dump() for m in history], sites_placed=body.sites_placed)
    except LLMError as exc:
        raise HTTPException(503, f"Intake assistant unavailable: {exc}", headers=cookie) from None


def _use_message(request: Request, response: Response, user_messages: int = 0) -> dict:
    """Count one AI request against this browser session's cap.

    Returns the session cookie header, for error responses (which don't
    include cookies set on `response`). Raises 429 past the cap.
    """
    session = request.cookies.get(SESSION_COOKIE) or secrets.token_urlsafe(16)
    response.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="lax")
    cookie = {"set-cookie": response.headers["set-cookie"]}
    with _session_lock:
        over_cap = _session_counts[session] >= INTAKE_MESSAGE_CAP or user_messages > INTAKE_MESSAGE_CAP
        if not over_cap:
            _session_counts[session] += 1
    if over_cap:
        raise HTTPException(
            429,
            f"This session has reached its limit of {INTAKE_MESSAGE_CAP} AI messages. "
            "The screening results still work; the AI features don't.",
            headers=cookie,
        )
    return cookie


@app.post("/snapshot")
def snapshot(request: SnapshotRequest) -> dict:
    try:
        validate_intake(request.intake)
        return screen_project(read_project(request.sites, request.crs, state["aoi"]), request.intake)
    except (ValueError, ScreeningError) as exc:
        raise HTTPException(422, str(exc)) from None


@app.post("/report")
def write_report(body: SnapshotRequest, request: Request, response: Response) -> dict:
    try:
        validate_intake(body.intake)
        result = screen_project(read_project(body.sites, body.crs, state["aoi"]), body.intake)
    except (ValueError, ScreeningError) as exc:
        raise HTTPException(422, str(exc)) from None
    cookie = _use_message(request, response)
    try:
        return report.generate_report(result, body.intake)
    except LLMError as exc:
        raise HTTPException(503, f"The report writer is unavailable: {exc}", headers=cookie) from None


class AskRequest(SnapshotRequest):
    history: list[ChatMessage]  # earlier questions and answers, ending with the new question


@app.post("/ask")
def ask(body: AskRequest, request: Request, response: Response) -> dict:
    if not body.history or body.history[-1].role != "user":
        raise HTTPException(422, "history must end with the user's question")
    try:
        validate_intake(body.intake)
        project = read_project(body.sites, body.crs, state["aoi"])
    except (ValueError, ScreeningError) as exc:
        raise HTTPException(422, str(exc)) from None
    cookie = _use_message(request, response)
    try:
        return assistant.answer(project, body.intake, [m.model_dump() for m in body.history])
    except LLMError as exc:
        raise HTTPException(503, f"The assistant is unavailable: {exc}", headers=cookie) from None


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


# ----------------------------------------------------------------- helpers


def _index_html() -> str:
    """The page, with the settings the browser needs from rules.yaml filled in."""
    rules = load_rules()
    config = {
        "work_types": {
            key: {"label": spec["label"], "shape": spec["shape"]}
            for key, spec in rules["work_types"].items()
        },
        "levels": rules["levels"],
        "level_meanings": rules["level_meanings"],
        "site_geometry": rules["site_geometry"],
        "ancient_woodland_buffer_m": next(r for r in rules["rules"] if r["check"] == "near_ancient_woodland")["buffer_m"],
        # The screened area's outline, so the map can show it.
        "aoi": to_lat_long(state["aoi"].segmentize(1_000)),
    }
    # Escape "</" so the JSON can't close its <script> tag.
    payload = json.dumps(config).replace("</", "<\\/")
    html = (STATIC_DIR / "index.html").read_text().replace("{{CONFIG}}", payload)
    # Version the static files so browsers fetch new copies after a change.
    for name in ("app.js", "style.css"):
        version = int((STATIC_DIR / name).stat().st_mtime)
        html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
    return html


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
