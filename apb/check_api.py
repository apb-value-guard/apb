"""The Check API: deterministic decision support for an agent about to pay.

No AI runs here, nothing is stored, and nothing executes. The web app and the
MCP server both call `check` and `recheck`.
"""

import hashlib
import hmac
import os
import re
import secrets
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from apb.benchmark import METHOD_VERSION
from apb.decide import decide, mandate_hash
from apb.eligibility import exclusion_reason, hold_is_active, payee_is_mismatched
from apb.explain import template_explanation
from apb.models import (
    DecisionReceipt,
    PayeeCheck,
    PaymentMandate,
    ReasonCode,
    RouteQuote,
    ScreeningResult,
    _canonical_decimal,
)

NOTICE = "Decision support only. APB never moves money."
MAX_QUOTES = 50

# A keyed hash, because an unkeyed hash of a phone number can be reversed by
# trying every number. Without APB_WALLET_KEY the key lasts for one process.
_WALLET_KEY = (os.environ.get("APB_WALLET_KEY") or secrets.token_hex(32)).encode()

REASON_CODES = [
    ("deadline_violation", "route", "The route's delivery time is longer than the mandate's deadline."),
    ("payout_unsupported", "route", "The route doesn't pay out by the mandate's payout method."),
    ("amount_out_of_limits", "route", "The quote doesn't cover exactly the mandate's send amount."),
    ("quote_expired", "route", "The quote is past its expiry time when it is checked."),
    ("disclosure_missing", "route", "The quote leaves out its fee, FX rate, recipient amount or delivery time."),
    ("screening_hold", "all", "A screening result is on hold, so every route is excluded and nothing may be paid."),
    ("payee_mismatch", "all", "The payee name check found no match, so every route is excluded. No confirmation can release it."),
    ("kyc_not_verified", "route", "The route requires KYC that the sender hasn't completed with that provider."),
]


class CheckContext(BaseModel):
    """What the caller knows beyond the quotes. APB uses it and verifies none of it."""

    model_config = ConfigDict(extra="forbid")

    screening_result: ScreeningResult | None = None
    payee_check: PayeeCheck | None = None
    sender_verified_for: list[str] | None = None
    user_confirmed_payee: bool = Field(
        default=False,
        description="The user confirmed a close payee name match. It never releases a no_match.",
    )
    user_approved_fee: bool = Field(
        default=False,
        description="The user approved a fee above the mandate's approval limit.",
    )


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate: PaymentMandate
    quotes: list[RouteQuote] = Field(min_length=1, max_length=MAX_QUOTES)
    context: CheckContext | None = None
    now: AwareDatetime | None = Field(
        default=None,
        description="When to check quote expiry. Defaults to the newest quote's timestamp.",
    )


class RecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt: DecisionReceipt
    mandate: PaymentMandate = Field(
        description="The same mandate as the earlier check. It must match receipt.mandate_hash."
    )
    quotes: list[RouteQuote] = Field(min_length=1, max_length=MAX_QUOTES)
    now: AwareDatetime
    context: CheckContext | None = None


class CheckError(ValueError):
    """The request is well formed but can't be checked. The message is safe to return."""


def wallet_ref(wallet: str) -> str:
    """Return a keyed hash of the wallet, so the number itself never leaves."""
    normalised = re.sub(r"[^0-9a-z]", "", wallet.lower()).encode()
    digest = hmac.new(_WALLET_KEY, normalised, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest[:16]}"


def merged_mandate(mandate: PaymentMandate, context: CheckContext | None) -> PaymentMandate:
    """Apply the context to the mandate. A conflict is an error, not a choice."""
    updates: dict[str, Any] = {}
    for field in ("screening_result", "payee_check", "sender_verified_for"):
        value = getattr(context, field) if context else None
        if value is None:
            continue
        existing = getattr(mandate, field)
        if existing is not None and existing != value:
            raise CheckError(f"{field} is set differently in the mandate and the context.")
        updates[field] = value
    return mandate.model_copy(update=updates)


def _decision(
    receipt: DecisionReceipt, mandate: PaymentMandate, context: CheckContext
) -> tuple[Literal["pay", "pause", "stop"], str]:
    if hold_is_active(mandate):
        return "stop", ReasonCode.SCREENING_HOLD.value
    if payee_is_mismatched(mandate):
        return "stop", ReasonCode.PAYEE_MISMATCH.value
    if receipt.selected_route is None:
        return "stop", "no_eligible_route"
    if receipt.confirmation_reason and not context.user_confirmed_payee:
        return "pause", "payee_close_match"
    quote = next(q for q in receipt.candidate_set if q.provider == receipt.selected_route)
    if quote.fee > mandate.approval_required_above_fee and not context.user_approved_fee:
        return "pause", "approval_required"
    return "pay", "best_eligible_route"


def check(request: CheckRequest) -> dict[str, Any]:
    """Gate and score the quotes, and say whether to pay, pause or stop."""
    context = request.context or CheckContext()
    mandate = merged_mandate(request.mandate, request.context)
    try:
        receipt = decide(mandate, request.quotes, now=request.now)
    except ValueError as error:
        raise CheckError(str(error)) from None
    receipt = receipt.model_copy(
        update={
            "explanation": template_explanation(receipt),
            "explanation_source": "template",
        }
    )
    decision, reason = _decision(receipt, mandate, context)
    data = receipt.model_dump(mode="json")
    return {
        "notice": NOTICE,
        "method_version": METHOD_VERSION,
        "decision": decision,
        "decision_reason": reason,
        "selected_route": receipt.selected_route,
        "excluded_routes": data["excluded_routes"],
        "scores": receipt.scores,
        "delivered_value_score": receipt.delivered_value_score,
        "recipient_wallet_ref": wallet_ref(mandate.recipient_wallet),
        "receipt": data,
    }


def recheck(request: RecheckRequest) -> dict[str, Any]:
    """Check the earlier choice against fresh quotes, and re-decide if it no longer holds."""
    mandate = merged_mandate(request.mandate, request.context)
    if mandate_hash(mandate) != request.receipt.mandate_hash:
        raise CheckError("The mandate and context don't match the receipt's mandate_hash.")

    route = request.receipt.selected_route
    result: dict[str, Any] = {
        "notice": NOTICE,
        "method_version": METHOD_VERSION,
        "checked_at": request.now.isoformat(),
        "route": route,
        "status": "ok",
        "reason": None,
        "gate_reason": None,
        "earlier_recipient_amount": None,
        "fresh_recipient_amount": None,
        "decision": None,
    }
    if route is None:
        return {**result, "reason": "no_route_to_recheck"}

    earlier = next(q for q in request.receipt.candidate_set if q.provider == route)
    fresh = next((q for q in request.quotes if q.provider == route), None)
    result["earlier_recipient_amount"] = _canonical_decimal(earlier.recipient_amount)

    if fresh is None:
        result.update(status="re-decide", reason="route_not_quoted")
    else:
        if fresh.recipient_amount is not None:
            result["fresh_recipient_amount"] = _canonical_decimal(fresh.recipient_amount)
        gate = exclusion_reason(mandate, fresh, request.now)
        if gate is ReasonCode.QUOTE_EXPIRED:
            result.update(status="re-decide", reason="quote_expired", gate_reason=gate.value)
        elif gate is not None:
            result.update(status="re-decide", reason="route_no_longer_eligible", gate_reason=gate.value)
        elif fresh.recipient_amount < earlier.recipient_amount:
            result.update(status="re-decide", reason="rate_worse")

    if result["status"] == "re-decide":
        result["decision"] = check(
            CheckRequest(
                mandate=request.mandate,
                quotes=request.quotes,
                context=request.context,
                now=request.now,
            )
        )
    return result


def reason_codes() -> dict[str, Any]:
    return {
        "notice": NOTICE,
        "method_version": METHOD_VERSION,
        "reason_codes": [
            {"code": code, "scope": scope, "meaning": meaning}
            for code, scope, meaning in REASON_CODES
        ],
    }
