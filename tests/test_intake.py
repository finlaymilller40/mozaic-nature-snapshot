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
                physical_survey_required=None, next_question=None, summary=None)
    base.update(answers)
    return base


ALL_KNOWN = dict(work_type="storm_overflow", beyond_site="no", abstraction="no",
                 dewatering="no", physical_survey_required="yes")
HISTORY = [{"role": "user", "content": "Upgrading storm overflows"}]


def test_asks_question_while_answers_missing(monkeypatch):
    fake_llm(monkeypatch, reply(work_type="storm_overflow", next_question="Is any work outside the site?"))
    assert intake.next_turn(HISTORY) == {"type": "question", "text": "Is any work outside the site?"}


def test_confirms_when_all_answers_known(monkeypatch):
    fake_llm(monkeypatch, reply(**ALL_KNOWN, summary="Storm overflow work, inside sites. Right?"))
    turn = intake.next_turn(HISTORY)
    assert turn["type"] == "confirm"
    assert turn["intake"] == ALL_KNOWN
    assert turn["summary"] == "Storm overflow work, inside sites. Right?"


def test_falls_back_to_fixed_question_if_model_gives_none(monkeypatch):
    fake_llm(monkeypatch, reply(work_type="storm_overflow"))
    turn = intake.next_turn(HISTORY)
    assert turn == {"type": "question", "text": intake.QUESTIONS["beyond_site"]}


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


def test_schema_rejects_values_outside_rules():
    schema = intake.intake_schema()
    with pytest.raises(ValidationError):
        schema(**reply(work_type="reservoir"))
    with pytest.raises(ValidationError):
        schema(**reply(dewatering="unknown"))
