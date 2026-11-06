"""Data models for mandates, route quotes and decision receipts."""

from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PositiveInt,
)


def _canonical_decimal(value: Decimal) -> str:
    """Render a decimal so that numerically equal values serialize identically."""
    return format(value.normalize(), "f")


Money = Annotated[
    Decimal, PlainSerializer(_canonical_decimal, return_type=str, when_used="json")
]
NonNegativeMoney = Annotated[Money, Field(ge=0)]
PositiveMoney = Annotated[Money, Field(gt=0)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReasonCode(str, Enum):
    DEADLINE_VIOLATION = "deadline_violation"
    PAYOUT_UNSUPPORTED = "payout_unsupported"
    AMOUNT_OUT_OF_LIMITS = "amount_out_of_limits"
    QUOTE_EXPIRED = "quote_expired"
    DISCLOSURE_MISSING = "disclosure_missing"


class PaymentMandate(_Model):
    mandate_id: str
    send_amount: PositiveMoney
    send_currency: str
    receive_country: str
    payout_method: str
    recipient_wallet: str
    deadline_minutes: PositiveInt
    objective: str
    approval_required_above_fee: NonNegativeMoney


class RouteQuote(_Model):
    """A provider's quote.

    The disclosure fields are optional so that an incomplete quote reaches the
    eligibility gate and is excluded with a reason code, rather than failing
    validation with no record in the receipt.
    """

    quote_id: str
    provider: str
    timestamp: AwareDatetime
    send_amount: PositiveMoney
    fee: NonNegativeMoney | None = None
    fx_rate: PositiveMoney | None = None
    recipient_amount: PositiveMoney | None = None
    delivery_minutes: Annotated[int, Field(ge=0)] | None = None
    quote_expiry_seconds: Annotated[int, Field(ge=0)]
    payout_method: str
    data_mode: Literal["live", "sandbox", "synthetic"]


class ExcludedRoute(_Model):
    provider: str
    reason: ReasonCode


class DecisionReceipt(_Model):
    mandate_hash: str
    candidate_set: list[RouteQuote]
    excluded_routes: list[ExcludedRoute]
    eligible_routes: list[str]
    selected_route: str | None
    delivered_value_score: float | None
    scores: dict[str, float]
    mandate_compliant: bool
    disclosure_passed: bool
    quote_rechecked_at_commit: bool
    user_approved: bool
    execution: dict[str, Any] | None
    evidence_refs: list[str]
    explanation: str | None
    explanation_source: Literal["ai", "template"] | None = None
