"""The results assistant: answers questions about a screening using tools.

Claude reasons and explains; the tools are the deterministic screening code.
It can look up parts and protected features, measure distances, and re-screen
with different answers ("what if we didn't need dewatering?"). It can't change
levels or geometry itself, and every level in its answer is checked against
what the tools returned.

Answers cite parts as [S1.P2] (site 1, part 2) so the map can highlight them.
"""

import re
from typing import Literal

from shapely.geometry.base import BaseGeometry

from app import llm
from app.explain import FEATURE_KINDS, facts_sheet, feature_label, level_next_steps, part_levels
from app.scoring import load_layers, load_rules, validate_intake
from app.screening import Project, screen_project

PART_REF = re.compile(r"\[(S\d+\.P\d+)\]")
LEVEL_TOKEN = re.compile(r"\bA[1-4]\b")
MAX_MATCHES = 5
NEAREST_LAYERS = {
    "sssi": ("sssi", lambda r: r["name"]),
    "sac": ("european", lambda r: r["site"]),
    "spa": ("european", lambda r: r["site"]),
    "ramsar": ("european", lambda r: r["site"]),
    "ancient_woodland": ("ancient_woodland", lambda r: r["name"] or "Ancient woodland"),
    "priority_habitat": ("priority_habitat", lambda r: r["mainhabs"]),
}

SYSTEM = """You answer questions from a water company project team about an automated screening of their project against protected nature sites in England. Use the tools to look things up; never guess.

The project is split into sites (S1, S2…) and each site into parts (S1.P2 = site 1, part 2), each with a level: A1 nothing found, a desk check is enough; A2 an ecologist should review from records; A3 specialist assessment, likely with Natural England input; A4 specialist assessment plus a site survey.

How to answer:
- Keep it short and plain: a few sentences, or a short list. No jargon without a brief explanation.
- Routes are checked as a working corridor either side of the line, so "inside" a site can mean the corridor overlaps it while the centreline doesn't. find_feature gives both the distance and how much of the route's centreline is inside.
- Mention a level only as the tools report it. To answer "what if…" questions about the answers (dewatering, abstraction, work outside your land, site survey, type of work), use what_if and say clearly what would change.
- Cite parts in square brackets, like [S1.P1], whenever you talk about a specific part. The app shows each reference as a link with the part's location (e.g. "0.00–0.95 km"), so don't repeat the location next to it.
- You can't move or redraw the route yourself. If they ask about a re-route, use the tools to say where the problem is and how far the nearest protected features are, and suggest they redraw it and check again.
- If something isn't in the data, say so.

Advice and next steps (list_parts gives each level's next step and with_mozaic line, and each part's next steps; get_part gives a part's full set):
1. Build advice from the supplied next steps. Don't invent new required actions.
2. Never recommend less than the level requires: no "no further action" for anything at A2 or above.
3. No legal or regulatory conclusions. Say who decides: Natural England, or the competent authority.
4. Label any extra idea (a redraw, timing, directional drilling) clearly as a suggestion to check with an ecologist.
5. Describe Mozaic only using the supplied with_mozaic lines: one or two factual sentences, no prices, timelines or promises.
6. Be concise: summarise by level (how many parts, main reasons), name at most the two or three most important stretches, and point to the map and Sections tab for the rest. Nest lists properly: sub-points go on their own lines, indented two spaces under their bullet.
7. State advice directly. Never refer to "supplied" information, the facts sheet, the tools or these instructions. Keep the terms in the next steps as given; don't define or rephrase them (for example, don't call SSSI assent "consent"). No asides such as "not you"."""

def tool_definitions() -> list[dict]:
    def nullable(values: list[str]) -> dict:
        return {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]}

    nullable_yes_no = nullable(["yes", "no"])
    return [
        {
            "name": "list_parts",
            "description": "Every site and part in the screening: reference, where it is, level and main reason.",
            "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            "strict": True,
        },
        {
            "name": "get_part",
            "description": "Full detail for one part: where, level, what the level means, every reason, the protected features involved and the nearest SSSI unit's condition.",
            "input_schema": {
                "type": "object",
                "properties": {"ref": {"type": "string", "description": "Part reference, e.g. S1.P2"}},
                "required": ["ref"], "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "find_feature",
            "description": "Look up protected features the project touches by name (e.g. 'Ouse Washes', 'risk zone', 'woodland'): what they are, the level they lead to, which parts touch them, and how much of each site is inside them.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "All or part of the feature's name or kind"}},
                "required": ["name"], "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "nearest",
            "description": "The nearest protected features of one kind to a site, whether or not the site touches them, with distances in metres.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "site": {"type": "string", "description": "Site reference, e.g. S1"},
                    "kind": {"type": "string", "enum": list(NEAREST_LAYERS)},
                },
                "required": ["site", "kind"], "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "what_if",
            "description": "Re-screen the same sites with some answers changed and compare levels before and after. Set only the answers to change; leave the rest null.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "work_type": nullable(list(load_rules()["work_types"])),
                    "beyond_site": nullable_yes_no,
                    "abstraction": nullable_yes_no,
                    "dewatering": nullable_yes_no,
                    "physical_survey_required": nullable_yes_no,
                },
                "required": ["work_type", "beyond_site", "abstraction", "dewatering", "physical_survey_required"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    ]


class Workspace:
    """One screening, with the tools Claude can use on it."""

    def __init__(self, project: Project, intake: dict):
        self.project = project
        self.intake = intake
        self.result = screen_project(project, intake)
        self.sheet = facts_sheet(self.result, intake)
        self.levels_seen = set(part_levels(self.sheet).values())

    def handle(self, name: str, args: dict):
        if name == "list_parts":
            return self.list_parts()
        if name == "get_part":
            return self.get_part(args["ref"])
        if name == "find_feature":
            return self.find_feature(args["name"])
        if name == "nearest":
            return self.nearest(args["site"], args["kind"])
        if name == "what_if":
            return self.what_if({k: v for k, v in args.items() if v is not None})
        raise ValueError(f"Unknown tool {name}")

    # ---- tools

    def list_parts(self, sheet: dict | None = None) -> list[dict]:
        sheet = sheet or self.sheet
        return [
            {
                "site": s["site"], "name": s["name"], "shape": s["shape"], "site_level": s["level"],
                "how_checked": s.get("how_checked"),
                **({"not_checked": s["not_checked"]} if s.get("not_checked") else {}),
                "parts": [{"ref": p["ref"], "where": p["where"], "level": p["level"], "main_reason": p["reasons"][0],
                           "next_steps": p["next_steps"]}
                          for p in s["parts"]],
                "next_steps_by_level": level_next_steps((p["level"] for p in s["parts"]), load_rules()),
            }
            for s in sheet["sites"]
        ]

    def get_part(self, ref: str) -> dict:
        for site in self.sheet["sites"]:
            for part in site["parts"]:
                if part["ref"] == ref.strip().upper():
                    level = self.sheet["next_steps_by_level"][part["level"]]
                    return {"site": site["name"], **part, "level_next_step": level["next_step"],
                            "with_mozaic": level["with_mozaic"]}
        raise ValueError(f"No part {ref}. Use list_parts to see the references.")

    def find_feature(self, name: str) -> list[dict]:
        query = name.strip().lower()
        found = []
        for i, site in enumerate(self.result["features"]):
            props = site["properties"]
            geometry = self.project.plans[i].geometry
            for f in props.get("features", []):
                label = feature_label(f)
                if query not in label.lower() and query not in FEATURE_KINDS[f["kind"]].lower():
                    continue
                parts = [f"S{i + 1}.P{j + 1}" for j, part in enumerate(props.get("parts", []))
                         if any(f["ref"] in r.get("refs", []) for r in part["reasons"])]
                found.append({
                    "feature": label,
                    "kind": FEATURE_KINDS[f["kind"]],
                    "level_it_leads_to": f["level"],
                    "parts": parts,
                    "site": f"S{i + 1}",
                    **_overlap(geometry, _feature_geometry(f["ref"])),
                })
                if len(found) >= MAX_MATCHES:
                    return found
        return found or [{"note": f"The project doesn't touch any feature matching '{name}'. Try nearest()."}]

    def nearest(self, site: str, kind: str) -> list[dict]:
        index = _site_index(site, len(self.project.plans))
        geometry = self.project.plans[index].geometry
        layer_name, name_of = NEAREST_LAYERS[kind]
        layer = load_layers()[layer_name]
        if layer_name == "european":
            layer = layer[layer["designation"].str.lower() == kind]
        if layer.empty:
            return [{"note": f"There are no {FEATURE_KINDS[kind]} features in the screened area."}]
        rows, distances = layer.sindex.nearest(geometry, return_all=True, return_distance=True)
        nearest = {}
        for row, distance in zip(rows[1], distances):
            label = name_of(layer.iloc[row])
            nearest.setdefault(label, round(float(distance)))
        if len(nearest) < MAX_MATCHES:  # also look a little further afield
            for row in layer.sindex.query(geometry.buffer(5_000), predicate="intersects"):
                label = name_of(layer.iloc[row])
                nearest.setdefault(label, round(float(layer.geometry.iloc[row].distance(geometry))))
        closest = sorted(nearest.items(), key=lambda kv: kv[1])[:MAX_MATCHES]
        return [{"feature": label, "kind": FEATURE_KINDS[kind], "distance_m": d} for label, d in closest]

    def what_if(self, changes: dict) -> dict:
        if not changes:
            raise ValueError("Set at least one answer to change.")
        intake = {**self.intake, **changes}
        validate_intake(intake)
        result = screen_project(self.project, intake)
        sheet = facts_sheet(result, intake)
        self.levels_seen |= set(part_levels(sheet).values())
        return {
            "changed_answers": changes,
            "what_changes": _differences(self.result, result),
            "before": self.list_parts(),
            "after": self.list_parts(sheet),
            "note": "Part references in 'after' come from the re-screening; parts may split or merge differently.",
        }


def answer(project: Project, intake: dict, history: list[dict]) -> dict:
    """Answer the latest question in history. Returns {answer, refs}."""
    workspace = Workspace(project, intake)
    tools = tool_definitions()
    system = SYSTEM
    for _ in range(2):
        text = llm.run_tools(system, history, tools, workspace.handle)
        seen = workspace.levels_seen | ({"A3"} if "A4" in workspace.levels_seen else set())  # A4 is "as A3, plus…"
        wrong = set(LEVEL_TOKEN.findall(text)) - seen
        if not wrong:
            break
        system = SYSTEM + f"\n\nYour last answer mentioned {sorted(wrong)}, which no tool reported. Only state levels the tools give you."
    else:
        text += "\n\n(Please check the levels against the results panel.)"
    valid = set(part_levels(workspace.sheet))
    refs = [ref for ref in dict.fromkeys(PART_REF.findall(text)) if ref in valid]
    return {"answer": text, "refs": refs}


# ---- helpers


def _differences(before: dict, after: dict) -> list[str]:
    """Plain list of what a re-screening changed, worked out by the code."""
    changes = []
    for i, (b, a) in enumerate(zip(before["features"], after["features"])):
        bp, ap = b["properties"], a["properties"]
        name = bp.get("name") or f"Site {i + 1}"
        if bp.get("level") != ap.get("level"):
            changes.append(f"{name} overall: {bp.get('level')} -> {ap.get('level')}")
        if bp.get("kind") == "line":
            # Compare the level along the route, stretch by stretch.
            edges = sorted({round(p[k], 3) for props in (bp, ap) for p in props.get("parts", []) for k in ("start_km", "end_km")})
            for start, end in zip(edges, edges[1:]):
                mid = (start + end) / 2
                old = next((p["level"] for p in bp["parts"] if p["start_km"] <= mid <= p["end_km"]), None)
                new = next((p["level"] for p in ap["parts"] if p["start_km"] <= mid <= p["end_km"]), None)
                if old and new and old != new:
                    changes.append(f"{name}, {start:.2f}–{end:.2f} km: {old} -> {new}")
        else:
            old = sorted((p["level"], round(p.get("area_ha", 0), 2)) for p in bp.get("parts", []))
            new = sorted((p["level"], round(p.get("area_ha", 0), 2)) for p in ap.get("parts", []))
            if old != new:
                changes.append(f"{name} parts (level, hectares): {old} -> {new}")
    return changes or ["No levels change."]



def _site_index(site: str, count: int) -> int:
    match = re.fullmatch(r"S(\d+)", site.strip().upper())
    if not match or not 1 <= int(match.group(1)) <= count:
        raise ValueError(f"No site {site}. Sites are S1 to S{count}.")
    return int(match.group(1)) - 1


def _feature_geometry(ref: str) -> BaseGeometry:
    layer, index = ref.split(":")
    return load_layers()[layer].geometry.loc[int(index)]


def _overlap(site: BaseGeometry, feature: BaseGeometry) -> dict:
    """How much of the site is inside the feature, and how far apart they are."""
    out = {"distance_m": round(site.distance(feature))}
    if site.geom_type in ("LineString", "MultiLineString"):
        out["route_inside_m"] = round(site.intersection(feature).length)
    elif site.geom_type in ("Polygon", "MultiPolygon"):
        out["area_inside_ha"] = round(site.intersection(feature).area / 10_000, 2)
    return out


__all__ = ["answer", "tool_definitions", "Workspace", "Literal"]
