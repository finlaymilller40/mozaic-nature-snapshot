"""A small wrapper so the rest of the app doesn't depend on one LLM provider.

The provider comes from LLM_PROVIDER in .env:
  gemini (default)  model gemini-flash-latest, key GEMINI_API_KEY
  claude            model claude-opus-5-5,     key ANTHROPIC_API_KEY
LLM_MODEL overrides the model for either provider.

API keys are never printed or logged; error messages have them removed.
"""

import os
import time
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEFAULT_MODELS = {"gemini": "gemini-flash-latest", "claude": "claude-opus-5-5"}
KEY_VARS = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY"}
GEMINI_ATTEMPTS = 4  # retries for temporary errors such as 503 "high demand"

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """A provider call failed. The message never contains an API key."""


def provider() -> str:
    name = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
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
    if not key:
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


def _claude(key: str, system: str, history: list[dict], schema: type[T]) -> T:
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    # Server-side fallback: if the model declines, the API retries on a
    # fallback model within the same call.
    response = client.beta.messages.parse(
        model=model(),
        max_tokens=16000,
        system=system,
        messages=[{"role": m["role"], "content": m["content"]} for m in history],
        output_format=schema,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise LLMError("claude declined the request")
    if response.parsed_output is None:
        raise LLMError(f"claude returned no structured output (stop_reason={response.stop_reason})")
    return response.parsed_output
