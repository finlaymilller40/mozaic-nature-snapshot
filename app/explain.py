"""Plain-English versions of screening results, for the AI report and assistant.

The AI never sees raw rule output. It gets a facts sheet built here: every
part of every site with a reference (S1.P2 = site 1, part 2), where it is,
its level and reasons in plain words, and the protected features behind it.
The wording matches what the map shows (app/static/app.js).

Next steps come from app/rules.yaml, not the AI: one per level (with how
Mozaic would help) and one per kind of reason. The AI explains them.
"""

from app.scoring import load_rules

IRZ_LABELS = {
    "irz_all_consultations": "all planning applications",
    "irz_infrastructure": "infrastructure",
    "irz_wind_solar": "wind and solar energy",
    "irz_minerals_oil_gas": "minerals, oil and gas",
    "irz_rural_non_residential": "rural non-residential development",
    "irz_residential": "residential development",
    "irz_rural_residential": "rural residential development",
    "irz_air_pollution": "air pollution",
    "irz_combustion": "combustion",
    "irz_waste": "waste",
    "irz_composting": "composting",
    "irz_discharges": "discharges",
    "irz_water_supply": "water supply",
}
DESIGNATIONS = {"sac": "a Special Area of Conservation", "spa": "a Special Protection Area", "ramsar": "a Ramsar wetland"}
FEATURE_KINDS = {
    "sssi": "SSSI",
    "sac": "SAC",
    "spa": "SPA",
    "ramsar": "Ramsar wetland",
    "ancient_woodland": "ancient woodland",
    "priority_habitat": "priority habitat",
    "risk_zone": "SSSI Impact Risk Zone",
}
GENERIC_HABITAT = "No main habitat but additional habitats present"
ANSWER_WORDS = {
    "beyond_site": "Work outside land the company already operates",
    "abstraction": "Taking new or extra water from a river, lake or borehole",
    "dewatering": "Pumping groundwater out of trenches or pits",
    "physical_survey_required": "An in-person site survey is required",
}


def part_ref(site_index: int, part_index: int) -> str:
    return f"S{site_index + 1}.P{part_index + 1}"


def describe_reason(r: dict) -> str:
    """One reason as a sentence, as on the map."""
    rule, value, detail = r["rule"], r.get("value") or "", r.get("detail") or ""
    if rule == 1:
        return f"The work is inside {value}, {DESIGNATIONS.get(r.get('layer'), 'a protected European site')}."
    if rule == 2:
        return f"The work is inside {value.removesuffix(' SSSI')}, a Site of Special Scientific Interest."
    if rule == 3:
        return f"Ancient woodland ({value}) is within the buffer: {detail.split('. Source')[0]}."
    if rule == 4:
        scope = "all planning applications" if value.endswith("=1") else "all planning applications except householder ones"
        return f"Natural England's risk zone here asks to be consulted on {scope}."
    if rule == 5:
        return "The work involves abstraction or dewatering in a risk zone it triggers; a groundwater specialist may be needed."
    if rule == 6:
        category = IRZ_LABELS.get(value.split("=")[0], "this kind of work")
        source = detail.removeprefix("Triggered for ").split(" (")[0].lower()
        return f"The work is in an SSSI risk zone that flags {category}, which applies to {source}."
    if rule == 7:
        habitat = "priority habitat" if value == GENERIC_HABITAT else f"priority habitat ({value.lower()})"
        return f"The work overlaps {habitat}."
    if rule == 8:
        return "No protected sites, ancient woodland, priority habitat or risk-zone triggers were found."
    if rule == 10:
        return "An in-person site survey is required, which raises specialist assessment (A3) to A4."
    return detail


def reason_next_steps(reasons: list[dict], rules: dict) -> list[str]:
    """The next step for each kind of reason in a part, in rule order, without repeats.

    A step that only repeats the part's level step (e.g. "nothing found" at A1)
    is left out, since the level step is given alongside.
    """
    by_id = {r["id"]: r for r in rules["rules"]}
    level_steps = {s["next_step"] for s in rules["level_next_steps"].values()}
    steps = (by_id[r["rule"]].get("next_step") for r in sorted(reasons, key=lambda r: r["rule"]))
    return [s for s in dict.fromkeys(steps) if s and s not in level_steps]


def level_next_steps(levels, rules: dict) -> dict:
    """{level: {next_step, with_mozaic}} for the given levels, lowest first."""
    present = set(levels)
    return {lvl: dict(rules["level_next_steps"][lvl]) for lvl in rules["levels"] if lvl in present}


def part_where(props: dict, part: dict) -> str:
    if props.get("kind") == "line":
        return f"{part['start_km']:.2f}–{part['end_km']:.2f} km along the route"
    if len(props.get("parts", [])) == 1:
        return f"the whole {props.get('buffer_m', 50)} m area" if props.get("kind") == "point" else "the whole site"
    return f"{part['area_ha']:.1f} ha ({round(part['share'] * 100)}% of the site)"


def feature_name(f: dict) -> str:
    if f["kind"] == "risk_zone":
        return "SSSI Impact Risk Zone"
    if f["kind"] == "priority_habitat":
        return "Priority habitat" if f["name"] == GENERIC_HABITAT else f"{f['name']} (priority habitat)"
    if f["kind"] == "ancient_woodland":
        return f["name"] or "Ancient woodland"
    return f["name"]


def feature_label(f: dict) -> str:
    """Name plus what kind of feature it is, without repeating the kind."""
    name, kind = feature_name(f), FEATURE_KINDS[f["kind"]]
    return name if name.lower().endswith(kind.lower()) or name.lower().endswith(f"({kind.lower()})") else f"{name} ({kind})"


def facts_sheet(result: dict, intake: dict) -> dict:
    """Everything the AI may say about a screening, in plain words."""
    rules = load_rules()
    meanings = rules["level_meanings"]
    sites = []
    for i, site in enumerate(result["features"]):
        props = site["properties"]
        features = {f["ref"]: f for f in props.get("features", [])}
        entry = {
            "site": f"S{i + 1}",
            "name": props.get("name") or f"Site {i + 1}",
            "shape": {"line": "route", "point": "point", "polygon": "area"}.get(props.get("kind"), "site"),
            "level": props.get("level"),
        }
        if props.get("kind") == "line":
            entry["length_km"] = props.get("length_km")
            entry["how_checked"] = (f"A working corridor {props.get('corridor_m')} m either side of the route was checked, "
                                    "so 'inside' a site means the corridor overlaps it, even if the pipe's centreline doesn't.")
        elif props.get("kind") == "point":
            entry["how_checked"] = f"A {props.get('buffer_m')} m circle around the point was checked."
        elif props.get("kind") == "polygon":
            entry["how_checked"] = "The area as drawn was checked."
        if props.get("status") == "not_screened":
            entry["not_screened"] = props.get("detail")
        if props.get("outside"):
            entry["not_checked"] = props["outside"]["warning"]
        entry["parts"] = [
            {
                "ref": part_ref(i, j),
                "where": part_where(props, part),
                "level": part["level"],
                "what_it_means": meanings[part["level"]],
                "may_rise_to": part.get("may_escalate_to"),
                "reasons": list(dict.fromkeys(describe_reason(r) for r in part["reasons"])),
                "protected_features": sorted({
                    feature_label(features[ref])
                    for r in part["reasons"] for ref in r.get("refs", []) if ref in features
                }),
                "nearest_sssi_unit": (part.get("context") or {}).get("sssi_condition"),
                "next_steps": reason_next_steps(part["reasons"], rules),
            }
            for j, part in enumerate(props.get("parts", []))
        ]
        sites.append(entry)
    return {
        "project": {
            "work_type": rules["work_types"][intake["work_type"]]["label"],
            "answers": {ANSWER_WORDS[k]: intake[k] for k in ANSWER_WORDS if k in intake},
        },
        "level_meanings": meanings,
        # What to do at each level found, from rules.yaml. Each part adds its
        # own next steps for its reasons.
        "next_steps_by_level": level_next_steps((p["level"] for s in sites for p in s["parts"]), rules),
        "sites": sites,
    }


def part_levels(sheet: dict) -> dict[str, str]:
    """ref -> level for every part in a facts sheet."""
    return {p["ref"]: p["level"] for s in sheet["sites"] for p in s["parts"]}
