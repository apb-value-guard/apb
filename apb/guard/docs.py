"""Write generated docs and schemas: python -m apb.guard.docs

docs/REASON_CODES.md and schemas/*.schema.json come from code, and tests fail
when the committed copies drift.
"""

import json
from pathlib import Path

from apb.guard import reason_codes
from apb.guard.episodes import ROOT
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


def outputs() -> dict[Path, str]:
    files = {ROOT / "docs" / "REASON_CODES.md": reason_codes.markdown()}
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
