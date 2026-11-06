"""Commit a decision. Deterministic code only: the AI plays no part in paying."""

from datetime import datetime
from typing import Any

from apb.decide import mandate_hash
from apb.eligibility import (
    exclusion_reason,
    hold_is_active,
    needs_payee_confirmation,
    payee_is_mismatched,
)
from apb.models import (
    PAYEE_CONFIRM_MESSAGE,
    PAYEE_STOP_MESSAGE,
    SCREENING_STOP_MESSAGE,
    DecisionReceipt,
    PaymentMandate,
)
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


def _hard_stop(reason: str, message: str) -> dict[str, Any]:
    return {
        "status": "stopped",
        "reason": reason,
        "provider": None,
        "executed": False,
        "note": message,
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
    payee_confirmed: bool | None = None,
) -> DecisionReceipt:
    """Return the receipt with its execution fields filled.

    PayPal is called only when it is the selected route, the quote still passes
    the gate at `now`, and any required approval was given.

    `approve` covers both the fee limit and a close payee match. Set
    `payee_confirmed` to confirm the name alone, leaving the fee limit in place.
    """
    if mandate_hash(mandate) != receipt.mandate_hash:
        raise ValueError("the mandate does not match the receipt's mandate_hash")

    # Checked first and on their own, so that no later step can pay through them.
    if hold_is_active(mandate):
        stop = _hard_stop("screening_hold", SCREENING_STOP_MESSAGE)
        return receipt.model_copy(update={"execution": stop})
    if payee_is_mismatched(mandate):
        stop = _hard_stop("payee_mismatch", PAYEE_STOP_MESSAGE)
        return receipt.model_copy(update={"execution": stop})

    confirmed = approve if payee_confirmed is None else payee_confirmed
    if needs_payee_confirmation(mandate) and not confirmed:
        check = mandate.payee_check
        return receipt.model_copy(
            update={
                "execution": {
                    "status": "needs_confirmation",
                    "reason": "payee_close_match",
                    "provider": receipt.selected_route,
                    "expected_name": check.expected_name,
                    "returned_name": check.returned_name,
                    "executed": False,
                    "note": PAYEE_CONFIRM_MESSAGE,
                }
            }
        )

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
        "user_approved": approve or confirmed,
    }

    reason = exclusion_reason(mandate, quote, now)
    if reason is not None:
        update["execution"] = _stopped("blocked", reason.value)
    elif quote.fee > mandate.approval_required_above_fee and not approve:
        update["execution"] = _stopped("blocked", "approval_required")
    else:
        update["execution"] = _pay(receipt, mandate)
    return receipt.model_copy(update=update)
