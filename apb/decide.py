"""Gate, score and select: turns a mandate and quotes into a receipt."""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime

from apb.eligibility import apply_gate
from apb.models import DecisionReceipt, PaymentMandate, RouteQuote
from apb.scoring import score_routes


def mandate_hash(mandate: PaymentMandate) -> str:
    """Return the sha256 of the mandate's canonical JSON."""
    canonical = json.dumps(
        mandate.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _selection_key(quote: RouteQuote) -> tuple:
    """Order routes best first: most value, then fastest, then cheapest."""
    return (
        -quote.recipient_amount,
        quote.delivery_minutes,
        quote.fee,
        quote.provider,
    )


def decide(
    mandate: PaymentMandate,
    quotes: Sequence[RouteQuote],
    now: datetime | None = None,
) -> DecisionReceipt:
    """Decide a route for the mandate.

    `now` is the time quote expiry is checked against. The engine never reads
    the clock, so that a decision can be reproduced. When `now` is omitted it
    defaults to the newest quote's timestamp; callers holding live quotes must
    pass the real time, or stale quotes will not be caught.
    """
    providers = [quote.provider for quote in quotes]
    if len(set(providers)) != len(providers):
        raise ValueError("each provider may appear only once in a decision")

    candidates = sorted(quotes, key=lambda quote: quote.provider)
    evaluated_at = now or max(
        (quote.timestamp for quote in candidates), default=None
    )

    eligible, excluded = apply_gate(mandate, candidates, evaluated_at)
    scores = score_routes(eligible)
    selected = min(eligible, key=_selection_key, default=None)

    evidence_refs = [f"quote:{quote.quote_id}" for quote in candidates]
    if evaluated_at is not None:
        evidence_refs.append(f"evaluated_at:{evaluated_at.isoformat()}")

    return DecisionReceipt(
        mandate_hash=mandate_hash(mandate),
        candidate_set=candidates,
        excluded_routes=excluded,
        eligible_routes=[quote.provider for quote in eligible],
        selected_route=selected.provider if selected else None,
        delivered_value_score=scores[selected.provider] if selected else None,
        scores=scores,
        mandate_compliant=selected is not None,
        disclosure_passed=selected is not None,
        quote_rechecked_at_commit=False,
        user_approved=False,
        execution=None,
        evidence_refs=evidence_refs,
        explanation=None,
    )
