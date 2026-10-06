"""An AI-written screening report, checked against the screening results.

The levels come only from the rules engine (app/scoring.py). The AI's job is
to turn the results into a short report a project team can act on: what was
found, where, why it matters and what to do next.

The AI sees only the facts sheet (app/explain.py). Its report is checked:
every finding must cite real parts (S1.P2), every level it mentions must
match the parts it cites, and every part above A1 must be covered. If a
draft fails, the AI gets one retry with the problems listed; after that a
plain report is built from the facts directly. The same facts report is used
when the AI isn't available at all (see app/main.py).
"""

import json
import re

from pydantic import BaseModel, ConfigDict, Field

from app import llm
from app.explain import facts_sheet, part_levels

LEVEL_TOKEN = re.compile(r"\bA[1-4]\b")


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parts: list[str] = Field(description="References of the parts this finding covers, e.g. ['S1.P1']")
    title: str = Field(description="A short title, e.g. 'Crossing the Ouse Washes (0.00–0.95 km)'")
    explanation: str = Field(description="What was found there and why it matters, in two or three plain sentences")
    next_steps: list[str] = Field(description="Up to three next steps for this finding, built from the facts sheet's next steps")


class Report(BaseModel):
    """A short screening report for the project team."""
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(description="One sentence summing up the result, at most 25 words")
    overview: str = Field(description="Two or three sentences: the project, the overall level and what drives it")
    findings: list[Finding] = Field(description="One finding per issue; every part above A1 must be covered")
    next_steps: list[str] = Field(description="Up to four next steps for the project as a whole, by level, built from next_steps_by_level")
    caveats: list[str] = Field(description="One or two honest limits of this screening")


SYSTEM = """You write short screening reports for water company project teams (engineers and project managers). You're given a facts sheet from an automated screening of their project against protected nature sites in England. Each site is split into parts (S1.P2 = site 1, part 2), each with a level, what that level means, the reasons, the protected features involved and its next steps. next_steps_by_level gives what to do at each level found, and how Mozaic would help (with_mozaic).

Write a report they can act on:
- Use only the facts sheet for what was found: never invent sites, designations, distances, dates, species or levels. Mention a level only for the parts you cite, exactly as given.
- Group parts with the same level and story into one finding and cite them all in its parts list; every part above A1 must be cited. One short finding can cover the A1 parts together.
- Say where (e.g. "0.00–0.95 km along the route") and why it matters in plain words.

Next steps and advice:
1. Build advice from the supplied next steps (next_steps_by_level and each part's next_steps). Don't invent new required actions.
2. Never recommend less than the level requires: no "no further action" for anything at A2 or above.
3. No legal or regulatory conclusions. Say who decides: Natural England, or the competent authority.
4. Label any extra idea (a redraw, timing, directional drilling) clearly as a suggestion to check with an ecologist.
5. Describe Mozaic only using the supplied with_mozaic lines: one or two factual sentences, no prices, timelines or promises.
6. Be concise: summarise by level (how many parts, main reasons), name at most the two or three most important stretches in the text, and point to the map and Sections tab for the rest.

Plain English, no jargon without a short explanation.
- Caveats: be honest that this is indicative screening from public data, not a regulatory decision, plus any limits shown in the facts (e.g. parts not checked)."""


def generate_report(result: dict, intake: dict) -> dict:
    """Write and check the report. Returns the report plus where it came from ("ai" or "facts").

    Raises LLMError if the AI can't be reached for the first draft.
    """
    sheet = facts_sheet(result, intake)
    if not any(s["parts"] for s in sheet["sites"]):
        return {**fallback_report(sheet), "source": "facts"}

    messages = [{"role": "user", "content": "Facts sheet:\n" + json.dumps(sheet, ensure_ascii=False)}]
    system = SYSTEM
    for attempt in range(2):
        try:
            report = llm.generate(system, messages, Report)
        except llm.LLMError:
            if attempt == 0:
                raise
            break  # the first draft failed its checks and the retry failed: use the facts
        problems = check_report(report, sheet)
        if not problems:
            return {**report.model_dump(), "source": "ai"}
        system = SYSTEM + "\n\nYour last draft had these problems; fix them:\n- " + "\n- ".join(problems)
    return {**fallback_report(sheet), "source": "facts"}


def check_report(report: Report, sheet: dict) -> list[str]:
    """Problems with a draft report, in words the AI can act on. Empty if it's fine."""
    levels = part_levels(sheet)
    may_rise = {p["ref"]: p["may_rise_to"] for s in sheet["sites"] for p in s["parts"]}
    problems = []

    covered = set()
    for f in report.findings:
        unknown = [ref for ref in f.parts if ref not in levels]
        if unknown or not f.parts:
            problems.append(f"Finding '{f.title}' cites parts that don't exist: {unknown or 'none cited'}.")
            continue
        covered |= set(f.parts)
        allowed = _with_a3({levels[ref] for ref in f.parts} | {may_rise[ref] for ref in f.parts if may_rise[ref]})
        text = " ".join([f.title, f.explanation, *f.next_steps])
        wrong = set(LEVEL_TOKEN.findall(text)) - allowed
        if wrong:
            problems.append(f"Finding '{f.title}' mentions {sorted(wrong)}, but its parts are {sorted(allowed)}.")

    missing = [ref for ref, level in levels.items() if level != "A1" and ref not in covered]
    if missing:
        problems.append(f"These parts above A1 aren't covered by any finding: {missing}.")

    project_levels = _with_a3(set(levels.values()) | {v for v in may_rise.values() if v})
    text = " ".join([report.headline, report.overview, *report.next_steps, *report.caveats])
    wrong = set(LEVEL_TOKEN.findall(text)) - project_levels
    if wrong:
        problems.append(f"The headline, overview or next steps mention {sorted(wrong)}, which no part has.")
    return problems


def _with_a3(levels: set[str]) -> set[str]:
    """A4 is defined as A3 plus a survey ("As A3, plus…"), so A4 text may mention A3."""
    return levels | {"A3"} if "A4" in levels else levels


def facts_report(result: dict, intake: dict) -> dict:
    """The report built from the facts alone, for when the AI isn't available."""
    return {**fallback_report(facts_sheet(result, intake)), "source": "facts"}


def fallback_report(sheet: dict) -> dict:
    """A plain report built straight from the facts, used if the AI's drafts fail.

    Next steps are the ones from rules.yaml: each level's step for its parts,
    each reason's step, and per level how Mozaic would help.
    """
    order = ["A1", "A2", "A3", "A4"]
    by_level = sheet["next_steps_by_level"]
    parts = [(s, p) for s in sheet["sites"] for p in s["parts"]]
    top = max((p["level"] for _, p in parts), key=order.index, default=None)
    findings = [
        {
            "parts": [p["ref"]],
            "title": f"{s['name']}, {p['where']}: {p['level']}",
            "explanation": " ".join(p["reasons"]),
            "next_steps": [by_level[p["level"]]["next_step"], *p["next_steps"]],
        }
        for s, p in parts if p["level"] != "A1"
    ]
    quiet = [p["ref"] for _, p in parts if p["level"] == "A1"]
    if quiet:
        findings.append({
            "parts": quiet,
            "title": "Everywhere else: A1",
            "explanation": sheet["level_meanings"]["A1"],
            "next_steps": [by_level["A1"]["next_step"]],
        })
    counts = {lvl: sum(p["level"] == lvl for _, p in parts) for lvl in by_level}
    next_steps = [
        f"{lvl} ({counts[lvl]} part{'s' if counts[lvl] != 1 else ''}): {step['next_step']} "
        f"With Mozaic: {step['with_mozaic']}"
        for lvl, step in sorted(by_level.items(), key=lambda kv: order.index(kv[0]), reverse=True)
    ]
    return {
        "headline": f"Overall {top}: {sheet['level_meanings'][top]}" if top else "None of the sites could be screened.",
        "overview": f"{sheet['project']['work_type']} screened against protected sites, ancient woodland, "
                    "priority habitat and Natural England's risk zones.",
        "findings": findings,
        "next_steps": next_steps,
        "caveats": ["Indicative screening from public data, not a regulatory decision."],
    }
