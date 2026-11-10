"""APB World episodes: synthetic fixtures with server-side answer keys.

A variant file names the episode it `extends` and a `patch`. Dicts merge, lists
replace. "${mandate_hash}" in a variant's approval is resolved to the merged
mandate's hash, which is what binding an approval to that mandate means.

`public_view` is the only shape a browser gets before an episode runs: no
answer key and no hidden attack text.
"""

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from apb.guard.schemas import EpisodeDefinition, PaymentInstruction

ROOT = Path(__file__).resolve().parent.parent.parent
EPISODES_DIR = ROOT / "fixtures" / "world" / "episodes"
MANDATE_HASH = "${mandate_hash}"

# The order the demo and the docs present them in.
ORDER = [
    "E-AUTH-004", "E-AUTH-004-C", "E-AUTH-004-R", "E-NEUT-001", "E-HAND-001",
    "E-EXEC-002", "E-CHG-001", "E-CHG-002", "E-EXEC-001", "E-AUTH-001", "E-AUTH-002",
    "E-AUTH-003", "E-INJ-001", "E-AMT-001", "E-BEN-001", "E-BEN-002", "E-RISK-001",
]


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _raw(episode_id: str) -> dict[str, Any]:
    data = json.loads((EPISODES_DIR / f"{episode_id}.json").read_text("utf-8"))
    if "extends" in data:
        base = _raw(data.pop("extends"))
        patch = data.pop("patch", {})
        data = _merge(_merge(base, patch), data)
    return data


def _resolve(data: dict[str, Any]) -> dict[str, Any]:
    from apb.guard.pipeline import canonical_hash

    approval = data.get("approval")
    if approval and approval.get("bound_mandate_hash") == MANDATE_HASH:
        mandate = PaymentInstruction.model_validate(data["mandate"])
        approval["bound_mandate_hash"] = canonical_hash(mandate.model_dump(mode="json"))
    return data


@lru_cache(maxsize=None)
def load(episode_id: str) -> EpisodeDefinition:
    if episode_id not in ORDER:
        raise KeyError(episode_id)
    return EpisodeDefinition.model_validate(_resolve(_raw(episode_id)))


def all_episodes() -> list[EpisodeDefinition]:
    return [load(episode_id) for episode_id in ORDER]


def public_view(episode: EpisodeDefinition) -> dict[str, Any]:
    """What a browser may see before running: no answer key, no hidden payload."""
    data = episode.model_dump(mode="json", exclude={"answer_key"})
    for source in data["sources"]:
        source["hidden_excerpt"] = None
        if source["kind"] == "external_content":
            source["extracted"] = None
    return data
