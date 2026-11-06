"""Stage 1: the eligibility gate. Pure functions with no side effects."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from apb.models import ExcludedRoute, PaymentMandate, ReasonCode, RouteQuote


def violates_deadline(
    mandate: PaymentMandate, quote: RouteQuote, now: datetime
) -> bool:
    # An undisclosed delivery time is reported as disclosure_missing instead.
    return (
        quote.delivery_minutes is not None
        and quote.delivery_minutes > mandate.deadline_minutes
    )


def payout_unsupported(
    mandate: PaymentMandate, quote: RouteQuote, now: datetime
) -> bool:
    return quote.payout_method != mandate.payout_method


def amount_out_of_limits(
    mandate: PaymentMandate, quote: RouteQuote, now: datetime
) -> bool:
    """True when the quote does not cover exactly the mandated send amount."""
    return quote.send_amount != mandate.send_amount


def quote_expired(mandate: PaymentMandate, quote: RouteQuote, now: datetime) -> bool:
    return now >= quote.timestamp + timedelta(seconds=quote.quote_expiry_seconds)


def disclosure_missing(
    mandate: PaymentMandate, quote: RouteQuote, now: datetime
) -> bool:
    # Compare with None: a disclosed fee of 0.00 is a valid disclosure.
    return any(
        value is None
        for value in (
            quote.fee,
            quote.fx_rate,
            quote.recipient_amount,
            quote.delivery_minutes,
        )
    )


def hold_is_active(mandate: PaymentMandate) -> bool:
    result = mandate.screening_result
    return result is not None and result.status == "hold"


def screening_hold(mandate: PaymentMandate, quote: RouteQuote, now: datetime) -> bool:
    """A hold stops the payment, so it applies to every route."""
    return hold_is_active(mandate)


def payee_is_mismatched(mandate: PaymentMandate) -> bool:
    check = mandate.payee_check
    return check is not None and check.result == "no_match"


def needs_payee_confirmation(mandate: PaymentMandate) -> bool:
    check = mandate.payee_check
    return check is not None and check.result == "close_match"


def payee_mismatch(mandate: PaymentMandate, quote: RouteQuote, now: datetime) -> bool:
    """A wrong payee is wrong on every route."""
    return payee_is_mismatched(mandate)


def kyc_not_verified(
    mandate: PaymentMandate, quote: RouteQuote, now: datetime
) -> bool:
    verified = mandate.sender_verified_for or []
    return quote.requires_kyc and quote.provider not in verified


# When a route fails several checks, the first failure here is its reason.
_CHECKS = (
    (ReasonCode.SCREENING_HOLD, screening_hold),
    (ReasonCode.PAYEE_MISMATCH, payee_mismatch),
    (ReasonCode.DEADLINE_VIOLATION, violates_deadline),
    (ReasonCode.PAYOUT_UNSUPPORTED, payout_unsupported),
    (ReasonCode.AMOUNT_OUT_OF_LIMITS, amount_out_of_limits),
    (ReasonCode.QUOTE_EXPIRED, quote_expired),
    (ReasonCode.DISCLOSURE_MISSING, disclosure_missing),
    (ReasonCode.KYC_NOT_VERIFIED, kyc_not_verified),
)


def exclusion_reason(
    mandate: PaymentMandate, quote: RouteQuote, now: datetime
) -> ReasonCode | None:
    """Return why a route is excluded, or None if it is eligible."""
    for reason, fails in _CHECKS:
        if fails(mandate, quote, now):
            return reason
    return None


def apply_gate(
    mandate: PaymentMandate, quotes: Sequence[RouteQuote], now: datetime | None
) -> tuple[list[RouteQuote], list[ExcludedRoute]]:
    """Split quotes into eligible routes and excluded routes with reasons."""
    eligible: list[RouteQuote] = []
    excluded: list[ExcludedRoute] = []
    for quote in quotes:
        reason = exclusion_reason(mandate, quote, now)
        if reason is None:
            eligible.append(quote)
        else:
            excluded.append(ExcludedRoute(provider=quote.provider, reason=reason))
    return eligible, excluded
