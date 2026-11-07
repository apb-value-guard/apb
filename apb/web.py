"""Local web UI: python -m apb.web

Serves on localhost only. The browser gets decisions and results, never
credentials, and it can only ask to execute a decision the server already made.
"""

import os
import re
import secrets
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from apb.benchmark import ROOT, run_benchmark
from apb.decide import decide
from apb.demo import FIXTURE_CURRENCY, fresh_mandate, load_mandate, load_quotes
from apb.execute import execute
from apb.explain import with_explanation
from apb.models import DecisionReceipt, PaymentMandate
from apb.parser import parse_mandate

HOST = "127.0.0.1"
PORT = 8000
MAX_STORED_DECISIONS = 100
RECEIVE_CURRENCIES = {"KE": "KES"}

STATIC = Path(__file__).resolve().parent / "static"

LOCAL_FOOTER = (
    "All quotes are synthetic. Payouts run in PayPal sandbox. No real money moves."
)
PUBLIC_FOOTER = (
    "All quotes are synthetic. No payments run on this public demo; PayPal sandbox "
    "payouts run locally (see the video)."
)
_BANNER_HIDDEN = 'id="public-banner" role="note" hidden>'
_BANNER_SHOWN = 'id="public-banner" role="note">'
METHOD_PATH = ROOT / "docs" / "METHOD.md"

def public_demo() -> bool:
    """True on the public deploy, which never executes and shows a banner."""
    load_dotenv()
    return os.environ.get("PUBLIC_DEMO", "").strip().lower() == "true"


app = FastAPI(
    title="APB Value Guard, built on PayPal",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# A rebinding attack reaches the local server under a foreign Host header. The
# public demo is reached by its own hostname and can't execute, so it skips this.
if not public_demo():
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", HOST])

# Execution uses the stored decision, so the browser can't supply a receipt.
_decisions: OrderedDict[str, tuple[PaymentMandate, DecisionReceipt]] = OrderedDict()


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case: Literal["a", "b", "c", "d", "e"] | None = None
    text: str | None = Field(default=None, max_length=1000)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(max_length=64)
    confirm_payee: bool = False


def execute_allowed() -> bool:
    load_dotenv()
    if public_demo():
        return False
    return os.environ.get("ALLOW_EXECUTE", "").strip().lower() == "true"


def _store(mandate: PaymentMandate, receipt: DecisionReceipt) -> str:
    decision_id = secrets.token_urlsafe(16)
    _decisions[decision_id] = (mandate, receipt)
    while len(_decisions) > MAX_STORED_DECISIONS:
        _decisions.popitem(last=False)
    return decision_id


def mask_wallet(wallet: str) -> str:
    """Keep only the last 4 digits. The full wallet never reaches the browser."""
    return f"\u2022\u2022\u2022\u2022 {re.sub(r'[^0-9]', '', wallet)[-4:]}"


def _request_view(
    mandate: PaymentMandate, source: Literal["ai", "rules", "fixture"]
) -> dict[str, Any]:
    return {
        "source": source,
        "amount": f"{mandate.send_amount:.2f}",
        "currency": mandate.send_currency,
        "payout_method": mandate.payout_method,
        "wallet": mask_wallet(mandate.recipient_wallet),
        "deadline_minutes": mandate.deadline_minutes,
    }


def _decision_response(
    mandate: PaymentMandate, source: Literal["ai", "rules", "fixture"]
) -> dict[str, Any]:
    # With execution on, a decision is live: fresh mandate, current quotes.
    stamp = datetime.now(UTC) if execute_allowed() else None
    if stamp:
        mandate = fresh_mandate(mandate, stamp)
    receipt = decide(mandate, load_quotes(stamp), now=stamp)
    receipt = with_explanation(receipt, mandate)
    return {
        "status": "ok",
        "decision_id": _store(mandate, receipt),
        "mandate": mandate.model_dump(mode="json", exclude={"recipient_wallet"}),
        "request": _request_view(mandate, source),
        "receive_currency": RECEIVE_CURRENCIES[mandate.receive_country],
        "receipt": receipt.model_dump(mode="json"),
    }


def render_page(public: bool) -> str:
    """Return the page. Public mode shows its banner and footer in the HTML itself."""
    page = (STATIC / "index.html").read_text("utf-8")
    if not public:
        return page
    if _BANNER_HIDDEN not in page or LOCAL_FOOTER not in page:
        raise RuntimeError("index.html no longer has the public banner or footer")
    return page.replace(_BANNER_HIDDEN, _BANNER_SHOWN).replace(LOCAL_FOOTER, PUBLIC_FOOTER)


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(render_page(public_demo()))


@app.get("/docs/METHOD.md", response_class=PlainTextResponse)
def method() -> str:
    return METHOD_PATH.read_text("utf-8")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, bool]:
    return {"allow_execute": execute_allowed(), "public_demo": public_demo()}


@app.get("/api/benchmark")
def benchmark() -> dict[str, Any]:
    return run_benchmark()


@app.post("/api/decide")
def run_decide(request: DecideRequest) -> dict[str, Any]:
    if (request.case is None) == (request.text is None):
        raise HTTPException(422, "Send either a case or some text.")
    if request.case is not None:
        return _decision_response(load_mandate(request.case), "fixture")

    result = parse_mandate(request.text)
    if result.status != "ok":
        return {
            "status": result.status,
            "message": result.message,
            "missing_fields": [item.model_dump() for item in result.missing_fields],
        }
    if result.mandate.send_currency != FIXTURE_CURRENCY:
        return {
            "status": "out_of_scope",
            "message": (
                f"Parsed a {result.mandate.send_currency} mandate, but the fixture "
                f"routes are {FIXTURE_CURRENCY} only, so no decision was made."
            ),
            "missing_fields": [],
        }
    return _decision_response(result.mandate, "rules" if result.parser == "rules" else "ai")


@app.post("/api/execute")
def run_execute(request: ExecuteRequest) -> dict[str, Any]:
    if not execute_allowed():
        raise HTTPException(403, "Execution is off. Set ALLOW_EXECUTE=true to allow it.")
    stored = _decisions.get(request.decision_id)
    if stored is None:
        raise HTTPException(404, "Unknown decision. Run the decision again.")

    mandate, receipt = stored
    if receipt.execution is None or receipt.execution.get("executed") is not True:
        # Confirming the name approves nothing else, such as a fee over the limit.
        receipt = execute(
            receipt,
            mandate,
            datetime.now(UTC),
            payee_confirmed=request.confirm_payee,
        )
        _decisions[request.decision_id] = (mandate, receipt)
    return {
        "execution": receipt.execution,
        "receipt": receipt.model_dump(mode="json"),
    }


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
