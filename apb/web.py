"""Local web UI: python -m apb.web

Serves on localhost only. The browser gets decisions and results, never
credentials, and it can only ask to execute a decision the server already made.
"""

import os
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from apb.benchmark import CURRENCIES, ROOT, run_benchmark
from apb.check_api import (
    NOTICE,
    CheckError,
    CheckRequest,
    RecheckRequest,
    check,
    reason_codes,
    recheck,
)
from apb.decide import decide
from apb.demo import FIXTURE_CURRENCY, fresh_mandate, load_mandate, load_quotes
from apb.execute import execute
from apb.explain import with_explanation
from apb.models import DecisionReceipt, PaymentMandate
from apb.parser import parse_mandate

HOST = "127.0.0.1"
PORT = 8000
MAX_STORED_DECISIONS = 100
RECEIVE_CURRENCIES = CURRENCIES

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


API_DESCRIPTION = (
    "Deterministic decision support for an agent about to pay. Send the mandate "
    "and every quote, and APB says pay, pause or stop, with reason codes and a "
    "receipt. No AI runs in this path, nothing is stored, and nothing executes. "
    f"**{NOTICE}**"
)

app = FastAPI(
    title="APB Value Guard, built on PayPal",
    description=API_DESCRIPTION,
    version="apb-0.1",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
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


@app.get("/docs/METHOD.md", response_class=PlainTextResponse, include_in_schema=False)
def method() -> str:
    return METHOD_PATH.read_text("utf-8")


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config", include_in_schema=False)
def config() -> dict[str, bool]:
    return {"allow_execute": execute_allowed(), "public_demo": public_demo()}


@app.get("/api/benchmark", include_in_schema=False)
def benchmark() -> dict[str, Any]:
    return run_benchmark()


@app.post("/api/decide", include_in_schema=False)
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


@app.post("/api/execute", include_in_schema=False)
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


# Check API ------------------------------------------------------------------

API_PREFIX = "/v1/"
MAX_BODY_BYTES = 64 * 1024
RATE_LIMIT = 60
RATE_WINDOW_SECONDS = 60.0

# Request times per client address, for the rate limit only. No request data.
_hits: dict[str, deque[float]] = {}
_hits_lock = threading.Lock()


def _client_address(request: Request) -> str:
    # Render's proxy adds the caller's address last, so that entry is trusted.
    forwarded = request.headers.get("x-forwarded-for")
    if public_demo() and forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _within_rate_limit(address: str) -> bool:
    now = time.monotonic()
    with _hits_lock:
        if len(_hits) > 10_000:
            _hits.clear()
        hits = _hits.setdefault(address, deque())
        while hits and now - hits[0] > RATE_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            return False
        hits.append(now)
        return True


def _api_error(status: int, detail: Any) -> JSONResponse:
    return JSONResponse({"detail": detail, "notice": NOTICE}, status_code=status)


@app.middleware("http")
async def guard_the_api(request: Request, call_next: Any) -> Any:
    if not request.url.path.startswith(API_PREFIX):
        return await call_next(request)

    response = None
    if request.method == "POST":
        length = request.headers.get("content-length")
        if length is None:
            response = _api_error(411, "Send a Content-Length header.")
        elif not length.isdigit() or int(length) > MAX_BODY_BYTES:
            response = _api_error(413, f"The body is over {MAX_BODY_BYTES} bytes.")
    if response is None and not _within_rate_limit(_client_address(request)):
        response = _api_error(429, f"Over {RATE_LIMIT} requests a minute. Try again soon.")
        response.headers["Retry-After"] = str(int(RATE_WINDOW_SECONDS))
    if response is None:
        response = await call_next(request)
    response.headers["X-APB-Notice"] = NOTICE
    return response


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    """Report what's wrong without echoing the values, which could hold a wallet."""
    detail = [
        {"loc": list(item.get("loc", ())), "msg": item.get("msg"), "type": item.get("type")}
        for item in error.errors()
    ]
    if request.url.path.startswith(API_PREFIX):
        return _api_error(422, detail)
    return JSONResponse({"detail": detail}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def api_http_error(request: Request, error: StarletteHTTPException) -> Any:
    if request.url.path.startswith(API_PREFIX):
        return _api_error(error.status_code, error.detail)
    return await http_exception_handler(request, error)


def _examples() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the worked examples from the fixtures, so they always run."""
    from datetime import timedelta

    case_a = load_mandate("a").model_dump(mode="json", exclude_none=True)
    case_b = load_mandate("b").model_dump(mode="json", exclude_none=True)
    quotes = [quote.model_dump(mode="json") for quote in load_quotes()]
    no_match = {
        "result": "no_match",
        "expected_name": "Jane Sample Wanjiku",
        "returned_name": "Peter Example Mwangi",
        "source": "caller_supplied",
    }
    checks = {
        "case_a": {
            "summary": "Case A: 2-hour deadline, so PayPal is the best eligible route",
            "value": {"mandate": case_a, "quotes": quotes},
        },
        "case_b": {
            "summary": "Case B: 2-day deadline, so another provider delivers more",
            "value": {"mandate": case_b, "quotes": quotes},
        },
        "payee_no_match": {
            "summary": "Payee no_match: stop, even though the user confirmed",
            "value": {
                "mandate": case_a,
                "quotes": quotes,
                "context": {"payee_check": no_match, "user_confirmed_payee": True},
            },
        },
    }
    receipt = check(CheckRequest.model_validate(checks["case_a"]["value"]))["receipt"]
    later = load_quotes()[0].timestamp + timedelta(minutes=20)
    rechecks = {
        "quote_expired": {
            "summary": "The same quotes 20 minutes later: expired, so re-decide",
            "value": {
                "receipt": receipt,
                "mandate": case_a,
                "quotes": quotes,
                "now": later.isoformat(),
            },
        }
    }
    return checks, rechecks


CHECK_EXAMPLES, RECHECK_EXAMPLES = _examples()


@app.post(
    "/v1/check",
    tags=["Check API"],
    summary="Check a payment before making it",
    description=f"Returns pay, pause or stop with reason codes and a receipt. {NOTICE}",
)
def v1_check(
    request: Annotated[CheckRequest, Body(openapi_examples=CHECK_EXAMPLES)],
) -> dict[str, Any]:
    try:
        return check(request)
    except CheckError as error:
        raise HTTPException(422, str(error)) from None


@app.post(
    "/v1/recheck",
    tags=["Check API"],
    summary="Recheck an earlier decision just before paying",
    description=(
        "Checks the earlier receipt's route against fresh quotes at `now`. Returns ok, "
        "or re-decide with the reason (quote_expired, rate_worse, "
        f"route_no_longer_eligible, route_not_quoted) and a new decision. {NOTICE}"
    ),
)
def v1_recheck(
    request: Annotated[RecheckRequest, Body(openapi_examples=RECHECK_EXAMPLES)],
) -> dict[str, Any]:
    try:
        return recheck(request)
    except CheckError as error:
        raise HTTPException(422, str(error)) from None


@app.get(
    "/v1/reason-codes",
    tags=["Check API"],
    summary="Every reason code, its scope and meaning",
)
def v1_reason_codes() -> dict[str, Any]:
    return reason_codes()


def main() -> None:
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
