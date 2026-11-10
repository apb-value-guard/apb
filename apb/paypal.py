"""PayPal sandbox payouts. This module can only talk to the sandbox.

Credentials are read from the environment for one call and never stored. Errors
carry only short codes, so nothing PayPal returns can leak into a receipt or log.
"""

import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlsplit

from dotenv import load_dotenv

from apb.models import DecisionReceipt, PaymentMandate, RouteQuote

SANDBOX_HOST = "api-m.sandbox.paypal.com"
API_BASE = f"https://{SANDBOX_HOST}"
PAYOUT_CURRENCY = "USD"
TIMEOUT_SECONDS = 30

_ERROR_NAME = re.compile(r"[A-Z0-9_]{1,64}")
_BATCH_ID = re.compile(r"[A-Z0-9]{1,64}")
_BATCH_LINK = re.compile(r"/payouts/([A-Z0-9]{1,64})(?:[/?#]|$)")
_ISSUE = re.compile(r"[A-Z0-9_]{1,64}")
_LINK_REL = re.compile(r"[a-z_]{1,32}")


class PayPalError(Exception):
    """The payout did not happen. `code` is safe to store in a receipt."""

    def __init__(self, code: str, details: list[str] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or []


class PayPalOutcomeUnknown(PayPalError):
    """The request may have reached PayPal. Rerunning is safe: same batch id."""


class PayPalDuplicate(PayPalError):
    """PayPal rejected a reused batch id, without naming the original batch."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect could carry the Authorization header away from the sandbox."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def require_sandbox(url: str) -> None:
    """Refuse any URL that is not the PayPal sandbox API over HTTPS."""
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != SANDBOX_HOST
        or parts.port not in (None, 443)
        or parts.username
        or parts.password
    ):
        raise PayPalError("non_sandbox_url")


def _call(
    method: str, url: str, headers: dict[str, str], data: bytes | None = None
) -> tuple[int, dict[str, Any]]:
    require_sandbox(url)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
        error.close()
    except OSError:
        raise PayPalOutcomeUnknown("paypal_unreachable") from None
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    return status, body if isinstance(body, dict) else {}


def _error_code(body: dict[str, Any]) -> str:
    name = body.get("name")
    if isinstance(name, str) and _ERROR_NAME.fullmatch(name):
        return f"paypal_{name.lower()}"
    return "paypal_error"


def _safe_details(body: dict[str, Any]) -> list[str]:
    """Keep only PayPal's issue codes and link types. Free text is dropped."""
    found = []
    details = body.get("details")
    for detail in details if isinstance(details, list) else []:
        issue = detail.get("issue") if isinstance(detail, dict) else None
        if isinstance(issue, str) and _ISSUE.fullmatch(issue):
            found.append(issue)
    links = body.get("links")
    for link in links if isinstance(links, list) else []:
        rel = link.get("rel") if isinstance(link, dict) else None
        if isinstance(rel, str) and _LINK_REL.fullmatch(rel):
            found.append(f"link:{rel}")
    return found


def _access_token() -> str:
    load_dotenv()
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "").strip()
    secret = os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
    if not client_id or not secret:
        raise PayPalError("paypal_not_configured")

    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    try:
        status, body = _call(
            "POST",
            f"{API_BASE}/v1/oauth2/token",
            {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode({"grant_type": "client_credentials"}).encode(),
        )
    except PayPalOutcomeUnknown:
        # Nothing has been paid yet, so this is a plain failure.
        raise PayPalError("paypal_unreachable") from None
    token = body.get("access_token")
    if status != 200 or not isinstance(token, str) or not token:
        raise PayPalError("paypal_auth_failed")
    return token


def selected_quote(receipt: DecisionReceipt) -> RouteQuote:
    return next(
        quote
        for quote in receipt.candidate_set
        if quote.provider == receipt.selected_route
    )


def sender_batch_id(receipt: DecisionReceipt) -> str:
    """Derive the batch id, so the same decision can never pay twice."""
    seed = f"{receipt.mandate_hash}:{selected_quote(receipt).quote_id}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


SANDBOX_CURRENCIES = {"USD", "EUR"}


def _payout_body(
    batch_id: str,
    amount: Decimal,
    receiver: str,
    currency: str = PAYOUT_CURRENCY,
    note: str = "Sandbox stand-in for M-PESA delivery.",
) -> dict[str, Any]:
    return {
        "sender_batch_header": {
            "sender_batch_id": batch_id,
            "email_subject": "APB Guard sandbox payout",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "amount": {"value": f"{amount:.2f}", "currency": currency},
                "receiver": receiver,
                "note": note,
                "sender_item_id": batch_id[:32],
            }
        ],
    }


def _original_batch_id(body: dict[str, Any]) -> str | None:
    """Find the original batch in a duplicate rejection's links.

    PayPal rejects a reused sender_batch_id with a link to the original payout.
    Only the id is taken from the link. The URL itself is never followed.
    """
    links = body.get("links")
    for link in links if isinstance(links, list) else []:
        href = link.get("href") if isinstance(link, dict) else None
        match = _BATCH_LINK.search(href) if isinstance(href, str) else None
        if match:
            return match.group(1)
    return None


def _batch_status(batch_id: str, headers: dict[str, str]) -> tuple[str | None, str | None]:
    """Return the batch and item status. A failed lookup returns None for both."""
    try:
        status, body = _call(
            "GET", f"{API_BASE}/v1/payments/payouts/{batch_id}", headers
        )
    except PayPalError:
        return None, None
    if status != 200:
        return None, None
    header = body.get("batch_header")
    batch_status = header.get("batch_status") if isinstance(header, dict) else None
    items = body.get("items")
    first = items[0] if isinstance(items, list) and items else None
    item_status = first.get("transaction_status") if isinstance(first, dict) else None
    return (
        batch_status if isinstance(batch_status, str) else None,
        item_status if isinstance(item_status, str) else None,
    )


def create_payout(
    receipt: DecisionReceipt, mandate: PaymentMandate
) -> dict[str, Any]:
    """Send one sandbox payout for the mandate's send_amount, then fetch its status."""
    if receipt.selected_route != "paypal":
        raise PayPalError("route_not_paypal")
    if mandate.send_currency != PAYOUT_CURRENCY:
        raise PayPalError("currency_unsupported")
    return submit_payout(sender_batch_id(receipt), mandate.send_amount, PAYOUT_CURRENCY)


def submit_payout(
    batch_id: str,
    amount: Decimal,
    currency: str,
    note: str = "Sandbox stand-in for M-PESA delivery.",
) -> dict[str, Any]:
    """Send one sandbox payout under a caller-derived batch id, then fetch its status.

    The batch id is the idempotency key: PayPal rejects a reused one, and the
    original payout is reported instead of paying twice.
    """
    if currency not in SANDBOX_CURRENCIES:
        raise PayPalError("currency_unsupported")
    if amount != amount.quantize(Decimal("0.01")) or amount <= 0:
        raise PayPalError("amount_invalid")
    if not _BATCH_ID.fullmatch(batch_id.upper()) and not re.fullmatch(r"[a-z0-9-]{1,64}", batch_id):
        raise PayPalError("batch_id_invalid")

    load_dotenv()
    receiver = os.environ.get("PAYPAL_SANDBOX_RECEIVER", "").strip()
    if not receiver:
        raise PayPalError("paypal_not_configured")

    token = _access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    status, body = _call(
        "POST",
        f"{API_BASE}/v1/payments/payouts",
        headers,
        json.dumps(_payout_body(batch_id, amount, receiver, currency, note)).encode(),
    )

    duplicate = False
    if status == 201:
        header = body.get("batch_header")
        payout_id = header.get("payout_batch_id") if isinstance(header, dict) else None
        created_status = header.get("batch_status") if isinstance(header, dict) else None
        if not isinstance(payout_id, str) or not _BATCH_ID.fullmatch(payout_id):
            raise PayPalOutcomeUnknown("paypal_invalid_response")
    elif status >= 500:
        raise PayPalOutcomeUnknown(_error_code(body))
    else:
        payout_id = _original_batch_id(body)
        if payout_id is None:
            # PayPal's duplicate error shows the reused sender_batch_id.
            if batch_id in json.dumps(body):
                raise PayPalDuplicate(_error_code(body), _safe_details(body))
            raise PayPalError(_error_code(body), _safe_details(body))
        duplicate, created_status = True, None

    batch_status, item_status = _batch_status(payout_id, headers)
    return {
        "payout_batch_id": payout_id,
        "sender_batch_id": batch_id,
        "batch_status": batch_status or created_status or "SUBMITTED",
        "item_status": item_status,
        "duplicate": duplicate,
    }
