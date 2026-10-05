"""Offline tests for app/intake.py. The LLM is replaced by a fake."""

import pytest
from pydantic import ValidationError

from app import intake


def fake_llm(monkeypatch, *replies):
    """Make llm.generate return the given field dicts, one per call."""
    calls = []

    def generate(system, history, schema):
        calls.append(system)
        return schema(**replies[len(calls) - 1])

    monkeypatch.setattr(intake.llm, "generate", generate)
    return calls


def reply(**answers):
    base = dict(work_type=None, beyond_site=None, abstraction=None, dewatering=None,
                physical_survey_required=None, asking_about=None, answer_example=None, location_method=None, postcodes=[], postcode_districts=[],
                next_question=None, summary=None)
    base.update(answers)
    return base


ALL_KNOWN = dict(work_type="storm_overflow", beyond_site="no", abstraction="no",
                 dewatering="no", physical_survey_required="yes")
HISTORY = [{"role": "user", "content": "Upgrading storm overflows"}]


def test_asks_question_while_answers_missing(monkeypatch):
    fake_llm(monkeypatch, reply(work_type="storm_overflow", next_question="Is any work outside the site?"))
    turn = intake.next_turn(HISTORY)
    assert turn["type"] == "question" and turn["text"] == "Is any work outside the site?"
    assert turn["answers"]["work_type"] == "storm_overflow"
    assert turn["postcodes"] == [] and turn["districts"] == []


def test_confirms_when_all_answers_known_and_sites_placed(monkeypatch):
    fake_llm(monkeypatch, reply(**ALL_KNOWN, summary="Storm overflow work at 3 sites. Right?"))
    turn = intake.next_turn(HISTORY, sites_placed=3)
    assert turn["type"] == "confirm"
    assert turn["intake"] == ALL_KNOWN
    assert turn["summary"] == "Storm overflow work at 3 sites. Right?"


def test_never_confirms_without_a_site_on_the_map(monkeypatch):
    fake_llm(monkeypatch, reply(**ALL_KNOWN, summary="All done. Right?"))
    turn = intake.next_turn(HISTORY, sites_placed=0)
    assert turn == {"type": "question", "text": intake.LOCATION_QUESTION, "example": intake.EXAMPLES["location"],
                    "answers": ALL_KNOWN, "location_method": None, "postcodes": [], "districts": []}


def test_location_method_is_passed_on(monkeypatch):
    history = [{"role": "user", "content": "I'll draw it on the map"}]
    fake_llm(monkeypatch, reply(**ALL_KNOWN, location_method="draw", next_question="Please draw the route."))
    turn = intake.next_turn(history)
    assert turn["location_method"] == "draw" and turn["text"] == "Please draw the route."


def test_passes_on_postcodes_the_user_typed(monkeypatch):
    history = [{"role": "user", "content": "It's at cb7 4da, the works by the river"}]
    fake_llm(monkeypatch, reply(postcodes=["CB7 4DA"], next_question="Is any work outside the site?"))
    turn = intake.next_turn(history)
    assert turn["postcodes"] == ["CB7 4DA"]


def test_drops_postcodes_the_user_never_typed(monkeypatch):
    # The AI must only copy postcodes, never invent them.
    history = [{"role": "user", "content": "It's near Ely"}]
    fake_llm(monkeypatch, reply(postcodes=["CB7 4DA", "not a postcode"], postcode_districts=["CB7"],
                                next_question="Could you give me a postcode?"))
    turn = intake.next_turn(history)
    assert turn["postcodes"] == [] and turn["districts"] == []


def test_districts_are_passed_on_for_zooming(monkeypatch):
    history = [{"role": "user", "content": "Somewhere in CB7"}]
    fake_llm(monkeypatch, reply(postcode_districts=["cb7"], next_question="Please place the site on the map."))
    assert intake.next_turn(history)["districts"] == ["CB7"]


def test_pin_prompt_when_postcode_given_and_model_asks_nothing(monkeypatch):
    history = [{"role": "user", "content": "PE1 1AA"}]
    fake_llm(monkeypatch, reply(**ALL_KNOWN, postcodes=["PE1 1AA"]))
    turn = intake.next_turn(history, sites_placed=0)
    assert turn["type"] == "question" and turn["text"] == intake.CONFIRM_PINS


def test_site_count_is_given_to_the_model(monkeypatch):
    calls = fake_llm(monkeypatch, reply(next_question="What type of work is it?"))
    intake.next_turn(HISTORY, sites_placed=2)
    assert "the user has 2 site(s) on the map" in calls[0]


def test_falls_back_to_fixed_question_if_model_gives_none(monkeypatch):
    fake_llm(monkeypatch, reply(work_type="storm_overflow"))
    turn = intake.next_turn(HISTORY)
    assert turn["text"] == intake.QUESTIONS["beyond_site"]


def test_level_mention_is_retried_then_replaced(monkeypatch):
    calls = fake_llm(
        monkeypatch,
        reply(next_question="This looks like A3. What type of work is it?"),
        reply(next_question="Probably a low assurance level. What work is it?"),
    )
    turn = intake.next_turn(HISTORY)
    assert len(calls) == 2 and "Do not mention levels" in calls[1]
    assert turn["type"] == "question"
    assert not intake.LEVEL_MENTION.search(turn["text"])


def test_schema_has_no_coordinate_fields_and_rejects_bad_values():
    schema = intake.intake_schema()
    assert not {"lat", "lon", "latitude", "longitude", "easting", "northing", "coordinates"} & set(schema.model_fields)
    with pytest.raises(ValidationError):
        schema(**reply(work_type="reservoir"))
    with pytest.raises(ValidationError):
        schema(**reply(dewatering="unknown"))


def test_example_answer_follows_the_question(monkeypatch):
    fake_llm(monkeypatch, reply(work_type="new_pipeline_sewer", asking_about="dewatering",
                                answer_example="Probably, near the river", next_question="Will the trench need pumping out?"))
    assert intake.next_turn(HISTORY)["example"] == "e.g. Probably, near the river"


def test_default_example_when_the_ai_gives_none_or_mentions_a_level(monkeypatch):
    fake_llm(monkeypatch, reply(asking_about="beyond_site", next_question="Will it cross fields?"))
    assert intake.next_turn(HISTORY)["example"] == intake.EXAMPLES["beyond_site"]
    fake_llm(monkeypatch, reply(asking_about="dewatering", answer_example="Yes, so it's A3", next_question="Pumping?"))
    assert intake.next_turn(HISTORY)["example"] == intake.EXAMPLES["dewatering"]


def test_survey_question_gets_the_a4_note_and_isnt_treated_as_a_level_leak(monkeypatch):
    calls = fake_llm(monkeypatch, reply(**ALL_KNOWN | {"physical_survey_required": None}, asking_about="physical_survey_required",
                                        next_question="Does your company require an in-person site survey?"))
    turn = intake.next_turn(HISTORY)
    assert turn["text"] == f"Does your company require an in-person site survey? {intake.SURVEY_NOTE}"
    assert "A4" in turn["text"] and len(calls) == 1  # no retry for the app's own note
