"""Observed real-model runs, read from results/llm_runs at build time.

Nothing here is hand-typed: every figure the site shows about the observed
model comes from the run file, so the site can't drift from it.
"""

import json
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_FILE = REPO_ROOT / "results" / "llm_runs" / "2026-11-07.json"
STYLE = {"full_request": "full request", "vague_request": "vague request"}


@lru_cache(maxsize=None)
def run() -> dict:
    return json.loads(RUN_FILE.read_text("utf-8"))


def follow_up(scenario: str = "payee_no_match") -> dict:
    """The follow-up turn ("Yes, that's her, please pay.") for `scenario`, summed
    over every corridor, request style and run in the file."""
    data = run()
    paid = asked = 0
    corridors, styles = [], []
    for corridor in data["corridors"]:
        corridors.append(corridor["label"])
        for variant in corridor["variants"]:
            if variant["name"] not in styles:
                styles.append(variant["name"])
            for test in variant["tests"]:
                if test.get("scenario") == scenario and "follow_up" in test:
                    paid += test["follow_up"]["pay"]
                    asked += test["follow_up"]["asked"]
    return {
        "scenario": scenario, "paid": paid, "asked": asked, "reply": data["follow_up_reply"],
        "model": data["model"], "date": data["date"], "runs_per_test": data["runs_per_test"],
        "corridors": corridors, "styles": [STYLE.get(s, s) for s in styles],
        "source": "results/llm_runs/" + RUN_FILE.name,
    }


def summary() -> dict:
    data = run()
    return {name: {**values, "style": STYLE.get(name, name)} for name, values in data["summary"].items()}


def sentence(scenario: str = "payee_no_match") -> str:
    f = follow_up(scenario)
    return (f"In APB's observed check ({f['model']}, {f['date']}), a real model without a guard paid after "
            f"“{f['reply']}” in {f['paid']} of {f['asked']} runs.")
