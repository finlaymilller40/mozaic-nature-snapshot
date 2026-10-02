"""Trial the scoring engine on every Anglian Water storm overflow in the AOI.

Each overflow point is buffered by 50 m and scored as a storm overflow
upgrade, with abstraction, dewatering and physical survey all "no". It runs
twice: works within the existing site boundary, then beyond it.
"""

import sys
from collections import Counter
from pathlib import Path

import geopandas as gpd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.scoring import CLEAN_DIR, check_site, load_rules, score_site  # noqa: E402

BUFFER_M = 50
EXAMPLES_PER_LEVEL = 2
BASE_INTAKE = {
    "work_type": "storm_overflow",
    "abstraction": "no",
    "dewatering": "no",
    "physical_survey_required": "no",
}


def main() -> None:
    levels = load_rules()["levels"]
    overflows = gpd.read_file(CLEAN_DIR / "storm_overflows.gpkg").sort_values("unique_id")
    sites = [(row, check_site(row.geometry.buffer(BUFFER_M))) for row in overflows.itertuples()]

    for beyond_site in ("no", "yes"):
        intake = {**BASE_INTAKE, "beyond_site": beyond_site}
        scored = [(row, score_site(facts, intake)) for row, facts in sites]
        counts = Counter(score["level"] for _, score in scored)

        print(f"\n{'=' * 72}\nbeyond_site = {beyond_site}: {len(scored)} storm overflows")
        print("  ".join(f"{level}: {counts.get(level, 0)}" for level in levels))

        for level in levels:
            examples = [(row, s) for row, s in scored if s["level"] == level][:EXAMPLES_PER_LEVEL]
            for row, score in examples:
                escalate = f" (may -> {score['may_escalate_to']})" if score["may_escalate_to"] else ""
                print(f"\n[{level}{escalate}] {row.unique_id} {row.site_name} -> {row.receiving_water}")
                for r in score["reasons"]:
                    print(f"   rule {r['rule']} ({r['level']}) {r['layer']}: {r['value']} | {r['detail']}")
                linkage = score["context"]["european_site_linkage"]
                if linkage:
                    sites_linked = sorted({s for z in linkage for s in z["european_sites"]})
                    print(f"   context: triggered zone linked to {'; '.join(sites_linked)}")


if __name__ == "__main__":
    main()
