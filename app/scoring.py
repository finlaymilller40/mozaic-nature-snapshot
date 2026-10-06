"""Score a site against the assurance rules in app/rules.yaml.

check_site(geometry)       -> facts about the site from the cleaned layers
score_site(facts, intake)  -> assurance level (A1-A4) with reasons

Geometries are in British National Grid (EPSG:27700), like data/clean/.
Facts and scores are plain dicts and lists, so they can be saved as JSON.
"""

from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd
import yaml
from shapely.geometry.base import BaseGeometry

ROOT = Path(__file__).resolve().parent.parent
CLEAN_DIR = ROOT / "data" / "clean"
RULES_PATH = Path(__file__).resolve().parent / "rules.yaml"

YES = {"yes", "y", "true"}
NO = {"no", "n", "false"}


# ---------------------------------------------------------------- loading


@lru_cache(maxsize=None)
def load_rules(path: Path = RULES_PATH) -> dict:
    with open(path) as f:
        rules = yaml.safe_load(f)
    unknown = {r["check"] for r in rules["rules"]} - KNOWN_CHECKS
    if unknown:
        raise ValueError(f"rules.yaml uses checks scoring.py doesn't know: {sorted(unknown)}")
    _check_next_steps(rules)
    return rules


def _check_next_steps(rules: dict) -> None:
    """Every level, and every rule that gives a reason, must say what to do next."""
    steps = rules.get("level_next_steps", {})
    missing = [f"level {lvl}" for lvl in rules["levels"]
               if not (steps.get(lvl, {}).get("next_step") and steps.get(lvl, {}).get("with_mozaic"))]
    missing += [f"rule {r['id']}" for r in rules["rules"] if r["check"] != "intake_complete" and not r.get("next_step")]
    if missing:
        raise ValueError(f"rules.yaml has no next step for: {', '.join(missing)}")


@lru_cache(maxsize=None)
def load_layers() -> dict[str, gpd.GeoDataFrame]:
    def read(name: str) -> gpd.GeoDataFrame:
        return gpd.read_file(CLEAN_DIR / f"{name}.gpkg")

    european = pd.concat(
        [
            read("sac").assign(designation="SAC").rename(columns={"sac_name": "site", "sac_code": "site_code"}),
            read("spa").assign(designation="SPA").rename(columns={"spa_name": "site", "spa_code": "site_code"}),
            read("ramsar").assign(designation="Ramsar").rename(columns={"name": "site", "code": "site_code"}),
        ],
        ignore_index=True,
    )[["designation", "site", "site_code", "geometry"]]

    layers = {
        "european": gpd.GeoDataFrame(european, geometry="geometry", crs="EPSG:27700"),
        "sssi": read("sssi"),
        "sssi_units": read("sssi_units"),
        "ancient_woodland": read("ancient_woodland"),
        "priority_habitat": read("priority_habitat"),
        "irz": read("irz"),
    }
    for gdf in layers.values():
        gdf.sindex  # build spatial indexes once
    layers["irz"]["european_sites"] = _zone_european_links(layers["irz"], layers["european"])
    return layers


def _zone_european_links(irz: gpd.GeoDataFrame, european: gpd.GeoDataFrame) -> list[list[str]]:
    """For each IRZ zone, the SAC/SPA/Ramsar sites it overlaps (worked out once)."""
    zone_idx, site_idx = european.sindex.query(irz.geometry.values, predicate="intersects")
    links: list[set[str]] = [set() for _ in range(len(irz))]
    for z, s in zip(zone_idx, site_idx):
        site = european.iloc[s]
        links[z].add(f"{site.designation}: {site.site} ({site.site_code})")
    return [sorted(l) for l in links]


def _rule(rules: dict, check: str) -> dict:
    return next(r for r in rules["rules"] if r["check"] == check)


def _hits(gdf: gpd.GeoDataFrame, geometry: BaseGeometry, **query) -> gpd.GeoDataFrame:
    idx = gdf.sindex.query(geometry, predicate=query.pop("predicate", "intersects"), **query)
    return gdf.iloc[sorted(idx)]


# ------------------------------------------------------------------ facts


def check_site(geometry: BaseGeometry, rules: dict | None = None) -> dict:
    """Collect what the cleaned layers say about a site (EPSG:27700)."""
    rules = rules or load_rules()
    layers = load_layers()
    aw_buffer = _rule(rules, "near_ancient_woodland")["buffer_m"]
    irz_columns = [c for c in layers["irz"].columns if c.startswith("irz_")]

    european = _hits(layers["european"], geometry)
    sssi = _hits(layers["sssi"], geometry)
    woods = _hits(layers["ancient_woodland"], geometry, predicate="dwithin", distance=aw_buffer)
    habitat = _hits(layers["priority_habitat"], geometry)
    zones = _hits(layers["irz"], geometry)

    irz_zones = [
        {
            "key": f"irz:{index}",
            "id": zone["id"],
            "irzcode": zone["irzcode"],
            "values": {c: int(zone[c]) for c in irz_columns},
            "european_sites": list(zone["european_sites"]),
        }
        for index, zone in zones.iterrows()
    ]

    return {
        "site_area_ha": round(geometry.area / 10_000, 4),
        "european_sites": [
            {"designation": r.designation, "name": r.site, "code": r.site_code,
             "keys": _keys("european", european, (european.designation == r.designation) & (european.site_code == r.site_code))}
            for r in european.drop_duplicates(["designation", "site_code"]).itertuples()
        ],
        "sssi": [
            {"name": r.name, "ref_code": r.ref_code, "keys": _keys("sssi", sssi, sssi.ref_code == r.ref_code)}
            for r in sssi.drop_duplicates("ref_code").itertuples()
        ],
        "ancient_woodland": [
            {
                "key": f"ancient_woodland:{r.Index}",
                "name": _text(r.name),
                "status": r.status,
                "source": r.source,
                "distance_m": round(r.geometry.distance(geometry), 1),
            }
            for r in woods.itertuples()
        ],
        "priority_habitat": [
            {"key": f"priority_habitat:{r.Index}", "uid": r.uid, "main_habitat": r.mainhabs,
             "additional_habitats": _text(r.addhabs)}
            for r in habitat.itertuples()
        ],
        "irz_zones": irz_zones,
        "nearest_sssi_unit": _nearest_sssi_unit(layers["sssi_units"], geometry),
    }


def _keys(layer: str, rows: gpd.GeoDataFrame, mask) -> list[str]:
    """Keys ("layer:row") of the map features behind a fact, so results can show them."""
    return [f"{layer}:{i}" for i in rows.index[mask]]


def _text(value) -> str | None:
    return None if value is None or pd.isna(value) or str(value).strip() == "" else str(value)


def _nearest_sssi_unit(units: gpd.GeoDataFrame, geometry: BaseGeometry) -> dict | None:
    if units.empty:
        return None
    (_, idx), distance = units.sindex.nearest(geometry, return_distance=True)
    unit = units.iloc[idx[0]]
    date = pd.to_datetime(unit["COND_DATE"], format="%a, %d %b %Y %H:%M:%S GMT", errors="coerce")
    return {
        "sssi_name": unit["SSSI_NAME"],
        "unit_id": str(unit["ID"]),
        "condition": unit["CONDITION"],
        "condition_date": None if pd.isna(date) else date.date().isoformat(),
        "distance_m": round(float(distance[0]), 1),
    }


# ---------------------------------------------------------------- scoring


def score_site(facts: dict, intake: dict, rules: dict | None = None) -> dict:
    """Return the assurance level, possible escalation and every reason.

    The highest level found wins. Context items are reported but never
    change the level.
    """
    rules = rules or load_rules()
    intake = validate_intake(intake, rules)  # rule 9
    levels = rules["levels"]
    irz_hits = _irz_trigger_hits(facts, intake, rules)

    reasons = []
    for rule in rules["rules"]:
        check = FINDING_CHECKS.get(rule["check"])
        if check:
            for reason in check(rule, facts, intake, irz_hits):
                reasons.append({"rule": rule["id"], "level": rule["level"], **reason})

    if not reasons:
        rule = _rule(rules, "nothing_found")
        reasons.append({
            "rule": rule["id"], "level": rule["level"], "layer": None, "value": None,
            "detail": f"{rule['description']}: {rule['note'].lower()}", "refs": [],
        })

    level = max((r["level"] for r in reasons), key=levels.index)
    escalations = [
        _rule_by_id(rules, r["rule"]).get("may_escalate_to")
        for r in reasons if r["level"] == level
    ]
    escalations = [e for e in escalations if e]
    may_escalate_to = max(escalations, key=levels.index) if escalations else None

    survey = _rule(rules, "physical_survey_upgrade")
    if intake["physical_survey_required"] and level == survey["from_level"]:
        level, may_escalate_to = survey["to_level"], None
        reasons.append({
            "rule": survey["id"], "level": survey["to_level"], "layer": "intake",
            "value": "physical_survey_required=yes",
            "detail": f"Physical survey required: {survey['from_level']} becomes {survey['to_level']}", "refs": [],
        })

    return {
        "level": level,
        "may_escalate_to": may_escalate_to,
        "reasons": reasons,
        "context": _context(facts, irz_hits, rules),
    }


def validate_intake(intake: dict, rules: dict | None = None) -> dict:
    """Rule 9: every question answered yes or no, and a known work type."""
    rules = rules or load_rules()
    problems = []
    work_type = intake.get("work_type")
    if work_type not in rules["work_types"]:
        problems.append(f"work_type must be one of {sorted(rules['work_types'])}, got {work_type!r}")

    clean = {"work_type": work_type}
    for field in rules["intake_fields"]:
        answer = intake.get(field)
        text = str(answer).strip().lower() if answer is not None else ""
        if isinstance(answer, bool):
            clean[field] = answer
        elif text in YES:
            clean[field] = True
        elif text in NO:
            clean[field] = False
        else:
            problems.append(f"{field} must be yes or no, got {answer!r}")

    if problems:
        raise ValueError("Incomplete intake (rule 9): " + "; ".join(problems))
    return clean


def _rule_by_id(rules: dict, rule_id: int) -> dict:
    return next(r for r in rules["rules"] if r["id"] == rule_id)


def _irz_trigger_hits(facts: dict, intake: dict, rules: dict) -> list[dict]:
    """IRZ zones whose values trigger for this work type and intake (rule 6)."""
    work_type = rules["work_types"][intake["work_type"]]
    triggers = [(t, work_type["label"]) for t in work_type["triggers"]]
    for field, spec in rules.get("intake_triggers", {}).items():
        if intake.get(field):
            triggers += [(t, spec["label"]) for t in spec["triggers"]]

    hits = []
    for zone in facts["irz_zones"]:
        for trigger, source in triggers:
            if facts["site_area_ha"] < trigger.get("min_site_area_ha", 0):
                continue
            value = zone["values"][trigger["category"]]
            if value in trigger["values"]:
                hits.append({"zone": zone, "category": trigger["category"], "value": value, "source": source})
    return hits


# One function per finding rule. Each returns reasons without rule/level.


def _overlaps_european_site(rule, facts, intake, irz_hits):
    return [
        {"layer": s["designation"].lower(), "value": s["name"],
         "detail": f"{rule['description']}: {s['designation']} {s['name']} ({s['code']})", "refs": s.get("keys", [])}
        for s in facts["european_sites"]
    ]


def _overlaps_sssi(rule, facts, intake, irz_hits):
    return [
        {"layer": "sssi", "value": s["name"], "detail": f"{rule['description']}: {s['name']}", "refs": s.get("keys", [])}
        for s in facts["sssi"]
    ]


def _near_ancient_woodland(rule, facts, intake, irz_hits):
    return [
        {
            "layer": "ancient_woodland",
            "value": w["name"] or w["status"],
            "detail": f"{w['status']} ({w['source']} inventory) {w['distance_m']} m away, "
                      f"within {rule['buffer_m']} m. Source: {rule['source']}",
            "refs": [w["key"]] if w.get("key") else [],
        }
        for w in facts["ancient_woodland"]
    ]


def _irz_all_consultations(rule, facts, intake, irz_hits):
    zones = defaultdict(list)
    for zone in facts["irz_zones"]:
        value = zone["values"]["irz_all_consultations"]
        if value in rule["values"]:
            zones[value].append(zone.get("key"))
    return [
        {"layer": "irz", "value": f"irz_all_consultations={v}", "detail": f"{rule['description']} ({len(keys)} zone(s))",
         "refs": [k for k in keys if k]}
        for v, keys in sorted(zones.items())
    ]


def _abstraction_or_dewatering_with_irz_trigger(rule, facts, intake, irz_hits):
    answered = [f for f in ("abstraction", "dewatering") if intake[f]]
    if not answered or not irz_hits:
        return []
    triggers = sorted({f"{h['category']}={h['value']}" for h in irz_hits})
    return [{
        "layer": "intake",
        "value": ", ".join(f"{f}=yes" for f in answered),
        "detail": f"{rule['description']} ({', '.join(triggers)}). {rule['note']}",
        "refs": sorted({h["zone"]["key"] for h in irz_hits if h["zone"].get("key")}),
    }]


def _irz_work_type_trigger(rule, facts, intake, irz_hits):
    grouped = defaultdict(dict)
    for hit in irz_hits:
        grouped[(hit["category"], hit["value"], hit["source"])][hit["zone"]["id"]] = hit["zone"].get("key")
    return [
        {"layer": "irz", "value": f"{cat}={value}", "detail": f"Triggered for {source} ({len(zones)} zone(s))",
         "refs": sorted(k for k in zones.values() if k)}
        for (cat, value, source), zones in sorted(grouped.items())
    ]


def _overlaps_priority_habitat(rule, facts, intake, irz_hits):
    keys = defaultdict(list)
    for h in facts["priority_habitat"]:
        keys[h["main_habitat"]].append(h.get("key"))
    return [
        {"layer": "priority_habitat", "value": habitat, "detail": f"{rule['description']} ({len(k)} polygon(s))",
         "refs": [x for x in k if x]}
        for habitat, k in sorted(keys.items())
    ]


FINDING_CHECKS = {
    "overlaps_european_site": _overlaps_european_site,
    "overlaps_sssi": _overlaps_sssi,
    "near_ancient_woodland": _near_ancient_woodland,
    "irz_all_consultations": _irz_all_consultations,
    "abstraction_or_dewatering_with_irz_trigger": _abstraction_or_dewatering_with_irz_trigger,
    "irz_work_type_trigger": _irz_work_type_trigger,
    "overlaps_priority_habitat": _overlaps_priority_habitat,
}
# Checks handled directly in score_site / validate_intake.
KNOWN_CHECKS = set(FINDING_CHECKS) | {"nothing_found", "intake_complete", "physical_survey_upgrade"}


def _context(facts: dict, irz_hits: list[dict], rules: dict) -> dict:
    """Information for the assessor that never changes the level."""
    all_consultations = _rule(rules, "irz_all_consultations")["values"]
    triggered = {}
    for zone in [h["zone"] for h in irz_hits] + [
        z for z in facts["irz_zones"] if z["values"]["irz_all_consultations"] in all_consultations
    ]:
        if zone["european_sites"]:
            triggered[zone["id"]] = {"zone_id": zone["id"], "irzcode": zone["irzcode"], "european_sites": zone["european_sites"]}
    return {
        "sssi_condition": facts["nearest_sssi_unit"],
        "european_site_linkage": list(triggered.values()),
        "direction_of_effect": None,  # not available from the data
    }
