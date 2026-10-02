"""Offline tests for the Claude path in app/llm.py, with a fake Anthropic client."""

from types import SimpleNamespace

import anthropic
import pytest

from app import llm
from app.intake import intake_schema

ANSWERS = {
    "work_type": "storm_overflow", "beyond_site": None, "abstraction": None, "dewatering": None,
    "physical_survey_required": None, "next_question": "Is any work outside the site boundary?", "summary": None,
}
HISTORY = [{"role": "user", "content": "Upgrading storm overflows"}]


@pytest.fixture(autouse=True)
def claude_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")


def fake_client(monkeypatch, *responses):
    """Make anthropic.Anthropic return canned responses; record each call."""
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            response = responses[len(calls) - 1]
            if isinstance(response, Exception):
                raise response
            return response

    class FakeAnthropic:
        def __init__(self, api_key):
            assert api_key == "sk-test-secret"
            self.beta = SimpleNamespace(messages=FakeMessages())

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return calls


def tool_reply(tool_input, name="submit_intake_turn", stop_reason="tool_use"):
    blocks = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="tool_use", name=name, input=tool_input)]
    return SimpleNamespace(stop_reason=stop_reason, content=blocks)


def text_reply(text="Sure, is any work outside the site?"):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


def test_default_provider_is_claude_sonnet(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert (llm.provider(), llm.model()) == ("claude", "claude-sonnet-5-5")


def test_tool_definition_is_strict_anthropic_format():
    tool = llm.tool_definition(intake_schema())
    assert tool["name"] == "submit_intake_turn"
    assert tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_claude_tool_call_is_parsed(monkeypatch):
    calls = fake_client(monkeypatch, tool_reply(ANSWERS))
    turn = llm.generate("system prompt", HISTORY, intake_schema())
    assert turn.work_type == "storm_overflow" and turn.next_question.startswith("Is any work")

    request = calls[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["tool_choice"] == {"type": "auto"}  # forced tool choice is a 400 on Sonnet 5.5
    assert request["tools"][0]["name"] == "submit_intake_turn"
    assert request["messages"] == HISTORY
    assert "submit_intake_turn" in request["system"]


def test_text_reply_is_retried_once(monkeypatch):
    calls = fake_client(monkeypatch, text_reply(), tool_reply(ANSWERS))
    assert llm.generate("system", HISTORY, intake_schema()).work_type == "storm_overflow"
    assert len(calls) == 2


def test_no_tool_call_after_retry_is_an_error(monkeypatch):
    fake_client(monkeypatch, text_reply(), text_reply())
    with pytest.raises(llm.LLMError, match="didn't call"):
        llm.generate("system", HISTORY, intake_schema())


def test_refusal_is_an_error(monkeypatch):
    fake_client(monkeypatch, SimpleNamespace(stop_reason="refusal", content=[]))
    with pytest.raises(llm.LLMError, match="declined"):
        llm.generate("system", HISTORY, intake_schema())


def test_invalid_tool_input_is_an_error(monkeypatch):
    fake_client(monkeypatch, tool_reply(ANSWERS | {"work_type": "reservoir"}))
    with pytest.raises(llm.LLMError, match="didn't match"):
        llm.generate("system", HISTORY, intake_schema())


def test_errors_never_contain_the_key(monkeypatch):
    fake_client(monkeypatch, RuntimeError("bad request with key sk-test-secret"))
    with pytest.raises(llm.LLMError) as caught:
        llm.generate("system", HISTORY, intake_schema())
    assert "sk-test-secret" not in str(caught.value) and "[REDACTED]" in str(caught.value)


@pytest.mark.parametrize("value", ["", "paste-here"])
def test_missing_or_placeholder_key_is_an_error(monkeypatch, value):
    monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY is not set"):
        llm.generate("system", HISTORY, intake_schema())
