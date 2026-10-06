"""Tests for the AI report and the results assistant. The AI is always faked."""

from types import SimpleNamespace

import anthropic
import geopandas as gpd
import pytest
from fastapi.testclient import TestClient

from app import assistant, llm, main, report
from app.explain import facts_sheet
from app.scoring import CLEAN_DIR, load_layers, load_rules
from app.screening import read_project, screen_project

pytestmark = pytest.mark.skipif(not (CLEAN_DIR / "irz.gpkg").exists(), reason="data/clean/ not built")

INTAKE = {"work_type": "new_pipeline_sewer", "beyond_site": "yes", "abstraction": "no",
          "dewatering": "yes", "physical_survey_required": "no"}
# A 2.2 km route whose first part crosses the Ouse Washes.
ROUTE = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {"name": "Sutton main"},
    "geometry": {"type": "LineString", "coordinates": [[542000, 279000], [544000, 280000]]},
}]}


@pytest.fixture(scope="module")
def project():
    load_layers()
    aoi = gpd.read_file(f"zip://{main.AOI_ZIP}").to_crs("EPSG:27700").union_all()
    return read_project(ROUTE, "EPSG:27700", aoi)


@pytest.fixture(scope="module")
def sheet(project):
    return facts_sheet(screen_project(project, INTAKE), INTAKE)


# ---------------------------------------------------------- facts sheet


def test_facts_sheet_has_refs_levels_and_plain_reasons(sheet):
    site = sheet["sites"][0]
    assert site["shape"] == "route" and "corridor" in site["how_checked"]
    refs = [p["ref"] for p in site["parts"]]
    assert refs == ["S1.P1", "S1.P2"]
    first = site["parts"][0]
    assert first["where"] == "0.00–0.95 km along the route" and first["level"] == "A3"
    assert any("Ouse Washes" in r for r in first["reasons"])
    assert "Ouse Washes SSSI" in first["protected_features"]


# Next steps from rules.yaml, by rule id and by level.
RULE_STEPS = {r["id"]: r.get("next_step") for r in load_rules()["rules"]}
LEVEL_STEPS = load_rules()["level_next_steps"]


def test_facts_sheet_has_next_steps_from_the_rules(sheet):
    assert sheet["next_steps_by_level"] == {"A3": LEVEL_STEPS["A3"]}
    first, second = sheet["sites"][0]["parts"]
    # In rule order, one per kind of reason, the risk-zone step only once.
    assert first["next_steps"] == [RULE_STEPS[1], RULE_STEPS[2], RULE_STEPS[4], RULE_STEPS[5], RULE_STEPS[7]]
    assert second["next_steps"] == [RULE_STEPS[5], RULE_STEPS[6]]


def test_assistant_tools_give_next_steps(project):
    w = assistant.Workspace(project, INTAKE)
    site = w.handle("list_parts", {})[0]
    assert site["next_steps_by_level"]["A3"]["with_mozaic"] == LEVEL_STEPS["A3"]["with_mozaic"]
    assert site["parts"][0]["next_steps"][0] == RULE_STEPS[1]
    part = w.handle("get_part", {"ref": "S1.P2"})
    assert part["level_next_step"] == LEVEL_STEPS["A3"]["next_step"]
    assert part["with_mozaic"] == LEVEL_STEPS["A3"]["with_mozaic"]
    assert part["next_steps"] == [RULE_STEPS[5], RULE_STEPS[6]]


def test_facts_report_uses_the_next_steps(project):
    result = report.facts_report(screen_project(project, INTAKE), INTAKE)
    first = result["findings"][0]
    assert first["next_steps"][0] == LEVEL_STEPS["A3"]["next_step"]
    assert RULE_STEPS[1] in first["next_steps"] and RULE_STEPS[2] in first["next_steps"]
    assert result["next_steps"] == [
        f"A3 (2 parts): {LEVEL_STEPS['A3']['next_step']} With Mozaic: {LEVEL_STEPS['A3']['with_mozaic']}"]


def test_facts_report_gives_every_level_its_step():
    from app.explain import level_next_steps

    def part(ref, level):
        return {"ref": ref, "where": "somewhere", "level": level, "reasons": ["A reason."], "next_steps": []}

    sheet = {
        "project": {"work_type": "New pipeline / sewer"},
        "level_meanings": load_rules()["level_meanings"],
        "next_steps_by_level": level_next_steps(["A1", "A2"], load_rules()),
        "sites": [{"name": "Route", "parts": [part("S1.P1", "A1"), part("S1.P2", "A2"), part("S1.P3", "A1")]}],
    }
    result = report.fallback_report(sheet)
    a2, quiet = result["findings"]
    assert a2["next_steps"] == [LEVEL_STEPS["A2"]["next_step"]]
    assert quiet["parts"] == ["S1.P1", "S1.P3"] and quiet["next_steps"] == [LEVEL_STEPS["A1"]["next_step"]]
    assert [s.split(" (")[0] for s in result["next_steps"]] == ["A2", "A1"]  # highest level first
    assert result["next_steps"][1].startswith("A1 (2 parts): ") and LEVEL_STEPS["A1"]["with_mozaic"] in result["next_steps"][1]


def test_a4_text_may_mention_a3(sheet):
    import copy

    a4 = copy.deepcopy(sheet)
    for p in a4["sites"][0]["parts"]:
        p["level"], p["may_rise_to"] = "A4", None
    draft = good_report(
        headline="Both parts of the route are A4.",
        findings=[report.Finding(parts=["S1.P1", "S1.P2"], title="Survey needed", explanation="A4.",
                                 next_steps=["As A3, plus the in-person survey the client requires."])])
    assert report.check_report(draft, a4) == []


# ---------------------------------------------------------------- report


def good_report(**changes):
    base = dict(
        headline="Both parts of the route are A3.",
        overview="A new sewer crossing the Ouse Washes.",
        findings=[
            report.Finding(parts=["S1.P1"], title="Ouse Washes crossing", explanation="Inside the SSSI, so A3.", next_steps=["Talk to Natural England."]),
            report.Finding(parts=["S1.P2"], title="Risk zone", explanation="Dewatering in a risk zone.", next_steps=[]),
        ],
        next_steps=["Start early talks with Natural England."],
        caveats=["Indicative screening from public data."],
    )
    base.update(changes)
    return report.Report(**base)


def test_check_report_accepts_a_faithful_report(sheet):
    assert report.check_report(good_report(), sheet) == []


@pytest.mark.parametrize("bad, problem", [
    ({"findings": [report.Finding(parts=["S9.P9"], title="X", explanation="", next_steps=[])]}, "don't exist"),
    ({"findings": [report.Finding(parts=["S1.P1", "S1.P2"], title="X", explanation="This is A1.", next_steps=[])]}, "mentions ['A1']"),
    ({"findings": [report.Finding(parts=["S1.P1"], title="X", explanation="", next_steps=[])]}, "aren't covered"),
    ({"headline": "Everything is A1."}, "which no part has"),
])
def test_check_report_catches_problems(sheet, bad, problem):
    assert any(problem in p for p in report.check_report(good_report(**bad), sheet))


def test_generate_report_retries_then_uses_ai(monkeypatch, project):
    drafts = [good_report(headline="It's all A1."), good_report()]
    systems = []

    def fake_generate(system, history, schema):
        systems.append(system)
        return drafts[len(systems) - 1]

    monkeypatch.setattr(llm, "generate", fake_generate)
    result = report.generate_report(screen_project(project, INTAKE), INTAKE)
    assert result["source"] == "ai" and len(systems) == 2
    assert "which no part has" in systems[1]  # the retry was told what was wrong


def test_generate_report_falls_back_to_the_facts(monkeypatch, project):
    monkeypatch.setattr(llm, "generate", lambda system, history, schema: good_report(headline="All A1."))
    result = report.generate_report(screen_project(project, INTAKE), INTAKE)
    assert result["source"] == "facts"
    assert result["headline"].startswith("Overall A3")
    assert [f["parts"] for f in result["findings"]] == [["S1.P1"], ["S1.P2"]]


def test_generate_report_raises_if_the_ai_is_unavailable(monkeypatch, project):
    def unavailable(system, history, schema):
        raise llm.LLMError("ANTHROPIC_API_KEY is not set in .env")

    monkeypatch.setattr(llm, "generate", unavailable)
    with pytest.raises(llm.LLMError):  # the endpoint turns this into the facts report
        report.generate_report(screen_project(project, INTAKE), INTAKE)


def test_generate_report_uses_the_facts_if_the_retry_fails(monkeypatch, project):
    calls = []

    def first_draft_then_outage(system, history, schema):
        calls.append(system)
        if len(calls) == 1:
            return good_report(headline="It's all A1.")  # fails its checks
        raise llm.LLMError("claude request failed")

    monkeypatch.setattr(llm, "generate", first_draft_then_outage)
    result = report.generate_report(screen_project(project, INTAKE), INTAKE)
    assert result["source"] == "facts" and len(calls) == 2


# ------------------------------------------------------- assistant tools


def test_workspace_tools(project):
    w = assistant.Workspace(project, INTAKE)
    parts = w.handle("list_parts", {})[0]["parts"]
    assert [p["ref"] for p in parts] == ["S1.P1", "S1.P2"]
    assert w.handle("get_part", {"ref": "s1.p1"})["level"] == "A3"

    ouse = w.handle("find_feature", {"name": "Ouse Washes"})
    spa = next(f for f in ouse if f["kind"] == "SPA")
    assert spa["parts"] == ["S1.P1"] and spa["route_inside_m"] > 500

    woods = w.handle("nearest", {"site": "S1", "kind": "ancient_woodland"})
    assert woods and woods[0]["distance_m"] > 0

    changed = w.handle("what_if", {"work_type": None, "beyond_site": None, "abstraction": None,
                                   "dewatering": "no", "physical_survey_required": None})
    assert [p["level"] for p in changed["after"][0]["parts"]] == ["A3", "A2"]  # no dewatering eases part 2
    assert "A2" in w.levels_seen

    with pytest.raises(ValueError, match="No part"):
        w.handle("get_part", {"ref": "S2.P1"})


def test_answer_checks_levels_and_returns_refs(monkeypatch, project):
    answers = ["It's all A1 [S1.P1].", "[S1.P1] is A3 because it crosses the Ouse Washes; see also [S7.P1]."]
    calls = []

    def fake_run_tools(system, history, tools, handle):
        calls.append(system)
        return answers[len(calls) - 1]

    monkeypatch.setattr(llm, "run_tools", fake_run_tools)
    result = assistant.answer(project, INTAKE, [{"role": "user", "content": "Which part is the problem?"}])
    assert len(calls) == 2 and "['A1']" in calls[1]  # retried after mentioning a level no tool gave
    assert result == {"answer": answers[1], "refs": ["S1.P1"]}  # unknown refs dropped


# ------------------------------------------------------------- tool loop


def test_run_tools_loops_until_claude_answers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    replies = [
        SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", id="t1", name="list_parts", input={})]),
        SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Part [S1.P1] is A3.")]),
    ]
    sent = []

    class FakeMessages:
        def create(self, **kwargs):
            sent.append(kwargs["messages"])
            return replies[len(sent) - 1]

    class FakeAnthropic:
        def __init__(self, api_key):
            self.beta = SimpleNamespace(messages=FakeMessages())

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    handled = []
    text = llm.run_tools("system", [{"role": "user", "content": "Q?"}], [], lambda n, i: handled.append(n) or {"ok": True})
    assert text == "Part [S1.P1] is A3." and handled == ["list_parts"]
    tool_result = sent[1][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and tool_result["is_error"] is False


def test_run_tools_needs_claude(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(llm.LLMError, match="needs Claude"):
        llm.run_tools("s", [], [], lambda n, i: None)


# ------------------------------------------------------------- endpoints


@pytest.mark.parametrize("cause", ["no_key", "cap"])
def test_report_endpoint_returns_the_facts_report_without_ai(monkeypatch, cause):
    monkeypatch.setattr(main, "_session_counts", main.defaultdict(int))
    if cause == "no_key":
        monkeypatch.setenv("LLM_PROVIDER", "")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    else:
        monkeypatch.setattr(main, "INTAKE_MESSAGE_CAP", 0)
        monkeypatch.setattr(llm, "generate", lambda *a: pytest.fail("the AI shouldn't be called past the cap"))
    body = {"intake": INTAKE, "crs": "EPSG:27700", "sites": ROUTE}
    with TestClient(main.app) as c:
        response = c.post("/report", json=body)
        assert response.status_code == 200
        result = response.json()
        assert result["source"] == "facts"
        assert result["headline"].startswith("Overall A3")
        assert [f["parts"] for f in result["findings"]] == [["S1.P1"], ["S1.P2"]]
    assert sum(main._session_counts.values()) == 0  # nothing counted against the cap


def test_ask_without_claude_is_503_and_not_counted(monkeypatch):
    monkeypatch.setattr(main, "_session_counts", main.defaultdict(int))
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    body = {"intake": INTAKE, "crs": "EPSG:27700", "sites": ROUTE, "history": [{"role": "user", "content": "Q?"}]}
    with TestClient(main.app) as c:
        response = c.post("/ask", json=body)
    assert response.status_code == 503 and "needs Claude" in response.json()["detail"]
    assert sum(main._session_counts.values()) == 0


def test_report_and_ask_endpoints(monkeypatch):
    monkeypatch.setattr(main, "_session_counts", main.defaultdict(int))
    monkeypatch.setattr(report, "generate_report", lambda result, intake: {"headline": "H", "source": "ai"})
    monkeypatch.setattr(assistant, "answer", lambda project, intake, history: {"answer": "A", "refs": []})
    body = {"intake": INTAKE, "crs": "EPSG:27700", "sites": ROUTE}
    with TestClient(main.app) as c:
        assert c.post("/report", json=body).json() == {"headline": "H", "source": "ai"}
        assert c.post("/ask", json=body | {"history": [{"role": "user", "content": "Q?"}]}).json()["answer"] == "A"
        assert c.post("/ask", json=body | {"history": []}).status_code == 422
        assert c.post("/report", json=body | {"intake": INTAKE | {"dewatering": "maybe"}}).status_code == 422
