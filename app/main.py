"""HTTP API for the nature exposure snapshot.

POST /intake          chat history -> next intake turn
POST /snapshot        confirmed intake + sites (lat/long GeoJSON) -> levels
GET  /sites/examples  the example portfolio (Anglian Water storm overflows)

Run locally with:  .venv/bin/uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
from typing import Any, Literal

import geopandas as gpd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from shapely.geometry import mapping, shape

from app.intake import next_turn
from app.llm import LLMError
from app.scoring import CLEAN_DIR, ROOT, check_site, load_layers, load_rules, score_site, validate_intake

BNG = "EPSG:27700"
WGS84 = "EPSG:4326"
POINT_BUFFER_M = 50
AOI_ZIP = ROOT / "data" / "aoi" / "cambs_aoi.zip"
NOT_SCREENED = "not_screened"

state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the rules, data layers and example sites once, not per request.
    load_rules()
    load_layers()
    state["aoi"] = gpd.read_file(f"zip://{AOI_ZIP}").to_crs(BNG).union_all()
    state["examples"] = _example_sites()
    yield
    state.clear()


app = FastAPI(title="Nature Exposure Snapshot", lifespan=lifespan)


# ------------------------------------------------------------------ models


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SnapshotRequest(BaseModel):
    intake: dict[str, Any]
    sites: dict[str, Any]  # GeoJSON FeatureCollection in lat/long (EPSG:4326)


# --------------------------------------------------------------- endpoints


@app.post("/intake")
def intake(history: list[ChatMessage]) -> dict:
    if not history or history[0].role != "user":
        raise HTTPException(422, "history must start with the user's description of the work")
    try:
        return next_turn([m.model_dump() for m in history])
    except LLMError as exc:
        raise HTTPException(503, f"Intake assistant unavailable: {exc}") from None


@app.post("/snapshot")
def snapshot(request: SnapshotRequest) -> dict:
    try:
        validate_intake(request.intake)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    features = _read_features(request.sites)
    lat_long = gpd.GeoSeries([shape(f["geometry"]) for f in features], crs=WGS84)
    bng = lat_long.to_crs(BNG)
    is_point = bng.geom_type.isin(["Point", "MultiPoint"])
    screened = [g.buffer(POINT_BUFFER_M) if p else g for g, p in zip(bng, is_point)]

    aoi = state["aoi"]
    levels = load_rules()["levels"]
    counts = {level: 0 for level in levels} | {NOT_SCREENED: 0}
    out = []
    for i, feature in enumerate(features):
        site = screened[i]
        properties = {
            "id": _feature_id(feature, i),
            **(feature.get("properties") or {}),
            "buffer_m": POINT_BUFFER_M if is_point.iloc[i] else 0,
        }
        if site.within(aoi):
            score = score_site(check_site(site), request.intake)
            properties |= {k: score[k] for k in ("level", "may_escalate_to", "reasons", "context")}
            counts[score["level"]] += 1
        else:
            properties |= {
                "level": None, "may_escalate_to": None, "reasons": [], "context": None,
                "status": NOT_SCREENED,
                "detail": "Site is not wholly inside the area the data covers, so it can't be screened.",
            }
            counts[NOT_SCREENED] += 1
        out.append({"type": "Feature", "geometry": feature["geometry"], "properties": properties})

    return {
        "type": "FeatureCollection",
        "features": out,
        "summary": {"sites": len(features), "counts": counts},
    }


@app.get("/sites/examples")
def sites_examples() -> dict:
    return state["examples"]


# ----------------------------------------------------------------- helpers


def _read_features(sites: dict) -> list[dict]:
    if sites.get("type") != "FeatureCollection" or not isinstance(sites.get("features"), list):
        raise HTTPException(422, "sites must be a GeoJSON FeatureCollection")
    if not sites["features"]:
        raise HTTPException(422, "sites has no features")

    features = []
    for i, feature in enumerate(sites["features"]):
        geometry = (feature or {}).get("geometry") or {}
        kind = geometry.get("type")
        if kind not in ("Point", "MultiPoint", "Polygon", "MultiPolygon"):
            raise HTTPException(422, f"Feature {i}: geometry must be a point or polygon, got {kind!r}")
        try:
            geom = shape(geometry)
        except Exception:
            raise HTTPException(422, f"Feature {i}: invalid geometry") from None
        minx, miny, maxx, maxy = geom.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            raise HTTPException(
                422, f"Feature {i}: coordinates must be longitude/latitude (EPSG:4326), not eastings/northings"
            )
        features.append(feature)
    return features


def _feature_id(feature: dict, index: int) -> str:
    fid = feature.get("id", (feature.get("properties") or {}).get("id"))
    return str(fid) if fid is not None else str(index)


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
