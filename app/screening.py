"""Screen a set of submitted sites: shared by /snapshot, /report and /ask."""

from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from app import sites as site_shapes
from app.scoring import load_rules, validate_intake
from app.sites import NOT_SCREENED, WGS84, Plan, SiteError


class ScreeningError(ValueError):
    """The request can't be screened. The message says why, in plain English."""


@dataclass
class Project:
    """Submitted sites, read and cut down to what gets screened."""
    features: list[dict]
    plans: list[Plan]
    sections: int


def read_project(sites: dict, crs: str, aoi: BaseGeometry) -> Project:
    cfg = site_shapes.settings()
    features = sites.get("features")
    if sites.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise ScreeningError("sites must be a GeoJSON FeatureCollection")
    if not features:
        raise ScreeningError("sites has no features")
    if len(features) > cfg["max_sites"]:
        raise ScreeningError(f"Too many sites: {len(features)}. The limit is {cfg['max_sites']} per screening.")

    plans = []
    for i, feature in enumerate(features):
        feature = feature or {}
        try:
            geom = site_shapes.read_geometry(feature.get("geometry"), feature.get("crs") or crs)
        except SiteError as exc:
            raise ScreeningError(f"Feature {i}: {exc}") from None
        plans.append(site_shapes.plan_site(geom, aoi))

    sections = sum(len(p.sections) for p in plans)
    if sections > cfg["max_sections"]:
        km = sections * cfg["section_length_m"] / 1000
        limit = cfg["max_sections"] * cfg["section_length_m"] / 1000
        raise ScreeningError(f"Routes total about {km:.0f} km; the limit is about {limit:.0f} km per screening.")
    return Project(features, plans, sections)


def screen_project(project: Project, intake: dict) -> dict:
    """Screen every site. Returns lat/long GeoJSON with each site's results."""
    try:
        validate_intake(intake)
    except ValueError as exc:
        raise ScreeningError(str(exc)) from None

    counts = {level: 0 for level in load_rules()["levels"]} | {NOT_SCREENED: 0}
    out = []
    for i, (feature, plan) in enumerate(zip(project.features, project.plans)):
        result = site_shapes.screen(plan, intake)
        counts[result["level"] or NOT_SCREENED] += 1
        properties = {
            "id": _feature_id(feature, i),
            **{k: v for k, v in ((feature or {}).get("properties") or {}).items() if k not in result},
            "kind": plan.kind,
            **result,
        }
        out.append({"type": "Feature", "geometry": site_shapes.to_lat_long(plan.geometry), "properties": properties})
    return {
        "type": "FeatureCollection",
        "features": out,
        "summary": {"sites": len(project.features), "counts": counts, "sections_screened": project.sections},
    }


def _feature_id(feature: dict, index: int) -> str:
    feature = feature or {}
    fid = feature.get("id", (feature.get("properties") or {}).get("id"))
    return str(fid) if fid is not None else str(index)


__all__ = ["Project", "ScreeningError", "read_project", "screen_project", "WGS84"]
