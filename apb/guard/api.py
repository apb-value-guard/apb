"""APB Guard v3 HTTP API, mounted under /api/v3 by apb.web.

Read endpoints return public views only. Running an episode is the only way
to see its hidden attack text and answer key, and only after the decision is
made. Nothing here accepts credentials, and nothing is stored but in-memory
idempotency results and anonymous event counts.
"""

import json
import threading
from collections import Counter
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from apb.guard import authority, reason_codes
from apb.guard.episodes import ORDER, ROOT, load, public_view
from apb.guard.execution import default_adapter, run_episode, sandbox_execution_enabled
from apb.guard.pipeline import IdempotencyStore, decide
from apb.guard.versions import DEMO_ASSET_ID, VERSION_MATRIX
from apb.guard.world import RESULTS_PATH

router = APIRouter(prefix="/api/v3", tags=["APB Guard v3"])

# One store per process, so repeating a PAY never executes twice.
_store = IdempotencyStore()
_store_lock = threading.Lock()

EVENTS = {
    "episode_started", "authority_level_viewed", "attack_revealed", "gate_completed",
    "decision_returned", "receipt_viewed", "receipt_downloaded", "variant_run",
    "paypal_sandbox_executed", "pilot_request_started", "pilot_request_submitted",
    "video_play", "video_progress_25", "video_progress_50", "video_progress_75",
    "video_complete", "video_cta_click", "interactive_demo_click",
}
_events: Counter[str] = Counter()
_events_lock = threading.Lock()

MONITORING_PATH = ROOT / "fixtures" / "world" / "monitoring.json"


def _summary(episode_id: str) -> dict[str, Any]:
    episode = load(episode_id)
    return {
        "episode_id": episode.episode_id, "title": episode.title, "family": episode.family,
        "summary": episode.summary, "variant_of": episode.variant_of, "relaxes": episode.relaxes,
        "data_mode": episode.data_mode,
    }


@router.get("/versions", summary="The canonical version matrix")
def versions() -> dict[str, str]:
    return VERSION_MATRIX


@router.get("/episodes", summary="Synthetic episodes, without answer keys")
def episodes() -> dict[str, Any]:
    return {"episodes": [_summary(episode_id) for episode_id in ORDER]}


@router.get("/episodes/{episode_id}", summary="One episode's public view")
def episode(episode_id: str) -> dict[str, Any]:
    if episode_id not in ORDER:
        raise HTTPException(404, "Unknown episode.")
    return public_view(load(episode_id))


@router.post("/episodes/{episode_id}/run", summary="Run an episode through APB Guard")
def run(episode_id: str) -> dict[str, Any]:
    """Decide, recheck at commit and, only on PAY, execute through the sandbox adapter."""
    if episode_id not in ORDER:
        raise HTTPException(404, "Unknown episode.")
    definition = load(episode_id)
    adapter = default_adapter()
    if sandbox_execution_enabled():
        # Real sandbox calls share one store, so a repeated PAY never pays twice.
        with _store_lock:
            receipts = run_episode(definition, adapter, _store)
    else:
        # Simulated runs start clean, so every visitor sees the episode as designed.
        receipts = run_episode(definition, adapter, IdempotencyStore())
    decision = decide(definition, IdempotencyStore())
    revealed = definition.model_dump(mode="json")
    return {
        "episode": {key: revealed[key] for key in (
            "episode_id", "title", "family", "summary", "relaxes", "variant_of", "identity",
            "principal", "delegation", "mandate", "sources", "beneficiary", "approval",
            "risk_signals", "routes", "quotes", "commit", "data_mode")},
        "authority": decision.trace.authority.model_dump(mode="json"),
        "policy_decision": decision.policy.model_dump(mode="json"),
        "receipts": [receipt.model_dump(mode="json") for receipt in receipts],
        "answer_key": definition.answer_key.model_dump(mode="json"),
        "execution_mode": "sandbox" if sandbox_execution_enabled() else "simulated_no_credentials",
    }


@router.get("/authority", summary="Authority Levels 0-5")
def authority_levels() -> dict[str, Any]:
    return {"operating_rule": authority.OPERATING_RULE, "levels": authority.as_table()}


@router.get("/reason-codes", summary="The reason-code taxonomy")
def codes() -> dict[str, Any]:
    return {"reason_codes": reason_codes.as_table()}


@router.get("/world", summary="Latest deterministic APB World results")
def world_results() -> dict[str, Any]:
    return json.loads(RESULTS_PATH.read_text("utf-8"))


@router.get("/monitoring", summary="Illustrative monitoring signals and circuit breakers")
def monitoring() -> dict[str, Any]:
    return json.loads(MONITORING_PATH.read_text("utf-8"))


class Event(BaseModel):
    """An anonymous product event. Payment details, prompts and receipts are never accepted."""

    model_config = ConfigDict(extra="forbid")

    event: str = Field(max_length=40)
    asset_id: Literal["apb_v2_product_demo"] | None = None
    episode_id: str | None = Field(default=None, max_length=20)


@router.post("/events", status_code=204, summary="Record an anonymous product event")
def record(event: Event) -> None:
    if event.event not in EVENTS:
        raise HTTPException(422, "Unknown event.")
    if event.episode_id is not None and event.episode_id not in ORDER:
        raise HTTPException(422, "Unknown episode.")
    with _events_lock:
        _events[event.event] += 1


def event_counts() -> dict[str, int]:
    with _events_lock:
        return dict(_events)


__all__ = ["router", "event_counts", "DEMO_ASSET_ID"]
