"""Collect the screening intake through a short chat.

next_turn(history, sites_placed) looks at the chat so far and returns either
  {"type": "question", "text": ..., "answers": {...}, "location_method": ..., "postcodes": [...], "districts": [...]}
  {"type": "confirm", "summary": ..., "intake": {...}, "location_method": ..., "postcodes": [...], "districts": [...]}

The intake has the five answers in DECISIONS.md ("Intake design"). Once they
are known, the chat asks how the user wants to give the location: uploading
coordinates, postcodes, or drawing on a map. The AI only copies postcodes the
user typed; the browser looks them up (/geocode) and shows pins to confirm.
The AI never produces coordinates.

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
    "beyond_site": ("Will any of the work happen outside land you already operate, such as across fields, roads "
                    "or other people's land? Work inside an existing treatment works or pumping station counts as no."),
    "abstraction": ("Will the work take new or extra water from a river, lake or borehole? "
                    "Normal sewage and rainwater flows don't count."),
    "dewatering": "Will you need to pump groundwater out of trenches or pits to keep them dry while you dig?",
    "physical_survey_required": ("Does your company, or this project, require someone to survey the sites in person, "
                                 "for example an ecologist's site visit, rather than working from maps and records?"),
}
# Added by the code (not the AI) whenever the survey question is asked. It's the
# one place levels are mentioned before the results: it explains the A4 step.
SURVEY_NOTE = ("We ask because a required site survey is our A4 step: any part of the work that needs "
               "specialist assessment moves up to A4, specialist assessment plus a site survey.")

# What the AI can say it's asking about, and a default example answer for each,
# shown as the placeholder in the message box.
TOPICS = ("work_type", "beyond_site", "abstraction", "dewatering", "physical_survey_required", "location", "other")
EXAMPLES = {
    "work_type": "e.g. A new sewer between two villages",
    "beyond_site": "e.g. Yes, it crosses a couple of fields",
    "abstraction": "e.g. No, it only carries sewage",
    "dewatering": "e.g. Probably, near the river crossing",
    "physical_survey_required": "e.g. No, we'll work from records",
    "location": "e.g. CB7 4DL, or \"I'll draw it on a map\"",
    "other": "Type your answer…",
}
EXAMPLE_MAX_CHARS = 90
LOCATION_QUESTION = (
    "Last question: where is the work? You can upload or paste coordinates (most accurate), "
    "give a postcode, or draw it on a map."
)
LOCATION_METHODS = ("coordinates", "postcode", "draw")
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
        "asking_about": (Optional[Literal[TOPICS]], Field(description="What next_question is mainly asking about; null when there's no question")),
        "answer_example": (Optional[str], Field(description="A short example of how the user might answer next_question, written as the user and specific to their project, e.g. 'Yes, it crosses two fields'; null if not useful")),
        "location_method": (Optional[Literal[LOCATION_METHODS]], Field(description="How the user wants to give the location, once they say: coordinates, postcode or draw; null until they choose")),
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
    return f"""You're a friendly, knowledgeable assistant helping water company staff describe planned work, so it can be checked against protected nature sites. You're talking to engineers and project managers, not ecologists. Your job is to get a clear picture of their project through a natural conversation, and from that fill in five answers plus where the work is. One set of answers applies to every site in the project.

What you need to end up with:
1. work_type, one of these supported types (use the key):
{work_types}
2. Four yes/no answers. This is what each one means:
{questions}
3. Where the work is. The user can upload or paste coordinates (the most accurate), give a postcode, or draw it on a map. Set location_method when they choose one; the app then shows the right tool. You're told how many sites have been added so far.

How to talk to them:
- Start from what they've told you. Work out what you can from their description and the kind of project it is, and ask about what's still unclear. Don't run through a fixed list.
- Ask about their project in their terms. Instead of a generic question, ask a specific one that gets the answer: for a sewer, "Will the trench be deep enough that you'd need to pump out groundwater while you dig?"; for a storm tank, "Is the tank going inside the existing works fence, or on new land?"
- If the description is vague ("some works near Ely"), ask a short open question about what the work actually involves before the yes/no questions.
- When their description makes an answer likely, say what you've understood and check it, rather than asking cold: "It sounds like the route runs across fields between the two towns, so outside land you already operate. Is that right?" Only fill in the answer once they confirm or say it themselves.
- You can check two closely related things in one message if it reads naturally, but keep each message to one or two short sentences.
- If they ask what you mean, or why it matters, explain briefly in plain words and then ask again.
- If they're unsure, help them think it through (what usually happens on this kind of job) and ask for their best answer. Every answer has to end up as yes or no.
- Use plain English. Don't use jargon like "site boundary", "abstraction" or "IRZ" without explaining it, and never show the internal keys.
- With every question, set asking_about to what it's mainly about, and give answer_example: a short, realistic answer the user might type, specific to their project (for a pipe near a river: "Probably, near the river crossing"). It's shown as a hint in their message box.
- For the site survey, ask whether their company or the project requires someone to survey the sites in person, such as an ecologist's site visit. The app adds a note explaining why; don't explain it yourself.
- If the work doesn't match a supported type, say plainly that it isn't supported yet, name the closest supported type, and ask whether to use it. Set work_type only once they agree or name a supported type.

The rules that always apply:
- Fill in an answer only when the user has clearly given or confirmed it, anywhere in the chat. Otherwise leave it null. Never assume "no" because something wasn't mentioned.
- Ask about the location only once the five answers are known, unless they offer it earlier. If they give a full postcode, copy it into postcodes and set location_method to postcode; a pin appears on a map for them to check. If they give only a postcode district (like CB7), copy it into postcode_districts and suggest a full postcode or drawing it. If the location is vague, ask for a postcode or suggest drawing it. Once sites have been added, don't ask for the location again.
- Never give coordinates, grid references or directions to a place, and never guess a postcode. Only copy postcodes the user actually typed.
- When all five answers are known and at least one site has been added, set next_question to null and write a short summary of the project in plain words (the five answers and the number of sites), ending by asking them to confirm or correct it.
- If they correct something after the summary, update that answer.
- Never mention, suggest or predict an assurance level, a risk level, A1 to A4, or whether anyone needs to be consulted. The check happens only after they confirm."""


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
    topic = reply.asking_about or "other"
    missing = [name for name in names if answers[name] is None]
    typed = _user_text(history)
    postcodes = _typed_codes(reply.postcodes, FULL_POSTCODE, typed)
    districts = _typed_codes(reply.postcode_districts, POSTCODE_DISTRICT, typed)
    location = {"location_method": reply.location_method, "postcodes": postcodes, "districts": districts}

    if not missing and sites_placed > 0:
        validate_intake(answers, rules)  # raises if anything is out of range
        summary = reply.summary if reply.summary and not _mentions_level(reply) else _summary(answers, rules, sites_placed)
        return {"type": "confirm", "summary": summary, "intake": answers, **location}

    text = reply.next_question
    if not text or LEVEL_MENTION.search(_without_note(text)):
        if postcodes:
            text, topic = CONFIRM_PINS, "location"
        elif missing:
            text, topic = _fallback_question(missing[0], rules), missing[0]
        else:
            text, topic = LOCATION_QUESTION, "location"
    if topic == "physical_survey_required" and SURVEY_NOTE not in text:
        text = f"{text} {SURVEY_NOTE}"
    return {"type": "question", "text": text, "example": _example(reply.answer_example, topic),
            "answers": answers, **location}


def _without_note(text: str) -> str:
    return text.replace(SURVEY_NOTE, "")


def _mentions_level(reply: BaseModel) -> bool:
    return any(LEVEL_MENTION.search(_without_note(t)) for t in (reply.next_question, reply.summary) if t)


def _example(suggested: str | None, topic: str) -> str:
    """Placeholder for the message box: the AI's example if it's short and safe, else a default."""
    if suggested:
        suggested = suggested.strip().strip('"')
        if suggested and len(suggested) <= EXAMPLE_MAX_CHARS and not LEVEL_MENTION.search(suggested):
            return suggested if suggested.lower().startswith("e.g.") else f"e.g. {suggested}"
    return EXAMPLES.get(topic, EXAMPLES["other"])


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
