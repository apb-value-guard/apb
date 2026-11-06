"""Commit a decision. Deterministic code only: the AI plays no part in paying."""

from datetime import datetime
from typing import Any

from apb.decide import mandate_hash
from apb.eligibility import exclusion_reason
from apb.models import DecisionReceipt, PaymentMandate
from apb.paypal import (
    PayPalDuplicate,
    PayPalError,
    PayPalOutcomeUnknown,
    create_payout,
    selected_quote,
    sender_batch_id,
)

SANDBOX_NOTE = (
    "PayPal sandbox payout in USD, a stand-in for M-PESA delivery. No real money "
    "moved, and the KES amounts in this receipt are synthetic."
)

_FAILED_BATCH_STATUSES = {"DENIED", "CANCELED"}
_FAILED_ITEM_STATUSES = {"FAILED", "BLOCKED", "RETURNED", "REFUNDED", "REVERSED"}


def _stopped(status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "provider": "paypal",
        "executed": False,
    }


def _pay(receipt: DecisionReceipt, mandate: PaymentMandate) -> dict[str, Any]:
    try:
        payout = create_payout(receipt, mandate)
    except PayPalOutcomeUnknown as error:
        return {
            "status": "outcome_unknown",
            "reason": error.code,
            "provider": "paypal",
            "sender_batch_id": sender_batch_id(receipt),
            "executed": None,
        }
    except PayPalDuplicate as error:
        return {
            "status": "duplicate_rejected",
            "reason": error.code,
            "paypal_details": error.details,
            "provider": "paypal",
            "sender_batch_id": sender_batch_id(receipt),
            "executed": None,
            "note": "This run paid nothing. An earlier run used this batch id.",
        }
    except PayPalError as error:
        return {
            **_stopped("failed", error.code),
            "paypal_details": error.details,
            "sender_batch_id": sender_batch_id(receipt),
        }

    executed = (
        payout["batch_status"] not in _FAILED_BATCH_STATUSES
        and payout["item_status"] not in _FAILED_ITEM_STATUSES
    )
    return {
        "status": payout["batch_status"],
        "item_status": payout["item_status"],
        "payout_batch_id": payout["payout_batch_id"],
        "sender_batch_id": payout["sender_batch_id"],
        "duplicate_rerun": payout["duplicate"],
        "provider": "paypal",
        "data_mode": "sandbox",
        "executed": executed,
        "note": SANDBOX_NOTE,
    }


def execute(
    receipt: DecisionReceipt,
    mandate: PaymentMandate,
    now: datetime,
    approve: bool = False,
) -> DecisionReceipt:
    """Return the receipt with its execution fields filled.

    PayPal is called only when it is the selected route, the quote still passes
    the gate at `now`, and any required approval was given.
    """
    if mandate_hash(mandate) != receipt.mandate_hash:
        raise ValueError("the mandate does not match the receipt's mandate_hash")

    if receipt.selected_route != "paypal":
        status = "handoff_logged" if receipt.selected_route else "no_route"
        return receipt.model_copy(
            update={
                "execution": {
                    "status": status,
                    "provider": receipt.selected_route,
                    "executed": False,
                }
            }
        )

    quote = selected_quote(receipt)
    update: dict[str, Any] = {
        "quote_rechecked_at_commit": True,
        "user_approved": approve,
    }

    reason = exclusion_reason(mandate, quote, now)
    if reason is not None:
        update["execution"] = _stopped("blocked", reason.value)
    elif quote.fee > mandate.approval_required_above_fee and not approve:
        update["execution"] = _stopped("blocked", "approval_required")
    else:
        update["execution"] = _pay(receipt, mandate)
    return receipt.model_copy(update=update)
