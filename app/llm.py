"""A small wrapper so the rest of the app doesn't depend on one LLM provider.

The provider comes from LLM_PROVIDER in .env:
  claude (default)  model claude-sonnet-5-5,   key ANTHROPIC_API_KEY
  gemini            model gemini-flash-latest, key GEMINI_API_KEY
LLM_MODEL overrides the model for either provider.

Keys are read on the server from .env and never sent to the browser,
printed or logged; error messages have them removed.
"""

import os
import re
import time
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEFAULT_PROVIDER = "claude"
DEFAULT_MODELS = {"claude": "claude-sonnet-5-5", "gemini": "gemini-flash-latest"}
KEY_VARS = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
PLACEHOLDER_KEY = "paste-here"
GEMINI_ATTEMPTS = 4  # retries for temporary errors such as 503 "high demand"
CLAUDE_TOOL_ATTEMPTS = 2  # tool_choice can't force a call on Sonnet 5.5, so retry once if it's skipped

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """A provider call failed. The message never contains an API key."""


def provider() -> str:
    name = os.environ.get("LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if name not in DEFAULT_MODELS:
        raise LLMError(f"LLM_PROVIDER must be one of {sorted(DEFAULT_MODELS)}, got {name!r}")
    return name


def model() -> str:
    return os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODELS[provider()]


def generate(system: str, history: list[dict], schema: type[T]) -> T:
    """Continue a chat and return the reply parsed into `schema`.

    history is a list of {"role": "user" | "assistant", "content": str}.
    """
    name = provider()
    key = os.environ.get(KEY_VARS[name], "").strip()
    if not key or key == PLACEHOLDER_KEY:
        raise LLMError(f"{KEY_VARS[name]} is not set in .env")
    call = _gemini if name == "gemini" else _claude
    for attempt in range(1, GEMINI_ATTEMPTS + 1):
        try:
            return call(key, system, history, schema)
        except LLMError:
            raise
        except Exception as exc:
            # The Anthropic SDK retries temporary errors itself; Gemini doesn't.
            if name == "gemini" and attempt < GEMINI_ATTEMPTS and _is_temporary(exc):
                time.sleep(2 ** attempt)
                continue
            message = str(exc).replace(key, "[REDACTED]")
            raise LLMError(f"{name} request failed ({type(exc).__name__}): {message}") from None


def _is_temporary(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    return code == 429 or (isinstance(code, int) and code >= 500)


# ------------------------------------------------------------------ Claude


def tool_definition(schema: type[BaseModel]) -> dict:
    """An Anthropic tool whose input is `schema`.

    strict: true makes the API guarantee the tool input matches the schema,
    so enum fields can only hold allowed values.
    """
    return {
        "name": _tool_name(schema),
        "description": (schema.__doc__ or "").strip() or f"Submit the {schema.__name__} for this turn.",
        "input_schema": schema.model_json_schema(),
        "strict": True,
    }


def _tool_name(schema: type[BaseModel]) -> str:
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", schema.__name__).lower()  # IntakeTurn -> intake_turn
    return f"submit_{snake}"


def _claude(key: str, system: str, history: list[dict], schema: type[T]) -> T:
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    tool = tool_definition(schema)
    instruction = f"\n\nAlways reply by calling the {tool['name']} tool exactly once. Don't reply with plain text."
    messages = [{"role": m["role"], "content": m["content"]} for m in history]

    for _ in range(CLAUDE_TOOL_ATTEMPTS):
        # Server-side fallback: if the model declines, the API retries on a
        # fallback model within the same call.
        response = client.beta.messages.create(
            model=model(),
            max_tokens=16000,
            system=system + instruction,
            messages=messages,
            tools=[tool],
            tool_choice={"type": "auto"},  # Sonnet 5.5 rejects forced tool choice
            output_config={"effort": "low"},  # short chat turns
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMError("claude declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError("claude ran out of output tokens")
        call = next((b for b in response.content if b.type == "tool_use" and b.name == tool["name"]), None)
        if call is None:
            continue  # answered in text instead of calling the tool; ask again
        try:
            return schema.model_validate(call.input)
        except ValidationError as exc:
            raise LLMError(f"claude tool input didn't match the schema: {exc.error_count()} error(s)") from None
    raise LLMError(f"claude didn't call the {tool['name']} tool")


# ------------------------------------------------------------------ Gemini


def _gemini(key: str, system: str, history: list[dict], schema: type[T]) -> T:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    contents = [
        types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])])
        for m in history
    ]
    response = client.models.generate_content(
        model=model(),
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    if isinstance(response.parsed, schema):
        return response.parsed
    if not response.text:
        raise LLMError("gemini returned no content")
    return schema.model_validate_json(response.text)
