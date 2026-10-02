"""Tests for the API in app/main.py. Needs data/clean/; the LLM and postcodes.io are faked."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import main
from app.scoring import CLEAN_DIR, load_rules

pytestmark = pytest.mark.skipif(not (CLEAN_DIR / "irz.gpkg").exists(), reason="data/clean/ not built")

INTAKE = {
    "work_type": "storm_overflow", "beyond_site": "no", "abstraction": "no",
    "dewatering": "no", "physical_survey_required": "no",
}
PIPELINE = INTAKE | {"work_type": "new_pipeline_sewer"}
HISTORY = [{"role": "user", "content": "Upgrading storm overflows"}]


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:  # runs the startup that loads the data once
        yield c


@pytest.fixture(scope="module")
def examples(client):
    return client.get("/sites/examples").json()


def feature_collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def feature(geometry_type, coordinates, **extra):
    return {"type": "Feature", "geometry": {"type": geometry_type, "coordinates": coordinates}, "properties": {}, **extra}


def fake_turn(monkeypatch, turn=None):
    seen = {}

    def next_turn(history, sites_placed=0):
        seen.update(history=history, sites_placed=sites_placed)
        return turn or {"type": "question", "text": "Next?", "answers": {}, "postcodes": [], "districts": []}

    monkeypatch.setattr(main, "next_turn", next_turn)
    return seen


# ---------------------------------------------------------- /sites/examples


def test_examples_are_461_lat_long_points(examples):
    features = examples["features"]
    assert examples["type"] == "FeatureCollection" and len(features) == 461
    first = features[0]
    assert set(first["properties"]) == {"id", "name"}
    lon, lat = first["geometry"]["coordinates"]
    assert -1 < lon < 1 and 51.8 < lat < 52.9


# ----------------------------------------------------------------- /intake


def test_intake_returns_next_turn_and_passes_site_count(client, monkeypatch):
    seen = fake_turn(monkeypatch)
    response = client.post("/intake", json={"history": HISTORY, "sites_placed": 2})
    assert response.status_code == 200 and response.json()["text"] == "Next?"
    assert seen == {"history": HISTORY, "sites_placed": 2}


def test_intake_reports_llm_outage_as_503(client, monkeypatch):
    def failing(history, sites_placed=0):
        raise main.LLMError("claude request failed (APIConnectionError): connection error")

    monkeypatch.setattr(main, "next_turn", failing)
    response = client.post("/intake", json={"history": HISTORY})
    assert response.status_code == 503
    assert main.SESSION_COOKIE in response.cookies  # errors still keep the session


def test_intake_rejects_bad_history(client):
    assert client.post("/intake", json={"history": []}).status_code == 422
    assert client.post("/intake", json={"history": [{"role": "assistant", "content": "Hi"}]}).status_code == 422
    assert client.post("/intake", json={"history": [{"role": "robot", "content": "Hi"}]}).status_code == 422
    assert client.post("/intake", json={"history": HISTORY, "sites_placed": -1}).status_code == 422


def test_intake_message_cap_per_session(client, monkeypatch):
    monkeypatch.setattr(main, "INTAKE_MESSAGE_CAP", 2)
    monkeypatch.setattr(main, "_session_counts", main.defaultdict(int))
    fake_turn(monkeypatch)

    with TestClient(main.app) as browser:  # its own cookie jar = one session
        assert browser.post("/intake", json={"history": HISTORY}).status_code == 200
        assert main.SESSION_COOKIE in browser.cookies
        assert browser.post("/intake", json={"history": HISTORY}).status_code == 200
        capped = browser.post("/intake", json={"history": HISTORY})
        assert capped.status_code == 429 and "limit of 2" in capped.json()["detail"]

    with TestClient(main.app) as other_browser:  # a new session starts fresh
        assert other_browser.post("/intake", json={"history": HISTORY}).status_code == 200


def test_intake_rejects_history_longer_than_cap(client, monkeypatch):
    monkeypatch.setattr(main, "INTAKE_MESSAGE_CAP", 2)
    monkeypatch.setattr(main, "_session_counts", main.defaultdict(int))
    fake_turn(monkeypatch)
    long_history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}] * 3
    with TestClient(main.app) as browser:
        assert browser.post("/intake", json={"history": long_history[:-1]}).status_code == 429


# ------------------------------------------------------ /snapshot: points


def test_snapshot_of_all_examples_matches_trial(client, examples):
    response = client.post("/snapshot", json={"intake": INTAKE, "sites": examples})
    assert response.status_code == 200
    body = response.json()
    # Same counts as scripts/trial_storm_overflows.py with beyond_site = no.
    assert body["summary"]["counts"] == {"A1": 126, "A2": 307, "A3": 28, "A4": 0, "not_screened": 0}
    site = body["features"][0]
    assert site["properties"]["kind"] == "point" and site["properties"]["buffer_m"] == 50
    assert {"id", "name", "level", "may_escalate_to", "reasons", "context", "outside"} <= set(site["properties"])


def test_point_on_the_edge_is_screened_with_a_warning(client, examples):
    # Great Easton STW sits on the AOI's southern edge: the inside half is
    # screened and the outside half is reported as not checked.
    edge = next(f for f in examples["features"] if f["id"] == "AWS00496")
    body = client.post("/snapshot", json={"intake": INTAKE, "sites": feature_collection(edge)}).json()
    props = body["features"][0]["properties"]
    assert props["level"] == "A1"
    assert "outside the screened area and has not been checked" in props["outside"]["warning"]
    assert props["outside"]["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_snapshot_point_is_buffered_and_scored(client, examples):
    broadholme = next(f for f in examples["features"] if f["id"] == "AWS00154")
    body = client.post("/snapshot", json={
        "intake": INTAKE | {"physical_survey_required": "yes"},
        "sites": feature_collection(broadholme),
    }).json()
    props = body["features"][0]["properties"]
    assert props["level"] == "A4"  # inside SPA/Ramsar/SSSI, survey turns A3 into A4
    assert {r["rule"] for r in props["reasons"]} >= {1, 2, 10}
    assert props["outside"] is None


def test_snapshot_marks_sites_outside_data_area(client):
    london = feature("Point", [-0.1276, 51.5072])
    body = client.post("/snapshot", json={"intake": INTAKE, "sites": feature_collection(london)}).json()
    props = body["features"][0]["properties"]
    assert props["level"] is None and props["status"] == "not_screened"
    assert body["summary"]["counts"]["not_screened"] == 1


# ---------------------------------------------------- /snapshot: polygons


def test_snapshot_accepts_polygons(client, examples):
    lon, lat = examples["features"][0]["geometry"]["coordinates"]
    d = 0.001
    square = [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]]
    body = client.post("/snapshot", json={"intake": INTAKE, "sites": feature_collection(feature("Polygon", square, id="sq"))}).json()
    props = body["features"][0]["properties"]
    assert props["id"] == "sq" and props["kind"] == "polygon" and "buffer_m" not in props
    assert props["level"] in {"A1", "A2", "A3", "A4"} and props["outside"] is None


def test_polygon_partly_outside_reports_area_not_checked(client):
    # A 2 km square straddling the AOI's southern edge (northing 225000), in BNG.
    square = [[[530000, 224000], [532000, 224000], [532000, 226000], [530000, 226000], [530000, 224000]]]
    body = client.post("/snapshot", json={
        "intake": INTAKE, "crs": "EPSG:27700", "sites": feature_collection(feature("Polygon", square)),
    }).json()
    props = body["features"][0]["properties"]
    assert props["level"] is not None
    assert props["outside"]["area_ha"] == pytest.approx(200, rel=0.01)
    assert props["outside"]["warning"] == "200.0 ha of this site is outside the screened area and has not been checked."


# ------------------------------------------------------- /snapshot: lines


def test_line_is_scored_in_merged_sections(client):
    # A 2 km pipeline in BNG that crosses the Ouse Washes.
    route = feature("LineString", [[542000, 279000], [544000, 280000]], crs="EPSG:27700")
    body = client.post("/snapshot", json={"intake": PIPELINE, "sites": feature_collection(route)}).json()
    props = body["features"][0]["properties"]
    assert props["kind"] == "line" and props["corridor_m"] == 15
    assert props["length_km"] == pytest.approx(2.236, abs=0.001)
    assert body["summary"]["sections_screened"] == 45  # 2.236 km in 50 m sections

    sections = props["sections"]
    assert sections[0]["start_km"] == 0 and sections[-1]["end_km"] == pytest.approx(2.236, abs=0.001)
    for a, b in zip(sections, sections[1:]):
        assert a["end_km"] == pytest.approx(b["start_km"])  # continuous along the route
        assert (a["level"], a["reasons"]) != (b["level"], b["reasons"])  # matching neighbours merged
    levels = load_rules()["levels"]
    assert props["level"] == max((s["level"] for s in sections), key=levels.index)
    assert all(s["geometry"]["type"] in ("LineString", "MultiLineString") for s in sections)

    ouse = next(r for r in props["reasons"] if r["rule"] == 2)
    assert ouse["value"] == "Ouse Washes SSSI" and re.fullmatch(r"[\d.]+–[\d.]+ km", ouse["where"])


def test_line_partly_outside_reports_length_not_checked(client):
    # 6 km route crossing the AOI's southern edge 3 km in.
    route = feature("LineString", [[530000, 222000], [530000, 228000]])
    body = client.post("/snapshot", json={"intake": PIPELINE, "crs": "EPSG:27700", "sites": feature_collection(route)}).json()
    props = body["features"][0]["properties"]
    assert props["outside"]["warning"] == "3.0 km of this route is outside the screened area and has not been checked."
    assert props["outside"]["length_km"] == pytest.approx(3.0, abs=0.001)
    assert props["sections"][0]["start_km"] == pytest.approx(3.0, abs=0.001)  # distances count the whole route


def test_lat_long_line_is_accepted(client):
    route = feature("LineString", [[0.10, 52.20], [0.12, 52.21]])
    body = client.post("/snapshot", json={"intake": PIPELINE, "sites": feature_collection(route)}).json()
    assert body["features"][0]["properties"]["kind"] == "line"


def test_route_length_cap(client, monkeypatch):
    cfg = dict(main.site_shapes.settings(), max_sections=10)
    monkeypatch.setattr(main.site_shapes, "settings", lambda: cfg)
    route = feature("LineString", [[500000, 250000], [501000, 250000]])  # 1 km = 20 sections
    response = client.post("/snapshot", json={"intake": PIPELINE, "crs": "EPSG:27700", "sites": feature_collection(route)})
    assert response.status_code == 422 and "the limit is about" in response.text


# ------------------------------------------- /snapshot: coordinate systems


def test_bng_and_lat_long_features_can_be_mixed(client):
    sites = feature_collection(
        feature("Point", [532790, 249740], crs="EPSG:27700"),
        feature("Point", [0.1, 52.2]),
    )
    body = client.post("/snapshot", json={"intake": INTAKE, "sites": sites}).json()
    lon, lat = body["features"][0]["geometry"]["coordinates"]
    assert -0.2 < lon < 0 and 52.1 < lat < 52.2  # returned in lat/long
    assert all(f["properties"]["level"] for f in body["features"])


@pytest.mark.parametrize("change, message", [
    ({"intake": INTAKE | {"dewatering": "unknown"}}, "rule 9"),
    ({"sites": {"type": "Feature"}}, "FeatureCollection"),
    ({"sites": feature_collection({"type": "Feature", "geometry": {"type": "GeometryCollection", "geometries": []}})}, "point, line or polygon"),
    ({"sites": feature_collection(feature("Point", [532790, 249740]))}, "for eastings/northings set crs to EPSG:27700"),
    ({"sites": feature_collection(feature("Point", [0.1, 52.2])), "crs": "EPSG:27700"}, "British National Grid"),
    ({"crs": "EPSG:3857"}, "crs"),
])
def test_snapshot_rejects_bad_requests(client, change, message):
    body = {"intake": INTAKE, "sites": feature_collection(feature("Point", [0.1, 52.2]))} | change
    response = client.post("/snapshot", json=body)
    assert response.status_code == 422
    assert message in response.text


# ---------------------------------------------------------------- /geocode


def fake_postcodes_io(monkeypatch, results):
    calls = []

    def lookup(path):
        calls.append(path)
        return results.get(path)

    monkeypatch.setattr(main, "_postcodes_io", lookup)
    return calls


def test_geocode_full_postcode(client, monkeypatch):
    calls = fake_postcodes_io(monkeypatch, {"/postcodes/CB74DA": {
        "postcode": "CB7 4DA", "longitude": 0.263, "latitude": 52.399, "eastings": 554000, "northings": 280000,
    }})
    body = client.get("/geocode", params={"postcode": " cb7  4da "}).json()
    assert calls == ["/postcodes/CB74DA"]
    assert body["kind"] == "postcode" and body["postcode"] == "CB7 4DA"
    assert (body["latitude"], body["longitude"], body["in_area"]) == (52.399, 0.263, True)
    assert body["accuracy"].startswith("Postcode-level accuracy")


def test_geocode_district_is_for_zooming(client, monkeypatch):
    fake_postcodes_io(monkeypatch, {"/outcodes/CB7": {
        "outcode": "CB7", "longitude": 0.3, "latitude": 52.4, "eastings": 556000, "northings": 281000,
    }})
    body = client.get("/geocode", params={"postcode": "CB7"}).json()
    assert body["kind"] == "district" and body["postcode"] == "CB7"


def test_geocode_outside_area_and_unknown(client, monkeypatch):
    fake_postcodes_io(monkeypatch, {"/postcodes/SW1A1AA": {
        "postcode": "SW1A 1AA", "longitude": -0.14, "latitude": 51.5, "eastings": 529090, "northings": 179645,
    }})
    assert client.get("/geocode", params={"postcode": "SW1A 1AA"}).json()["in_area"] is False
    assert client.get("/geocode", params={"postcode": "ZZ9 9ZZ"}).status_code == 404
    assert client.get("/geocode", params={"postcode": "near Ely"}).status_code == 422


# ----------------------------------------------------------------- /layers


def test_layers_are_lat_long_geojson(client):
    protected = client.get("/layers/protected").json()
    designations = {f["properties"]["designation"] for f in protected["features"]}
    assert designations == {"SSSI", "SAC", "SPA", "Ramsar"}
    irz = client.get("/layers/irz").json()
    assert len(irz["features"]) > 8000 and re.fullmatch(r"\d{13}", irz["features"][0]["properties"]["irzcode"])
    assert client.get("/layers/nope").status_code == 404


# --------------------------------------------------------------- frontend


def test_index_page_has_config_from_rules(client):
    response = client.get("/")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    blob = re.search(r'<script id="config" type="application/json">(.*?)</script>', response.text, re.S)
    config = json.loads(blob.group(1))
    rules = load_rules()
    assert set(config["work_types"]) == set(rules["work_types"])
    assert config["site_geometry"] == rules["site_geometry"]
    assert "Indicative screening from public data, not a regulatory decision." in response.text
    assert "https://mozaic.earth/contact" in response.text
    assert "Upload coordinates for the most accurate results." in response.text


def test_static_files_are_served(client):
    for path in ("/static/app.js", "/static/style.css"):
        assert client.get(path).status_code == 200
