"""Tests for app/scoring.py.

Most tests score hand-made facts, so they don't need the data files. The
check_site tests at the end use data/clean/ and are skipped without it.
"""

import pytest

from app.scoring import CLEAN_DIR, check_site, load_rules, score_site, validate_intake

IRZ_CATEGORIES = [
    "irz_all_consultations", "irz_infrastructure", "irz_wind_solar", "irz_minerals_oil_gas",
    "irz_rural_non_residential", "irz_residential", "irz_rural_residential", "irz_air_pollution",
    "irz_combustion", "irz_waste", "irz_composting", "irz_discharges", "irz_water_supply",
]


def zone(zone_id="z1", european_sites=(), **values):
    vals = {c: 0 for c in IRZ_CATEGORIES}
    vals.update(values)
    return {
        "key": f"irz:{zone_id}",
        "id": zone_id,
        "irzcode": "".join(str(vals[c]) for c in IRZ_CATEGORIES),
        "values": vals,
        "european_sites": list(european_sites),
    }


def facts(**overrides):
    base = {
        "site_area_ha": 0.5,
        "european_sites": [],
        "sssi": [],
        "ancient_woodland": [],
        "priority_habitat": [],
        "irz_zones": [zone()],
        "nearest_sssi_unit": {
            "sssi_name": "Example", "unit_id": "1", "condition": "UNFAVOURABLE DECLINING",
            "condition_date": "2024-01-01", "distance_m": 900.0,
        },
    }
    base.update(overrides)
    return base


def intake(**overrides):
    base = {
        "work_type": "storm_overflow", "beyond_site": "no", "abstraction": "no",
        "dewatering": "no", "physical_survey_required": "no",
    }
    base.update(overrides)
    return base


def rules_hit(result):
    return sorted({r["rule"] for r in result["reasons"]})


# ------------------------------------------------------------- levels


def test_a1_nothing_found():
    result = score_site(facts(), intake())
    assert result["level"] == "A1"
    assert result["may_escalate_to"] is None
    assert rules_hit(result) == [8]
    assert "confirm remotely" in result["reasons"][0]["detail"]


def test_a2_irz_trigger_for_work_type():
    result = score_site(facts(irz_zones=[zone(irz_discharges=5)]), intake())
    assert result["level"] == "A2"
    assert result["may_escalate_to"] == "A3"
    assert result["reasons"] == [{
        "rule": 6, "level": "A2", "layer": "irz", "value": "irz_discharges=5",
        "detail": "Triggered for Storm overflow / outfall / works discharge (1 zone(s))",
        "refs": ["irz:z1"],  # the map feature behind the reason
    }]


def test_a2_priority_habitat():
    habitat = [{"uid": "P1", "main_habitat": "Lowland fens", "additional_habitats": None}]
    result = score_site(facts(priority_habitat=habitat), intake())
    assert (result["level"], result["may_escalate_to"]) == ("A2", "A3")
    assert rules_hit(result) == [7]


def test_a3_inside_sssi():
    result = score_site(facts(sssi=[{"name": "Wicken Fen SSSI", "ref_code": "1"}]), intake())
    assert (result["level"], result["may_escalate_to"]) == ("A3", "A4")
    assert rules_hit(result) == [2]


def test_a3_ancient_woodland_within_15m():
    wood = [{"name": "Hayley Wood", "status": "ASNW", "source": "revised", "distance_m": 12.0}]
    result = score_site(facts(ancient_woodland=wood), intake())
    assert result["level"] == "A3"
    assert "within 15 m" in result["reasons"][0]["detail"]


def test_a4_physical_survey_upgrades_a3():
    european = [{"designation": "SAC", "name": "Ouse Washes", "code": "UK0013011"}]
    result = score_site(facts(european_sites=european), intake(physical_survey_required="yes"))
    assert result["level"] == "A4"
    assert result["may_escalate_to"] is None
    assert rules_hit(result) == [1, 10]


# ------------------------------------------------------------ details


def test_highest_level_wins_and_all_reasons_listed():
    result = score_site(
        facts(
            sssi=[{"name": "Wicken Fen SSSI", "ref_code": "1"}],
            irz_zones=[zone(irz_discharges=2)],
            priority_habitat=[{"uid": "P1", "main_habitat": "Lowland fens", "additional_habitats": None}],
        ),
        intake(),
    )
    assert result["level"] == "A3"
    assert rules_hit(result) == [2, 6, 7]


def test_physical_survey_does_not_change_a2():
    result = score_site(facts(irz_zones=[zone(irz_discharges=2)]), intake(physical_survey_required="yes"))
    assert result["level"] == "A2"
    assert 10 not in rules_hit(result)


def test_irz_all_consultations_1_or_2_is_a3_regardless_of_work_type():
    for value in (1, 2):
        result = score_site(facts(irz_zones=[zone(irz_all_consultations=value)]), intake())
        assert result["level"] == "A3"
        assert rules_hit(result) == [4]
    result = score_site(facts(irz_zones=[zone(irz_all_consultations=3)]), intake())
    assert result["level"] == "A1"


def test_beyond_site_adds_all_consultations_trigger():
    site = facts(irz_zones=[zone(irz_all_consultations=3)])
    assert score_site(site, intake(beyond_site="no"))["level"] == "A1"
    result = score_site(site, intake(beyond_site="yes"))
    assert result["level"] == "A2"
    assert result["reasons"][0]["value"] == "irz_all_consultations=3"


def test_abstraction_with_irz_trigger_is_a3_without_escalation():
    result = score_site(facts(irz_zones=[zone(irz_discharges=2)]), intake(dewatering="yes"))
    assert result["level"] == "A3"
    assert result["may_escalate_to"] is None
    assert "groundwater specialist" in result["reasons"][0]["detail"].lower()
    # Without an IRZ trigger, abstraction or dewatering alone adds nothing.
    assert score_site(facts(), intake(dewatering="yes"))["level"] == "A1"


def test_pipe_upgrade_does_not_trigger_infrastructure():
    site = facts(irz_zones=[zone(irz_infrastructure=1)])
    assert score_site(site, intake(work_type="new_pipeline_sewer"))["level"] == "A2"
    assert score_site(site, intake(work_type="pipe_upgrade_refurbishment"))["level"] == "A1"


def test_rural_non_residential_3_only_for_sites_over_1_ha():
    site = dict(irz_zones=[zone(irz_rural_non_residential=3)])
    work = intake(work_type="storm_tank_larger_works")
    assert score_site(facts(site_area_ha=0.5, **site), work)["level"] == "A1"
    assert score_site(facts(site_area_ha=1.5, **site), work)["level"] == "A2"


def test_context_never_changes_level():
    linked = zone(irz_discharges=2, european_sites=["SAC: Ouse Washes (UK0013011)"])
    result = score_site(facts(irz_zones=[linked]), intake())
    assert result["level"] == "A2"
    assert result["context"]["european_site_linkage"][0]["european_sites"] == ["SAC: Ouse Washes (UK0013011)"]
    assert result["context"]["sssi_condition"]["condition"] == "UNFAVOURABLE DECLINING"


@pytest.mark.parametrize("field, value", [
    ("dewatering", "unknown"), ("abstraction", None), ("beyond_site", ""), ("work_type", "teleporter"),
])
def test_rule_9_rejects_incomplete_intake(field, value):
    with pytest.raises(ValueError, match="rule 9"):
        score_site(facts(), intake(**{field: value}))


def test_intake_accepts_booleans_and_yes_no():
    clean = validate_intake(intake(beyond_site=True, abstraction="No", dewatering="YES"))
    assert clean["beyond_site"] is True and clean["abstraction"] is False and clean["dewatering"] is True


def test_rules_yaml_has_every_rule_from_decisions():
    rules = load_rules()
    assert [r["id"] for r in rules["rules"]] == list(range(1, 11))
    assert next(r for r in rules["rules"] if r["id"] == 3)["buffer_m"] == 15


# ------------------------------------------------- check_site (real data)

needs_data = pytest.mark.skipif(
    not (CLEAN_DIR / "irz.gpkg").exists(), reason="data/clean/ not built"
)


@needs_data
def test_check_site_inside_sssi():
    import geopandas as gpd

    sssi = gpd.read_file(CLEAN_DIR / "sssi.gpkg").iloc[0]
    site = sssi.geometry.representative_point().buffer(10)
    found = check_site(site)
    assert sssi["name"] in [s["name"] for s in found["sssi"]]
    assert found["irz_zones"], "every point in the AOI should be in at least one IRZ zone"
    assert found["nearest_sssi_unit"]["distance_m"] == 0.0


@needs_data
def test_check_site_ancient_woodland_buffer():
    import geopandas as gpd

    wood = gpd.read_file(CLEAN_DIR / "ancient_woodland.gpkg").iloc[0]
    edge = wood.geometry.exterior.coords[0] if wood.geometry.geom_type == "Polygon" else wood.geometry.geoms[0].exterior.coords[0]
    from shapely.geometry import Point

    near = Point(edge[0], edge[1]).buffer(1)  # touches the wood
    assert check_site(near)["ancient_woodland"]


# ------------------------------------------------ remaining work types


@pytest.mark.parametrize("value, level", [(1, "A2"), (2, "A1"), (3, "A1")])
def test_chp_triggers_only_on_all_combustion(value, level):
    # Combustion 2 and 3 are > 20 MW and > 50 MW; works CHP is below both.
    result = score_site(facts(irz_zones=[zone(irz_combustion=value)]), intake(work_type="treatment_works_chp"))
    assert result["level"] == level
    if level == "A2":
        assert result["reasons"][0]["value"] == "irz_combustion=1"


@pytest.mark.parametrize("category, value, level", [
    ("irz_composting", 1, "A2"), ("irz_composting", 2, "A2"), ("irz_composting", 3, "A1"),
    ("irz_air_pollution", 1, "A2"), ("irz_air_pollution", 4, "A2"),
    ("irz_air_pollution", 5, "A1"), ("irz_air_pollution", 6, "A1"),  # livestock-only values
])
def test_sludge_ad_composting_and_air_pollution(category, value, level):
    result = score_site(facts(irz_zones=[zone(**{category: value})]), intake(work_type="sludge_anaerobic_digestion"))
    assert result["level"] == level


def test_sludge_ad_lists_both_triggers():
    site = facts(irz_zones=[zone(irz_composting=2, irz_air_pollution=3)])
    result = score_site(site, intake(work_type="sludge_anaerobic_digestion"))
    assert result["level"] == "A2"
    assert sorted(r["value"] for r in result["reasons"]) == ["irz_air_pollution=3", "irz_composting=2"]


@pytest.mark.parametrize("value, level", [(1, "A2"), (2, "A1")])
def test_abstraction_work_type_triggers_water_supply_1(value, level):
    result = score_site(facts(irz_zones=[zone(irz_water_supply=value)]), intake(work_type="new_increased_abstraction"))
    assert result["level"] == level


def test_abstraction_answer_with_water_supply_trigger_is_a3():
    # The abstraction answer adds the Water Supply 1 trigger (rule 6) and,
    # because a trigger fired, rule 5 raises the site to A3.
    site = facts(irz_zones=[zone(irz_water_supply=1)])
    result = score_site(site, intake(work_type="new_increased_abstraction", abstraction="yes"))
    assert result["level"] == "A3"
    assert rules_hit(result) == [5, 6]
