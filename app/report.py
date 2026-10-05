"""An AI-written screening report, checked against the screening results.

The levels come only from the rules engine (app/scoring.py). The AI's job is
to turn the results into a short report a project team can act on: what was
found, where, why it matters and what to do next.

The AI sees only the facts sheet (app/explain.py). Its report is checked:
every finding must cite real parts (S1.P2), every level it mentions must
match the parts it cites, and every part above A1 must be covered. If a
draft fails, the AI gets one retry with the problems listed; after that a
plain report is built from the facts directly.
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
    next_steps: list[str] = Field(description="Up to three practical next steps for this finding")


class Report(BaseModel):
    """A short screening report for the project team."""
    model_config = ConfigDict(extra="forbid")
    headline: str = Field(description="One sentence summing up the result, at most 25 words")
    overview: str = Field(description="Two or three sentences: the project, the overall level and what drives it")
    findings: list[Finding] = Field(description="One finding per issue; every part above A1 must be covered")
    next_steps: list[str] = Field(description="Up to four next steps for the project as a whole")
    caveats: list[str] = Field(description="One or two honest limits of this screening")


SYSTEM = """You write short screening reports for water company project teams (engineers and project managers). You're given a facts sheet from an automated screening of their project against protected nature sites in England. Each site is split into parts (S1.P2 = site 1, part 2), each with a level, what that level means, the reasons, and the protected features involved.

Write a report they can act on:
- Use only the facts sheet for what was found: never invent sites, designations, distances, dates, species or levels. Mention a level only for the parts you cite, exactly as given.
- Group parts with the same story into one finding and cite them all. Cover every part above A1. One short finding can cover the A1 parts together.
- Say where (e.g. "0.00–0.95 km along the route") and why it matters in plain words.
- Next steps should be practical and specific to what was found. You may draw on general good practice in England, phrased as suggestions rather than legal advice. For example: contact Natural England early (their Discretionary Advice Service) where an SSSI or risk zone is involved; water companies, as statutory undertakers, normally need Natural England's assent before work likely to damage an SSSI; work affecting a SAC, SPA or Ramsar site may need a Habitats Regulations Assessment; keep a buffer of at least 15 m from ancient woodland; consider timing work outside the bird breeding season near an SPA; consider whether a short re-route could avoid a designated site.
- Plain English, concise. No jargon without a short explanation.
- Caveats: be honest that this is indicative screening from public data, not a regulatory decision, plus any limits shown in the facts (e.g. parts not checked)."""


def generate_report(result: dict, intake: dict) -> dict:
    """Write and check the report. Returns the report plus where it came from ("ai" or "facts")."""
    sheet = facts_sheet(result, intake)
    if not any(s["parts"] for s in sheet["sites"]):
        return {**fallback_report(sheet), "source": "facts"}

    messages = [{"role": "user", "content": "Facts sheet:\n" + json.dumps(sheet, ensure_ascii=False)}]
    system = SYSTEM
    for _ in range(2):
        report = llm.generate(system, messages, Report)
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
        allowed = {levels[ref] for ref in f.parts} | {may_rise[ref] for ref in f.parts if may_rise[ref]}
        text = " ".join([f.title, f.explanation, *f.next_steps])
        wrong = set(LEVEL_TOKEN.findall(text)) - allowed
        if wrong:
            problems.append(f"Finding '{f.title}' mentions {sorted(wrong)}, but its parts are {sorted(allowed)}.")

    missing = [ref for ref, level in levels.items() if level != "A1" and ref not in covered]
    if missing:
        problems.append(f"These parts above A1 aren't covered by any finding: {missing}.")

    project_levels = set(levels.values()) | {v for v in may_rise.values() if v}
    text = " ".join([report.headline, report.overview, *report.next_steps, *report.caveats])
    wrong = set(LEVEL_TOKEN.findall(text)) - project_levels
    if wrong:
        problems.append(f"The headline, overview or next steps mention {sorted(wrong)}, which no part has.")
    return problems


def fallback_report(sheet: dict) -> dict:
    """A plain report built straight from the facts, used if the AI's drafts fail."""
    order = ["A1", "A2", "A3", "A4"]
    parts = [(s, p) for s in sheet["sites"] for p in s["parts"]]
    top = max((p["level"] for _, p in parts), key=order.index, default=None)
    findings = [
        {
            "parts": [p["ref"]],
            "title": f"{s['name']}, {p['where']}: {p['level']}",
            "explanation": " ".join(p["reasons"]),
            "next_steps": [p["what_it_means"]],
        }
        for s, p in parts if p["level"] != "A1"
    ]
    quiet = [p["ref"] for _, p in parts if p["level"] == "A1"]
    if quiet:
        findings.append({
            "parts": quiet,
            "title": "Everywhere else: A1",
            "explanation": sheet["level_meanings"]["A1"],
            "next_steps": [],
        })
    return {
        "headline": f"Overall {top}: {sheet['level_meanings'][top]}" if top else "None of the sites could be screened.",
        "overview": f"{sheet['project']['work_type']} screened against protected sites, ancient woodland, "
                    "priority habitat and Natural England's risk zones.",
        "findings": findings,
        "next_steps": [],
        "caveats": ["Indicative screening from public data, not a regulatory decision."],
    }
