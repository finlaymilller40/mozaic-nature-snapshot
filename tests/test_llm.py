"""Offline tests for the Claude path in app/llm.py, with a fake Anthropic client."""

from types import SimpleNamespace

import anthropic
import pytest

from app import llm
from app.intake import intake_schema

ANSWERS = {
    "work_type": "storm_overflow", "beyond_site": None, "abstraction": None, "dewatering": None,
    "physical_survey_required": None, "asking_about": "beyond_site", "answer_example": "Yes, it crosses fields", "location_method": None, "postcodes": [], "postcode_districts": [],
    "next_question": "Is any work outside the site boundary?", "summary": None,
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


# ------------------------------------------------- settings and availability


def test_empty_settings_count_as_not_set(monkeypatch):
    # A copy of .env.example has every setting present but empty.
    for name in ("LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "INTAKE_MESSAGE_CAP"):
        monkeypatch.setenv(name, "")
    assert (llm.provider(), llm.model()) == ("claude", "claude-sonnet-5-5")
    assert llm.setting("INTAKE_MESSAGE_CAP", "20") == "20"
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY is not set"):
        llm.generate("system", HISTORY, intake_schema())


@pytest.mark.parametrize("env, expected", [
    ({"LLM_PROVIDER": "", "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": ""}, (False, False, False)),
    ({"LLM_PROVIDER": "claude", "ANTHROPIC_API_KEY": "sk-test", "GEMINI_API_KEY": ""}, (True, True, True)),
    ({"LLM_PROVIDER": "claude", "ANTHROPIC_API_KEY": "paste-here", "GEMINI_API_KEY": ""}, (False, False, False)),
    ({"LLM_PROVIDER": "gemini", "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "AI-test"}, (True, True, False)),
    ({"LLM_PROVIDER": "gemini", "ANTHROPIC_API_KEY": "sk-test", "GEMINI_API_KEY": ""}, (False, False, False)),
    ({"LLM_PROVIDER": "openai", "ANTHROPIC_API_KEY": "sk-test", "GEMINI_API_KEY": ""}, (False, False, False)),
])
def test_available_needs_a_key_for_the_configured_provider(monkeypatch, env, expected):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    ai = llm.available()
    assert (ai["chat"], ai["report"], ai["assistant"]) == expected


def test_app_starts_with_env_example_values():
    """Importing the app with every setting empty must not fail (it used to on INTAKE_MESSAGE_CAP)."""
    import os
    import subprocess
    import sys

    from dotenv import dotenv_values

    empty = {name: "" for name in dotenv_values(llm.ROOT / ".env.example")}
    assert empty and "INTAKE_MESSAGE_CAP" in empty
    run = subprocess.run(
        [sys.executable, "-c", "import app.main as m; print(m.INTAKE_MESSAGE_CAP, m.llm.available())"],
        cwd=llm.ROOT, env=os.environ | empty, capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stderr[-500:]
    assert run.stdout.split()[0] == "20"
    assert "'chat': False" in run.stdout and "'assistant': False" in run.stdout
