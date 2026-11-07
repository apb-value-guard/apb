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
    SCREENING_HOLD = "screening_hold"
    KYC_NOT_VERIFIED = "kyc_not_verified"
    PAYEE_MISMATCH = "payee_mismatch"


SCREENING_STOP_MESSAGE = "Payment stopped: screening hold (simulated)"
PAYEE_STOP_MESSAGE = "Payment stopped: payee name mismatch (simulated)"
PAYEE_CONFIRM_MESSAGE = "Confirmation needed: payee name close match (simulated)"


class ScreeningResult(_Model):
    """A screening outcome supplied as input. APB does not screen anyone."""

    status: Literal["clear", "hold"]
    list: Literal["sanctions", "pep", "aml"] | None = None
    # "caller_supplied" is a result an API caller got from its own provider.
    source: Literal["simulated", "caller_supplied"]


class PayeeCheck(_Model):
    """A payee name check result supplied as input. APB runs no such check."""

    result: Literal["match", "close_match", "no_match"]
    expected_name: str
    returned_name: str
    source: Literal["simulated", "caller_supplied"]


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
    screening_result: ScreeningResult | None = None
    sender_verified_for: list[str] | None = None
    payee_check: PayeeCheck | None = None


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
    # True if the route pays a referral. Disclosed, and never used in scoring.
    sponsored: bool = False
    requires_kyc: bool = False


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
    # Defaults let a receipt come back from a client that drops null values.
    execution: dict[str, Any] | None = None
    evidence_refs: list[str]
    explanation: str | None = None
    explanation_source: Literal["ai", "template"] | None = None
    sponsored_routes: list[str] = []
    screening_result: ScreeningResult | None = None
    stop_reason: str | None = None
    payee_check: PayeeCheck | None = None
    confirmation_reason: str | None = None
