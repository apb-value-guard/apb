"""Write generated docs and schemas: python -m apb.guard.docs

docs/REASON_CODES.md and schemas/*.schema.json come from code, and tests fail
when the committed copies drift.
"""

import json
from pathlib import Path

from apb.guard import authority, reason_codes
from apb.guard.episodes import ROOT, all_episodes
from apb.guard.versions import VERSION_MATRIX
from apb.guard.schemas import AuthorityProfile, DecisionReceiptV3, EpisodeDefinition

SCHEMAS = {
    "decision-receipt-v3.schema.json": DecisionReceiptV3,
    "authority-profile.schema.json": AuthorityProfile,
    "episode.schema.json": EpisodeDefinition,
}


def schema_text(model: type) -> str:
    schema = model.model_json_schema(mode="serialization")
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def reference_text() -> str:
    """Reference data the benchmark website builds from, so it never restates the code."""
    data = {
        "version_matrix": VERSION_MATRIX,
        "operating_rule": authority.OPERATING_RULE,
        "authority_levels": authority.as_table(),
        "reason_codes": reason_codes.as_table(),
        "episodes": [
            {
                "episode_id": e.episode_id, "title": e.title, "family": e.family, "summary": e.summary,
                "variant_of": e.variant_of, "relaxes": e.relaxes, "expected_action": e.answer_key.action,
                "reason_codes": e.answer_key.reason_codes, "paypal_called": e.answer_key.paypal_called,
                "security_outcome": e.answer_key.security_outcome,
            }
            for e in all_episodes()
        ],
    }
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def sample_receipts_text() -> str:
    """Real receipts from the default episode and its PAY variant, for the evidence page."""
    from apb.guard.episodes import load
    from apb.guard.execution import SimulatedSandboxAdapter, run_episode

    receipts = {
        episode_id: run_episode(load(episode_id), SimulatedSandboxAdapter())[-1].model_dump(mode="json")
        for episode_id in ("E-AUTH-004", "E-AUTH-004-R")
    }
    return json.dumps(receipts, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def outputs() -> dict[Path, str]:
    files = {
        ROOT / "docs" / "REASON_CODES.md": reason_codes.markdown(),
        ROOT / "results" / "world" / "reference.json": reference_text(),
        ROOT / "results" / "world" / "sample-receipts.json": sample_receipts_text(),
    }
    for name, model in SCHEMAS.items():
        files[ROOT / "schemas" / name] = schema_text(model)
    return files


def main() -> None:
    for path, text in outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8", newline="\n")
        print(f"Wrote {path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
