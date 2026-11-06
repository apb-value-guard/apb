"""Stage 2: Delivered Value Score (DVS) for eligible routes."""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from apb.models import RouteQuote

_ONE_DECIMAL = Decimal("0.1")


def score_routes(eligible: Sequence[RouteQuote]) -> dict[str, float]:
    """Return DVS by provider.

    Pass only routes that cleared the gate. The benchmark is the best amount
    in `eligible`, so an excluded route here would distort every other score.
    """
    if not eligible:
        return {}
    best = max(quote.recipient_amount for quote in eligible)
    return {
        quote.provider: float(
            (quote.recipient_amount / best * 100).quantize(
                _ONE_DECIMAL, rounding=ROUND_HALF_UP
            )
        )
        for quote in eligible
    }
