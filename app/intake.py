"""Collect the screening intake through a short chat.

next_turn(history, sites_placed) looks at the chat so far and returns either
  {"type": "question", "text": ..., "answers": {...}, "postcodes": [...], "districts": [...]}
  {"type": "confirm", "summary": ..., "intake": {...}, "postcodes": [...], "districts": [...]}

The intake has the five answers in DECISIONS.md ("Intake design"). The chat
also asks where the work is: the user gives postcodes, or places sites on the
map. The AI only copies postcodes the user typed; the browser looks them up
(/geocode) and shows pins to confirm. The AI never produces coordinates.

Structured output limits each answer to the values allowed by app/rules.yaml,
and the result is checked with scoring.validate_intake before it is returned.
The AI never suggests an assurance level; that comes only from scoring.
"""

import re
from functools import lru_cache
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, create_model

from app import llm
from app.scoring import load_rules, validate_intake

YES_NO = Literal["yes", "no"]

QUESTIONS = {
    "beyond_site": "Will any of the work take place outside the existing site boundary?",
    "abstraction": "Will the work involve new or increased water abstraction (taking water from a river, lake or groundwater)?",
    "dewatering": "Will you need to pump out groundwater to keep excavations dry (dewatering)?",
    "physical_survey_required": "Will someone need to visit the sites to survey them in person?",
}
LOCATION_QUESTION = (
    "Where is the work? Give me a postcode for each site, or place the sites on the map with the drawing "
    "tools. For the most accurate results, upload coordinates."
)
CONFIRM_PINS = "I've put a pin at that postcode. Please check it on the map, drag it to the exact spot if needed, and confirm it."

FULL_POSTCODE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$")
POSTCODE_DISTRICT = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")

# Catches replies that hint at the outcome of screening.
LEVEL_MENTION = re.compile(r"\bA[1-4]\b|assurance level|risk level", re.IGNORECASE)


@lru_cache(maxsize=None)
def intake_schema() -> type[BaseModel]:
    rules = load_rules()
    work_types = tuple(rules["work_types"])
    fields = {
        "work_type": (Optional[Literal[work_types]], Field(description="Supported work type, or null if not yet agreed")),
        **{
            name: (Optional[YES_NO], Field(description=f"{QUESTIONS[name]} null if not yet answered"))
            for name in rules["intake_fields"]
        },
        "postcodes": (list[str], Field(description="Full UK postcodes the user typed in their latest message, copied exactly; empty if none")),
        "postcode_districts": (list[str], Field(description="Postcode districts such as CB7 the user typed in their latest message, when they gave no full postcode; empty if none")),
        "next_question": (Optional[str], Field(description="The one question to ask next, or null when everything is known")),
        "summary": (Optional[str], Field(description="Plain-English summary for the user to confirm, only when everything is known")),
    }
    return create_model(
        "IntakeTurn",
        __config__=ConfigDict(extra="forbid"),  # additionalProperties: false, needed for strict tools
        __doc__="Record the intake answers known so far, any postcodes the user typed, and the next question or the summary to confirm.",
        **fields,
    )


@lru_cache(maxsize=None)
def system_prompt() -> str:
    rules = load_rules()
    work_types = "\n".join(
        f"- {key}: {spec['label']}" + (f" ({spec['note']})" if spec.get("note") else "")
        for key, spec in rules["work_types"].items()
    )
    questions = "\n".join(f"- {name}: {text}" for name, text in QUESTIONS.items())
    return f"""You help water company staff describe planned work so it can be screened for effects on protected nature sites. You collect five answers and the location of the work. One set of answers applies to every site being screened.

1. work_type - one of these supported types (use the key):
{work_types}
2. Four yes/no questions:
{questions}
3. Where the work is. The user either gives a postcode for each site, or places the sites on the map themselves (drawing a point, a line for a pipeline, or an outline), or uploads coordinates. You are told how many sites are on the map so far.

How to run the conversation:
- Fill in an answer only when the user has clearly given it, anywhere in the chat. Otherwise leave it null. Never assume "no" because something wasn't mentioned.
- If the user describes work that doesn't match a supported type, leave work_type null. Tell them plainly that this type of work isn't supported yet, name the closest supported type, and ask whether to use it. Set work_type only once they agree or name a supported type.
- Every answer must be yes or no. If the user is unsure or says they don't know, explain briefly why it matters and ask again.
- For the location, ask for a postcode, or ask the user to place the site on the map. If they give a full postcode, copy it into postcodes; a pin will appear for them to check and confirm. If they only give a postcode district (like CB7), copy it into postcode_districts; the map will zoom there, and you should ask them to place the site on the map or give a full postcode. If the location is vague (like "near Ely"), ask for a postcode or ask them to place the site on the map.
- Never give coordinates, grid references or directions to a place, and never guess a postcode. Only copy postcodes the user actually typed.
- Ask about one missing item at a time, in plain English, in one or two short sentences. Don't use the internal keys.
- When all five answers are known and at least one site is on the map, set next_question to null and write a short summary listing the five answers and the number of sites, ending by asking the user to confirm or correct them.
- If the user corrects something after the summary, update that answer.
- Never mention, suggest or predict an assurance level, a risk level, A1 to A4, or whether anyone needs to be consulted. Screening happens only after the user confirms."""


def next_turn(history: list[dict], sites_placed: int = 0) -> dict:
    """Decide the next assistant turn from the chat so far.

    history is a list of {"role": "user" | "assistant", "content": str},
    starting with the user's description of the work. sites_placed is how
    many sites the user has on the map.
    """
    schema = intake_schema()
    system = system_prompt() + f"\n\nRight now the user has {sites_placed} site(s) on the map."
    reply = llm.generate(system, history, schema)
    if _mentions_level(reply):
        reminder = "\n\nYour last draft mentioned an assurance or risk level. Do not mention levels at all."
        reply = llm.generate(system + reminder, history, schema)

    rules = load_rules()
    names = ["work_type", *rules["intake_fields"]]
    answers = {name: getattr(reply, name) for name in names}
    missing = [name for name in names if answers[name] is None]
    typed = _user_text(history)
    postcodes = _typed_codes(reply.postcodes, FULL_POSTCODE, typed)
    districts = _typed_codes(reply.postcode_districts, POSTCODE_DISTRICT, typed)
    location = {"postcodes": postcodes, "districts": districts}

    if not missing and sites_placed > 0:
        validate_intake(answers, rules)  # raises if anything is out of range
        summary = reply.summary if reply.summary and not _mentions_level(reply) else _summary(answers, rules, sites_placed)
        return {"type": "confirm", "summary": summary, "intake": answers, **location}

    text = reply.next_question
    if not text or LEVEL_MENTION.search(text):
        if postcodes:
            text = CONFIRM_PINS
        elif missing:
            text = _fallback_question(missing[0], rules)
        else:
            text = LOCATION_QUESTION
    return {"type": "question", "text": text, "answers": answers, **location}


def _mentions_level(reply: BaseModel) -> bool:
    return any(LEVEL_MENTION.search(t) for t in (reply.next_question, reply.summary) if t)


def _user_text(history: list[dict]) -> str:
    """Everything the user typed, upper-case with spaces removed."""
    return re.sub(r"\s+", "", " ".join(m["content"] for m in history if m["role"] == "user").upper())


def _typed_codes(codes: list[str], pattern: re.Pattern, typed: str) -> list[str]:
    """Keep codes that are well formed and that the user actually typed."""
    kept = []
    for code in codes:
        clean = re.sub(r"\s+", " ", code.strip().upper())
        if pattern.match(clean) and clean.replace(" ", "") in typed and clean not in kept:
            kept.append(clean)
    return kept


def _fallback_question(name: str, rules: dict) -> str:
    if name == "work_type":
        options = "; ".join(spec["label"] for spec in rules["work_types"].values())
        return f"What type of work is this? The supported types are: {options}."
    return QUESTIONS[name]


def _summary(answers: dict, rules: dict, sites_placed: int) -> str:
    lines = [f"- Work type: {rules['work_types'][answers['work_type']]['label']}"]
    lines += [f"- {QUESTIONS[name]} {answers[name].capitalize()}" for name in rules["intake_fields"]]
    lines.append(f"- Sites on the map: {sites_placed}")
    return "Here's what I have for all sites:\n" + "\n".join(lines) + "\nIs that right?"
