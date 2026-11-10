"""Execution adapters. PayPal is one adapter, reached only through `commit`.

`commit` is the only function that can invoke an adapter, and it does so only
when the decision is PAY after the commit-time recheck. A repeated request with
the same idempotency key returns the original result without a second call.
"""

import os
from decimal import Decimal
from typing import Protocol

from dotenv import load_dotenv

from apb.guard.pipeline import (
    Decision,
    IdempotencyStore,
    build_receipt,
    canonical_hash,
    decide,
)
from apb.guard.schemas import DecisionReceiptV3, EpisodeDefinition, ExecutionReference


class ExecutionAdapter(Protocol):
    name: str

    def execute(self, decision: Decision, idempotency_key: str) -> ExecutionReference: ...


class SimulatedSandboxAdapter:
    """Stands in for the PayPal sandbox where no credentials exist (the public demo).

    It makes no network call. Its reference says so, so it can't be mistaken for
    a PayPal payout.
    """

    name = "paypal_sandbox"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, decision: Decision, idempotency_key: str) -> ExecutionReference:
        self.calls += 1
        return ExecutionReference(
            adapter="paypal_sandbox",
            mode="simulated_no_credentials",
            reference="SIM-" + canonical_hash(idempotency_key)[7:19].upper(),
            status="SIMULATED_NOT_SENT",
            idempotency_key=idempotency_key,
        )


class PayPalSandboxAdapter:
    """Calls the PayPal sandbox through apb.paypal. Server-side credentials only."""

    name = "paypal_sandbox"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, decision: Decision, idempotency_key: str) -> ExecutionReference:
        from apb.paypal import PayPalError, submit_payout

        self.calls += 1
        amount = decision.trace.episode.mandate.amount
        try:
            payout = submit_payout(
                idempotency_key, Decimal(amount.value).quantize(Decimal("0.01")), amount.currency,
                note="APB Guard synthetic supplier payment (sandbox).",
            )
        except PayPalError as error:
            return ExecutionReference(
                adapter="paypal_sandbox", mode="sandbox", reference="none",
                status=f"failed:{error.code}", idempotency_key=idempotency_key,
            )
        return ExecutionReference(
            adapter="paypal_sandbox", mode="sandbox", reference=payout["payout_batch_id"],
            status=payout["batch_status"], idempotency_key=idempotency_key,
            duplicate=payout["duplicate"],
        )


def sandbox_execution_enabled() -> bool:
    load_dotenv()
    public = os.environ.get("PUBLIC_DEMO", "").strip().lower() == "true"
    allowed = os.environ.get("ALLOW_EXECUTE", "").strip().lower() == "true"
    return allowed and not public


def default_adapter() -> ExecutionAdapter:
    return PayPalSandboxAdapter() if sandbox_execution_enabled() else SimulatedSandboxAdapter()


def commit(
    decision: Decision, adapter: ExecutionAdapter, store: IdempotencyStore
) -> DecisionReceiptV3:
    """Execute a PAY decision once, then write the receipt. Anything else never executes."""
    if decision.outcome.action != "PAY":
        return build_receipt(decision, None)
    recheck = decision.trace.recheck
    if recheck is None or recheck.duplicate_check == "conflict":
        return build_receipt(decision, None)
    key = recheck.idempotency_key
    original = store.get(key)
    if original is not None:
        # Replay: the original result comes back and nothing is paid again.
        replay = original.model_copy(update={"duplicate": True})
        return build_receipt(decision, replay)
    reference = adapter.execute(decision, key)
    store.put(key, decision.trace.mandate_hash, reference)
    return build_receipt(_called(decision), reference)


def _called(decision: Decision) -> Decision:
    decision.outcome = decision.outcome.model_copy(update={"paypal_called": True})
    return decision


def run_episode(
    episode: EpisodeDefinition,
    adapter: ExecutionAdapter | None = None,
    store: IdempotencyStore | None = None,
) -> list[DecisionReceiptV3]:
    """Decide and commit, `replay_attempts` times, sharing one idempotency store."""
    adapter = adapter or default_adapter()
    store = store or IdempotencyStore()
    return [commit(decide(episode, store), adapter, store) for _ in range(episode.replay_attempts)]
