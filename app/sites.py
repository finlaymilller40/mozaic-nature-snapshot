"""Turn submitted sites into screened results.

Points are screened as a circle, polygons as drawn, and lines (pipelines)
as a corridor scored in short sections; the sizes are in rules.yaml under
site_geometry. Only the part of a site inside the data area (the AOI) is
screened. Anything outside is returned separately with a warning that it
hasn't been checked.

All measuring is done in British National Grid (EPSG:27700) metres.
"""

from dataclasses import dataclass, field

import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, substring, transform

from app.scoring import check_site, load_rules, score_site

WGS84 = "EPSG:4326"
BNG = "EPSG:27700"
_TO_BNG = Transformer.from_crs(WGS84, BNG, always_xy=True)
_TO_WGS84 = Transformer.from_crs(BNG, WGS84, always_xy=True)

KINDS = {
    "Point": "point", "MultiPoint": "point",
    "LineString": "line", "MultiLineString": "line",
    "Polygon": "polygon", "MultiPolygon": "polygon",
}
# Coordinate ranges used to check the stated coordinate system is plausible.
CRS_BOUNDS = {
    WGS84: (-180, -90, 180, 90),
    BNG: (0, 0, 700_000, 1_300_000),
}
NOT_SCREENED = "not_screened"
MIN_OUTSIDE_M = 1  # ignore slivers smaller than this (m for lines, m² for areas)
POINT_EDGE_TOLERANCE_M = 1  # points this close to the data area count as inside


class SiteError(ValueError):
    """A submitted site can't be read. The message says why, in plain English."""


def settings() -> dict:
    return load_rules()["site_geometry"]


# ---------------------------------------------------------------- reading


def read_geometry(geometry: dict, crs: str) -> BaseGeometry:
    """GeoJSON geometry in `crs` -> a valid 2D shape in British National Grid."""
    kind = (geometry or {}).get("type")
    if kind not in KINDS:
        raise SiteError(f"geometry must be a point, line or polygon, got {kind!r}")
    if crs not in CRS_BOUNDS:
        raise SiteError(f"crs must be {WGS84} (longitude/latitude) or {BNG} (British National Grid), got {crs!r}")
    try:
        geom = shapely.force_2d(shape(geometry))
    except Exception:
        raise SiteError("invalid geometry") from None
    if geom.is_empty:
        raise SiteError("geometry is empty")

    minx, miny, maxx, maxy = geom.bounds
    lo_x, lo_y, hi_x, hi_y = CRS_BOUNDS[crs]
    if not (lo_x <= minx <= maxx <= hi_x and lo_y <= miny <= maxy <= hi_y):
        if crs == WGS84:
            raise SiteError("coordinates must be longitude/latitude; for eastings/northings set crs to EPSG:27700")
        raise SiteError("coordinates must be British National Grid eastings/northings in metres")

    # Eastings/northings this small are in the sea off Cornwall; they're
    # almost certainly longitude/latitude sent with the wrong crs.
    if crs == BNG and maxx <= 180 and maxy <= 90:
        raise SiteError("these look like longitude/latitude, not British National Grid eastings/northings; "
                        "set crs to EPSG:4326")
    if crs == WGS84:
        geom = transform(_TO_BNG.transform, geom)
    if KINDS[geom.geom_type] == "polygon" and not geom.is_valid:
        geom = shapely.make_valid(geom, method="structure", keep_collapsed=False)
    if KINDS[geom.geom_type] == "line" and geom.length == 0:
        raise SiteError("line has no length")
    return geom


def to_lat_long(geom: BaseGeometry) -> dict:
    """British National Grid shape -> GeoJSON geometry in lat/long (6 d.p.)."""
    return mapping(shapely.set_precision(transform(_TO_WGS84.transform, geom), 1e-6))


# --------------------------------------------------------------- planning


@dataclass
class Section:
    start_m: float
    end_m: float
    line: LineString
    piece: int  # sections from different inside pieces never merge


@dataclass
class Plan:
    """A site cut down to what gets screened."""
    kind: str
    geometry: BaseGeometry  # whole site, BNG
    aoi: BaseGeometry  # the data area; only the part of the site inside it is screened
    footprint: BaseGeometry | None = None  # screened area for points/polygons
    sections: list[Section] = field(default_factory=list)  # lines only
    route_area_ha: float = 0.0  # lines: whole inside corridor, for area-based rules
    outside: BaseGeometry | None = None
    outside_size: float = 0.0  # m for lines, m² for points/polygons

    @property
    def screenable(self) -> bool:
        return bool(self.sections) or (self.footprint is not None and not self.footprint.is_empty)


def plan_site(geom: BaseGeometry, aoi: BaseGeometry) -> Plan:
    kind = KINDS[geom.geom_type]
    cfg = settings()
    if kind == "point":
        circle = geom.buffer(cfg["point_buffer_m"])
        # A point on the edge can land a few mm outside after converting
        # between lat/long and the grid, so allow for that rounding.
        inside = geom.distance(aoi) <= POINT_EDGE_TOLERANCE_M
        footprint = circle.intersection(aoi) if inside else None
        outside = circle.difference(aoi) if footprint is not None else circle
        return Plan(kind, geom, aoi, footprint=footprint, outside=outside, outside_size=outside.area)
    if kind == "polygon":
        footprint = _polygonal(geom.intersection(aoi))
        outside = _polygonal(geom.difference(aoi))
        return Plan(kind, geom, aoi, footprint=footprint, outside=outside, outside_size=outside.area)
    return _plan_line(geom, aoi, cfg)


def _plan_line(geom: BaseGeometry, aoi: BaseGeometry, cfg: dict) -> Plan:
    parts = list(geom.geoms) if isinstance(geom, MultiLineString) else [geom]
    sections: list[Section] = []
    inside_lines: list[LineString] = []
    outside_lines: list[LineString] = []
    offset = 0.0
    piece_no = 0
    for part in parts:
        for piece in _lines(part.intersection(aoi)):
            start, end = part.project(Point(piece.coords[0])), part.project(Point(piece.coords[-1]))
            if end < start:  # keep the route's direction
                piece, start = LineString(piece.coords[::-1]), end
            inside_lines.append(piece)
            sections += _cut(piece, offset + start, cfg["section_length_m"], piece_no)
            piece_no += 1
        outside_lines += _lines(part.difference(aoi))
        offset += part.length

    corridor = shapely.union_all([l.buffer(cfg["line_corridor_m"]) for l in inside_lines]) if inside_lines else None
    outside = MultiLineString(outside_lines) if outside_lines else None
    return Plan(
        "line", geom, aoi,
        sections=sections,
        route_area_ha=corridor.intersection(aoi).area / 10_000 if corridor is not None else 0.0,
        outside=outside,
        outside_size=outside.length if outside is not None else 0.0,
    )


def _cut(piece: LineString, start_m: float, length_m: float, piece_no: int) -> list[Section]:
    """Cut a line into sections of length_m; a short remainder joins the last one."""
    total = piece.length
    edges = [0.0]
    while edges[-1] + length_m < total:
        edges.append(edges[-1] + length_m)
    if len(edges) > 1 and total - edges[-1] < length_m / 2:
        edges.pop()
    edges.append(total)
    return [
        Section(start_m + a, start_m + b, substring(piece, a, b), piece_no)
        for a, b in zip(edges, edges[1:])
    ]


def _lines(geom: BaseGeometry) -> list[LineString]:
    if geom.is_empty:
        return []
    if isinstance(geom, LineString):
        return [geom]
    if hasattr(geom, "geoms"):
        merged = linemerge([g for g in geom.geoms if isinstance(g, LineString)] +
                           [p for g in geom.geoms if isinstance(g, MultiLineString) for p in g.geoms])
        return [merged] if isinstance(merged, LineString) else list(getattr(merged, "geoms", []))
    return []


def _polygonal(geom: BaseGeometry) -> BaseGeometry:
    """Keep only the polygon parts of an overlay result."""
    if geom.is_empty or geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    polys = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(polys) if polys else shapely.Polygon()


# --------------------------------------------------------------- screening


def screen(plan: Plan, intake: dict) -> dict:
    """Score a planned site. Returns result properties (geometries in lat/long)."""
    if not plan.screenable:
        return {
            "level": None, "may_escalate_to": None, "reasons": [], "context": None,
            "status": NOT_SCREENED,
            "detail": "This site is outside the area the data covers, so it can't be screened.",
            "outside": None,
        }
    if plan.kind == "line":
        return _screen_line(plan, intake)

    score = score_site(check_site(plan.footprint), intake)
    result = {k: score[k] for k in ("level", "may_escalate_to", "reasons", "context")}
    if plan.kind == "point":
        result["buffer_m"] = settings()["point_buffer_m"]
    result["outside"] = _outside_area(plan)
    return result


def _outside_area(plan: Plan) -> dict | None:
    if plan.outside is None or plan.outside_size < MIN_OUTSIDE_M:
        return None
    size = format_area(plan.outside_size)
    if plan.kind == "point":
        warning = (f"Part of the {settings()['point_buffer_m']} m area around this point ({size}) is outside "
                   "the screened area and has not been checked.")
    else:
        warning = f"{size} of this site is outside the screened area and has not been checked."
    return {"area_ha": round(plan.outside_size / 10_000, 4), "geometry": to_lat_long(plan.outside), "warning": warning}


def _screen_line(plan: Plan, intake: dict) -> dict:
    cfg = settings()
    levels = load_rules()["levels"]

    scored = []
    for section in plan.sections:
        footprint = section.line.buffer(cfg["line_corridor_m"]).intersection(plan.aoi)
        facts = check_site(footprint)
        facts["site_area_ha"] = plan.route_area_ha  # area rules use the whole route, not one section
        scored.append((section, score_site(facts, intake)))

    stretches = _merge(scored)
    level = max((s["level"] for s in stretches), key=levels.index)
    top = [s["may_escalate_to"] for s in stretches if s["level"] == level and s["may_escalate_to"]]

    outside = None
    if plan.outside is not None and plan.outside_size >= MIN_OUTSIDE_M:
        outside = {
            "length_km": round(plan.outside_size / 1000, 3),
            "geometry": to_lat_long(plan.outside),
            "warning": f"{format_length(plan.outside_size)} of this route is outside the screened area "
                       "and has not been checked.",
        }

    return {
        "level": level,
        "may_escalate_to": max(top, key=levels.index) if top else None,
        "reasons": _route_reasons(stretches),
        "context": _route_context([score for _, score in scored]),
        "length_km": round(plan.geometry.length / 1000, 3),
        "corridor_m": cfg["line_corridor_m"],
        "sections": [{k: v for k, v in s.items() if k != "_key"} for s in stretches],
        "outside": outside,
    }


def _merge(scored: list[tuple[Section, dict]]) -> list[dict]:
    """Join neighbouring sections whose level and reasons match."""
    stretches: list[dict] = []
    lines: list[list[LineString]] = []
    for section, score in scored:
        key = (score["level"], score["may_escalate_to"],
               frozenset((r["rule"], r["layer"], r["value"]) for r in score["reasons"]))
        last = stretches[-1] if stretches else None
        if last and last["_key"] == key and last["_piece"] == section.piece and abs(last["_end"] - section.start_m) < 0.01:
            last["_end"] = section.end_m
            lines[-1].append(section.line)
            continue
        stretches.append({
            "_key": key, "_piece": section.piece, "_start": section.start_m, "_end": section.end_m,
            "level": score["level"], "may_escalate_to": score["may_escalate_to"],
            "reasons": score["reasons"], "context": score["context"],
        })
        lines.append([section.line])

    out = []
    for stretch, parts in zip(stretches, lines):
        merged = linemerge(parts) if len(parts) > 1 else parts[0]
        out.append({
            "_key": stretch["_key"],
            "start_km": round(stretch["_start"] / 1000, 3),
            "end_km": round(stretch["_end"] / 1000, 3),
            "level": stretch["level"],
            "may_escalate_to": stretch["may_escalate_to"],
            "reasons": stretch["reasons"],
            "context": stretch["context"],
            "geometry": to_lat_long(merged),
        })
    return out


def _route_reasons(stretches: list[dict]) -> list[dict]:
    """Every reason on the route once, with where along the route it applies."""
    found: dict[tuple, dict] = {}
    for s in stretches:
        for r in s["reasons"]:
            key = (r["rule"], r["layer"], r["value"])
            entry = found.setdefault(key, {**r, "ranges_km": []})
            ranges = entry["ranges_km"]
            if ranges and abs(ranges[-1][1] - s["start_km"]) < 0.0005:
                ranges[-1][1] = s["end_km"]  # extend a continuous run
            else:
                ranges.append([s["start_km"], s["end_km"]])
    reasons = sorted(found.values(), key=lambda r: (r["rule"], r["ranges_km"][0][0]))
    for r in reasons:
        r["where"] = ", ".join(f"{a:.2f}–{b:.2f} km" for a, b in r["ranges_km"])
    return reasons


def _route_context(scores: list[dict]) -> dict:
    units = [s["context"]["sssi_condition"] for s in scores if s["context"]["sssi_condition"]]
    linked = {}
    for s in scores:
        for zone in s["context"]["european_site_linkage"]:
            linked.setdefault(zone["zone_id"], zone)
    return {
        "sssi_condition": min(units, key=lambda u: u["distance_m"]) if units else None,
        "european_site_linkage": list(linked.values()),
        "direction_of_effect": None,
    }


# ------------------------------------------------------------- formatting


def format_length(metres: float) -> str:
    return f"{metres / 1000:.1f} km" if metres >= 1000 else f"{round(metres)} m"


def format_area(m2: float) -> str:
    return f"{m2 / 10_000:.1f} ha" if m2 >= 1_000 else f"{round(m2)} m²"
