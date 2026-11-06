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


# When a route fails several checks, the first failure here is its reason.
_CHECKS = (
    (ReasonCode.DEADLINE_VIOLATION, violates_deadline),
    (ReasonCode.PAYOUT_UNSUPPORTED, payout_unsupported),
    (ReasonCode.AMOUNT_OUT_OF_LIMITS, amount_out_of_limits),
    (ReasonCode.QUOTE_EXPIRED, quote_expired),
    (ReasonCode.DISCLOSURE_MISSING, disclosure_missing),
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
