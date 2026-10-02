"""Try the intake chat live with three example work descriptions.

A scripted stand-in user answers each question by spotting keywords in it,
so the conversations are repeatable. Uses the provider set in .env.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import llm  # noqa: E402
from app.intake import next_turn  # noqa: E402

MAX_TURNS = 10

# Each scenario: opening description, then replies keyed by question topic.
# Topics are checked in order, so the more specific ones come first.
SCENARIOS = [
    {
        "opening": "We're upgrading 12 storm overflows to cut spills",
        "replies": {
            "survey": "No, we can do it from records.",
            "abstract": "No.",
            "dewater": "No.",
            "boundary": "No, everything is inside our existing sites.",
            "type": "Storm overflow work.",
        },
    },
    {
        "opening": "New 2 km sewer from a housing estate to the treatment works, will need groundwater pumping",
        "replies": {
            "survey": "Yes, we'll walk the route.",
            "abstract": "No, it's only to keep the trench dry.",
            "dewater": "Yes.",
            "boundary": "Yes, the route crosses farmland.",
            "type": "A new sewer.",
        },
    },
    {
        "opening": "We're building a reservoir",
        "replies": {
            "survey": "Yes.",
            "abstract": "Yes, it will be filled from the river.",
            "dewater": "No.",
            "boundary": "Yes, it's on a new site.",
            "type": "Yes, use that.",
        },
    },
]

TOPICS = {
    "survey": ("survey", "visit"),
    "abstract": ("abstract",),
    "dewater": ("dewater", "pump out", "excavation"),
    "boundary": ("boundary", "outside"),
}


def reply_to(question: str, replies: dict) -> str:
    q = question.lower()
    for topic, words in TOPICS.items():
        if any(w in q for w in words):
            return replies[topic]
    return replies["type"]


def run(scenario: dict) -> None:
    history = [{"role": "user", "content": scenario["opening"]}]
    print(f"USER: {scenario['opening']}")
    for _ in range(MAX_TURNS):
        turn = next_turn(history)
        if turn["type"] == "confirm":
            print(f"AI (confirm): {turn['summary']}")
            print(f"INTAKE: {turn['intake']}")
            return
        print(f"AI: {turn['text']}")
        answer = reply_to(turn["text"], scenario["replies"])
        print(f"USER: {answer}")
        history += [{"role": "assistant", "content": turn["text"]}, {"role": "user", "content": answer}]
    print(f"(stopped after {MAX_TURNS} turns without a summary)")


def main() -> None:
    print(f"Provider: {llm.provider()} ({llm.model()})")
    for i, scenario in enumerate(SCENARIOS, 1):
        print(f"\n{'=' * 72}\nConversation {i}\n{'=' * 72}")
        run(scenario)


if __name__ == "__main__":
    main()
