"""Check that the Gemini API key in .env works.

Sends a one-line prompt to Gemini and prints the reply. The key itself is
never printed or logged, including in error messages.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai

ROOT = Path(__file__).resolve().parent.parent
MODEL = "gemini-flash-latest"
PROMPT = "Reply with the word OK"
PLACEHOLDER = "paste-here"


def main() -> int:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or key == PLACEHOLDER:
        print("GEMINI_API_KEY is not set in .env (still missing or the placeholder).")
        return 1

    client = genai.Client(api_key=key)
    try:
        response = client.models.generate_content(model=MODEL, contents=PROMPT)
    except Exception as exc:
        # Report the error type and message with any copy of the key removed.
        message = str(exc).replace(key, "[REDACTED]")
        print(f"Gemini request failed ({type(exc).__name__}): {message}")
        return 1

    print(f"{MODEL} replied: {response.text.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
