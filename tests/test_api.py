"""Tests for the API in app/main.py. Needs data/clean/; the LLM is faked."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.scoring import CLEAN_DIR

pytestmark = pytest.mark.skipif(not (CLEAN_DIR / "irz.gpkg").exists(), reason="data/clean/ not built")

INTAKE = {
    "work_type": "storm_overflow", "beyond_site": "no", "abstraction": "no",
    "dewatering": "no", "physical_survey_required": "no",
}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:  # runs the startup that loads the data once
        yield c


@pytest.fixture(scope="module")
def examples(client):
    return client.get("/sites/examples").json()


def feature_collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


# ---------------------------------------------------------- /sites/examples


def test_examples_are_461_lat_long_points(examples):
    features = examples["features"]
    assert examples["type"] == "FeatureCollection" and len(features) == 461
    first = features[0]
    assert set(first["properties"]) == {"id", "name"}
    lon, lat = first["geometry"]["coordinates"]
    assert -1 < lon < 1 and 51.8 < lat < 52.9


# ----------------------------------------------------------------- /intake


def test_intake_returns_next_turn(client, monkeypatch):
    seen = {}

    def fake_next_turn(history):
        seen["history"] = history
        return {"type": "question", "text": "Will any work be outside the site boundary?"}

    monkeypatch.setattr(main, "next_turn", fake_next_turn)
    history = [{"role": "user", "content": "Upgrading storm overflows"}]
    response = client.post("/intake", json=history)
    assert response.status_code == 200
    assert response.json() == {"type": "question", "text": "Will any work be outside the site boundary?"}
    assert seen["history"] == history


def test_intake_reports_llm_outage_as_503(client, monkeypatch):
    def failing(history):
        raise main.LLMError("gemini request failed (ServerError): 503 UNAVAILABLE")

    monkeypatch.setattr(main, "next_turn", failing)
    response = client.post("/intake", json=[{"role": "user", "content": "Hi"}])
    assert response.status_code == 503


def test_intake_rejects_bad_history(client):
    assert client.post("/intake", json=[]).status_code == 422
    assert client.post("/intake", json=[{"role": "assistant", "content": "Hi"}]).status_code == 422
    assert client.post("/intake", json=[{"role": "robot", "content": "Hi"}]).status_code == 422


# --------------------------------------------------------------- /snapshot


def test_snapshot_of_all_examples_matches_trial(client, examples):
    response = client.post("/snapshot", json={"intake": INTAKE, "sites": examples})
    assert response.status_code == 200
    body = response.json()
    # Same counts as scripts/trial_storm_overflows.py with beyond_site = no,
    # except Great Easton (AWS00496): it sits on the AOI's southern edge, so
    # half its 50 m buffer has no data and it isn't screened (the trial
    # scored it A1).
    assert body["summary"] == {
        "sites": 461, "counts": {"A1": 125, "A2": 307, "A3": 28, "A4": 0, "not_screened": 1},
    }
    edge = next(f for f in body["features"] if f["properties"]["id"] == "AWS00496")
    assert edge["properties"]["status"] == "not_screened"
    site = body["features"][0]
    assert site["geometry"] == examples["features"][0]["geometry"]  # returned in lat/long
    assert {"id", "name", "level", "may_escalate_to", "reasons", "context", "buffer_m"} <= set(site["properties"])


def test_snapshot_point_is_buffered_and_scored(client, examples):
    broadholme = next(f for f in examples["features"] if f["id"] == "AWS00154")
    body = client.post("/snapshot", json={
        "intake": INTAKE | {"physical_survey_required": "yes"},
        "sites": feature_collection(broadholme),
    }).json()
    props = body["features"][0]["properties"]
    assert props["buffer_m"] == 50
    assert props["level"] == "A4"  # inside SPA/Ramsar/SSSI, survey turns A3 into A4
    assert {r["rule"] for r in props["reasons"]} >= {1, 2, 10}


def test_snapshot_accepts_polygons(client, examples):
    lon, lat = examples["features"][0]["geometry"]["coordinates"]
    d = 0.001
    square = {"type": "Polygon", "coordinates": [[
        [lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d],
    ]]}
    body = client.post("/snapshot", json={
        "intake": INTAKE, "sites": feature_collection({"type": "Feature", "id": "sq", "geometry": square, "properties": {}}),
    }).json()
    props = body["features"][0]["properties"]
    assert props["id"] == "sq" and props["buffer_m"] == 0 and props["level"] in {"A1", "A2", "A3", "A4"}


def test_snapshot_marks_sites_outside_data_area(client):
    london = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-0.1276, 51.5072]}, "properties": {}}
    body = client.post("/snapshot", json={"intake": INTAKE, "sites": feature_collection(london)}).json()
    props = body["features"][0]["properties"]
    assert props["level"] is None and props["status"] == "not_screened"
    assert body["summary"]["counts"]["not_screened"] == 1


@pytest.mark.parametrize("change, message", [
    ({"intake": INTAKE | {"dewatering": "unknown"}}, "rule 9"),
    ({"sites": {"type": "Feature"}}, "FeatureCollection"),
    ({"sites": feature_collection({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 52], [0.1, 52]]}})}, "point or polygon"),
    ({"sites": feature_collection({"type": "Feature", "geometry": {"type": "Point", "coordinates": [532790, 249740]}})}, "longitude/latitude"),
])
def test_snapshot_rejects_bad_requests(client, change, message):
    point = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.1, 52.2]}}
    body = {"intake": INTAKE, "sites": feature_collection(point)} | change
    response = client.post("/snapshot", json=body)
    assert response.status_code == 422
    assert message in response.text


# --------------------------------------------------------------- frontend


def test_index_page_has_work_types_from_rules(client):
    import json
    import re

    from app.scoring import load_rules

    response = client.get("/")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    blob = re.search(r'<script id="work-types" type="application/json">(.*?)</script>', response.text, re.S)
    work_types = json.loads(blob.group(1))
    assert set(work_types) == set(load_rules()["work_types"])
    assert "Indicative screening from public data, not a regulatory decision." in response.text
    assert "https://mozaic.earth/contact" in response.text


def test_static_files_are_served(client):
    for path in ("/static/app.js", "/static/style.css"):
        assert client.get(path).status_code == 200
