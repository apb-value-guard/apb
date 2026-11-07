import importlib
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient

from apb import check_api, web
from apb.check_api import NOTICE, CheckRequest, check, wallet_ref
from apb.decide import decide
from apb.demo import load_mandate, load_quotes
from apb.models import ReasonCode

ROOT = Path(__file__).resolve().parent.parent
WALLET = "254700000001"
HOLD = {"status": "hold", "list": "sanctions", "source": "caller_supplied"}


def payee(result, returned="J Sample Wanjiku"):
    return {
        "result": result,
        "expected_name": "Jane Sample Wanjiku",
        "returned_name": returned,
        "source": "caller_supplied",
    }


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr("apb.web.load_dotenv", lambda: False)
    monkeypatch.setattr("apb.ai.load_dotenv", lambda: False)
    monkeypatch.setattr("apb.paypal.load_dotenv", lambda: False)
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.setenv("AI_BACKEND", "none")
    web._hits.clear()

    def refuse(*args, **kwargs):
        raise AssertionError("the Check API reached AI or PayPal")

    monkeypatch.setattr("apb.ai.subprocess.run", refuse)
    monkeypatch.setattr("apb.ai.Anthropic", refuse)
    monkeypatch.setattr("apb.paypal._opener", type("Refuse", (), {"open": refuse})())


@pytest.fixture
def client():
    return TestClient(web.app, base_url="http://localhost")


def body(case="a", wallet=WALLET, **extra):
    mandate = load_mandate(case).model_copy(update={"recipient_wallet": wallet})
    return {
        "mandate": mandate.model_dump(mode="json", exclude_none=True),
        "quotes": [quote.model_dump(mode="json") for quote in load_quotes()],
        **extra,
    }


def post_check(client, payload):
    response = client.post("/v1/check", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# Decisions


def test_case_a_pays_paypal(client):
    data = post_check(client, body("a"))

    assert data["decision"] == "pay"
    assert data["decision_reason"] == "best_eligible_route"
    assert data["selected_route"] == "paypal"
    assert data["excluded_routes"] == [
        {"provider": "alternative_provider", "reason": "deadline_violation"}
    ]
    assert data["scores"] == {"paypal": 100.0, "zero_fee_provider": 98.9}
    assert data["delivered_value_score"] == 100.0
    assert data["method_version"] == "apb-0.1"
    assert data["notice"] == NOTICE
    assert len(data["receipt"]["mandate_hash"]) == 64


def test_case_b_pays_another_provider(client):
    data = post_check(client, body("b"))

    assert data["decision"] == "pay"
    assert data["selected_route"] == "alternative_provider"


def test_screening_hold_stops(client):
    data = post_check(client, body(context={"screening_result": HOLD}))

    assert data["decision"] == "stop"
    assert data["decision_reason"] == "screening_hold"
    assert data["selected_route"] is None


def test_payee_close_match_pauses(client):
    data = post_check(client, body(context={"payee_check": payee("close_match")}))

    assert data["decision"] == "pause"
    assert data["decision_reason"] == "payee_close_match"
    assert data["selected_route"] == "paypal"


def test_confirmed_close_match_pays(client):
    context = {"payee_check": payee("close_match"), "user_confirmed_payee": True}
    assert post_check(client, body(context=context))["decision"] == "pay"


def test_no_match_stops_even_if_the_user_confirmed(client):
    context = {
        "payee_check": payee("no_match", "Peter Example Mwangi"),
        "user_confirmed_payee": True,
        "user_approved_fee": True,
    }
    data = post_check(client, body(context=context))

    assert data["decision"] == "stop"
    assert data["decision_reason"] == "payee_mismatch"
    assert {r["reason"] for r in data["excluded_routes"]} == {"payee_mismatch"}


def test_fee_above_the_limit_pauses_until_approved(client):
    payload = body()
    payload["mandate"]["approval_required_above_fee"] = "1.00"

    assert post_check(client, payload)["decision_reason"] == "approval_required"
    payload["context"] = {"user_approved_fee": True}
    assert post_check(client, payload)["decision"] == "pay"


def test_no_eligible_route_stops(client):
    payload = body()
    payload["mandate"]["payout_method"] = "bank"

    data = post_check(client, payload)

    assert data["decision"] == "stop"
    assert data["decision_reason"] == "no_eligible_route"


def test_kyc_context_is_applied(client):
    payload = body("b", context={"sender_verified_for": ["paypal"]})
    for quote in payload["quotes"]:
        quote["requires_kyc"] = quote["provider"] != "zero_fee_provider"

    data = post_check(client, payload)

    assert data["selected_route"] == "paypal"
    assert data["excluded_routes"] == [
        {"provider": "alternative_provider", "reason": "kyc_not_verified"}
    ]


def test_matches_the_engine_exactly(client):
    data = post_check(client, body("a"))
    mandate = load_mandate("a").model_copy(update={"recipient_wallet": WALLET})
    receipt = decide(mandate, load_quotes())

    assert data["receipt"]["mandate_hash"] == receipt.mandate_hash
    assert data["scores"] == receipt.scores


def test_callers_data_mode_labels_are_kept(client):
    payload = body()
    for quote in payload["quotes"]:
        quote["data_mode"] = "live"

    data = post_check(client, payload)

    assert {q["data_mode"] for q in data["receipt"]["candidate_set"]} == {"live"}


def test_context_that_contradicts_the_mandate_is_refused(client):
    payload = body("e")
    payload["context"] = {"payee_check": payee("match", "Jane Sample Wanjiku")}

    response = client.post("/v1/check", json=payload)

    assert response.status_code == 422
    assert "differently" in response.json()["detail"]


def test_duplicate_providers_are_refused(client):
    payload = body()
    payload["quotes"].append(payload["quotes"][0])

    assert client.post("/v1/check", json=payload).status_code == 422


@pytest.mark.parametrize("source", ["simulated", "caller_supplied"])
def test_screening_sources_accepted(client, source):
    context = {"screening_result": {**HOLD, "source": source}}
    assert post_check(client, body(context=context))["decision"] == "stop"


def test_unlabelled_screening_result_is_refused(client):
    context = {"screening_result": {"status": "hold", "list": "sanctions"}}
    assert client.post("/v1/check", json=body(context=context)).status_code == 422


# Recheck


def recheck_body(client, minutes_later, quotes=None, case="a"):
    earlier = post_check(client, body(case))
    now = load_quotes()[0].timestamp + timedelta(minutes=minutes_later)
    return {
        "receipt": earlier["receipt"],
        "mandate": body(case)["mandate"],
        "quotes": quotes if quotes is not None else body(case)["quotes"],
        "now": now.isoformat(),
    }


def post_recheck(client, payload):
    response = client.post("/v1/recheck", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_recheck_with_an_expired_quote_re_decides(client):
    data = post_recheck(client, recheck_body(client, 20))

    assert data["status"] == "re-decide"
    assert data["reason"] == "quote_expired"
    assert data["decision"]["decision"] == "stop"
    assert data["decision"]["decision_reason"] == "no_eligible_route"


def test_recheck_with_fresh_valid_quotes_is_ok(client):
    data = post_recheck(client, recheck_body(client, 5))

    assert data["status"] == "ok"
    assert data["decision"] is None
    assert data["notice"] == NOTICE


def test_recheck_with_a_worse_rate_re_decides(client):
    quotes = body()["quotes"]
    paypal = next(q for q in quotes if q["provider"] == "paypal")
    paypal["recipient_amount"] = "25000"

    data = post_recheck(client, recheck_body(client, 5, quotes))

    assert data["status"] == "re-decide"
    assert data["reason"] == "rate_worse"
    assert (data["earlier_recipient_amount"], data["fresh_recipient_amount"]) == ("25709", "25000")
    assert data["decision"]["selected_route"] == "zero_fee_provider"


def test_recheck_when_the_route_is_no_longer_eligible(client):
    quotes = body()["quotes"]
    next(q for q in quotes if q["provider"] == "paypal")["delivery_minutes"] = 999

    data = post_recheck(client, recheck_body(client, 5, quotes))

    assert data["reason"] == "route_no_longer_eligible"
    assert data["gate_reason"] == "deadline_violation"


def test_recheck_when_the_route_is_missing(client):
    quotes = [q for q in body()["quotes"] if q["provider"] != "paypal"]

    data = post_recheck(client, recheck_body(client, 5, quotes))

    assert data["reason"] == "route_not_quoted"
    assert data["decision"]["selected_route"] == "zero_fee_provider"


def test_recheck_refuses_a_different_mandate(client):
    payload = recheck_body(client, 5)
    payload["mandate"] = body("b")["mandate"]

    response = client.post("/v1/recheck", json=payload)

    assert response.status_code == 422
    assert "mandate_hash" in response.json()["detail"]


def test_recheck_with_no_earlier_route(client):
    payload = body(context={"screening_result": HOLD})
    earlier = post_check(client, payload)
    now = load_quotes()[0].timestamp + timedelta(minutes=5)

    data = post_recheck(
        client,
        {**payload, "receipt": earlier["receipt"], "now": now.isoformat()},
    )

    assert data["status"] == "ok"
    assert data["reason"] == "no_route_to_recheck"


def test_recheck_needs_an_aware_time(client):
    payload = recheck_body(client, 5)
    payload["now"] = "2026-11-06T12:05:00"

    assert client.post("/v1/recheck", json=payload).status_code == 422


# Determinism


@pytest.mark.parametrize("case", ["a", "b", "e"])
def test_identical_inputs_give_identical_responses(client, case):
    first = client.post("/v1/check", json=body(case))
    second = client.post("/v1/check", json=body(case))

    assert first.content == second.content


def test_identical_rechecks_give_identical_responses(client):
    payload = recheck_body(client, 20)
    assert client.post("/v1/recheck", json=payload).content == client.post(
        "/v1/recheck", json=payload
    ).content


# Wallets and secrets


def test_no_raw_wallet_number_in_any_response(client):
    bad = body()
    bad["quotes"][0]["fee"] = "not a number"
    bad_wallet = body()
    bad_wallet["mandate"]["deadline_minutes"] = WALLET

    responses = [
        client.post("/v1/check", json=body()),
        client.post("/v1/check", json=body(context={"payee_check": payee("close_match")})),
        client.post("/v1/check", json=bad),
        client.post("/v1/check", json=bad_wallet),
        client.post("/v1/recheck", json=recheck_body(client, 20)),
        client.get("/v1/reason-codes"),
    ]

    for response in responses:
        assert WALLET not in response.text, response.text


def test_validation_errors_do_not_echo_values(client):
    payload = body()
    payload["mandate"]["deadline_minutes"] = "secret-value-123"

    response = client.post("/v1/check", json=payload)

    assert response.status_code == 422
    assert "secret-value-123" not in response.text
    assert response.json()["notice"] == NOTICE


def test_wallet_reference_is_a_keyed_hash():
    first = wallet_ref("+254 700 000 001")

    assert first == wallet_ref("254700000001")
    assert first != wallet_ref("254700000002")
    assert first.startswith("hmac-sha256:")
    assert WALLET not in first


def test_wallet_reference_depends_on_the_key(monkeypatch):
    before = wallet_ref(WALLET)
    monkeypatch.setattr(check_api, "_WALLET_KEY", b"another key")
    assert wallet_ref(WALLET) != before


def test_check_api_never_calls_ai_or_paypal(client):
    post_check(client, body(context={"payee_check": payee("close_match")}))
    post_recheck(client, recheck_body(client, 20))


# Safety limits and the notice


def test_every_api_response_carries_the_notice(client):
    responses = [
        client.post("/v1/check", json=body()),
        client.post("/v1/check", json={"quotes": []}),
        client.get("/v1/reason-codes"),
        client.get("/v1/unknown"),
    ]

    for response in responses:
        assert response.headers["x-apb-notice"] == NOTICE
        assert response.json()["notice"] == NOTICE


def test_oversized_body_is_refused(client):
    payload = body()
    payload["mandate"]["objective"] = "x" * (web.MAX_BODY_BYTES + 1)

    response = client.post("/v1/check", json=payload)

    assert response.status_code == 413
    assert response.headers["x-apb-notice"] == NOTICE


def test_body_without_a_length_is_refused(client):
    def chunks():
        yield json.dumps(body()).encode()

    response = client.post(
        "/v1/check", content=chunks(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 411


def test_too_many_quotes_are_refused(client):
    payload = body()
    quote = payload["quotes"][0]
    payload["quotes"] = [
        {**quote, "provider": f"p{i}", "quote_id": f"q{i}"} for i in range(51)
    ]

    assert client.post("/v1/check", json=payload).status_code == 422


def test_rate_limit_per_address(client, monkeypatch):
    monkeypatch.setattr(web, "RATE_LIMIT", 3)

    statuses = [client.get("/v1/reason-codes").status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]


def test_rate_limit_does_not_apply_to_the_web_ui(client, monkeypatch):
    monkeypatch.setattr(web, "RATE_LIMIT", 1)

    assert [client.get("/healthz").status_code for _ in range(3)] == [200, 200, 200]


def test_public_demo_limits_by_the_address_render_adds(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    request = type(
        "R",
        (),
        {"headers": {"x-forwarded-for": "6.6.6.6, 203.0.113.7"}, "client": None},
    )()

    assert web._client_address(request) == "203.0.113.7"


# Reason codes and docs


def test_reason_codes_cover_every_code_with_a_scope(client):
    data = client.get("/v1/reason-codes").json()
    codes = {item["code"]: item for item in data["reason_codes"]}

    assert set(codes) == {code.value for code in ReasonCode}
    assert codes["screening_hold"]["scope"] == "all"
    assert codes["payee_mismatch"]["scope"] == "all"
    assert codes["quote_expired"]["scope"] == "route"
    assert all(item["meaning"] for item in data["reason_codes"])


def test_docs_have_a_worked_example_per_endpoint(client):
    spec = client.get("/openapi.json").json()

    check_examples = spec["paths"]["/v1/check"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    recheck_examples = spec["paths"]["/v1/recheck"]["post"]["requestBody"]["content"][
        "application/json"
    ]["examples"]
    assert set(check_examples) == {"case_a", "case_b", "payee_no_match"}
    assert set(recheck_examples) == {"quote_expired"}
    assert NOTICE in spec["info"]["description"]


@pytest.mark.parametrize(
    "name, decision, route",
    [("case_a", "pay", "paypal"), ("case_b", "pay", "alternative_provider"),
     ("payee_no_match", "stop", None)],
)
def test_docs_examples_give_what_they_say(client, name, decision, route):
    data = post_check(client, web.CHECK_EXAMPLES[name]["value"])

    assert (data["decision"], data["selected_route"]) == (decision, route)


@pytest.mark.parametrize(
    "path, name",
    [("/v1/check", "case_a"), ("/v1/check", "case_b"), ("/v1/check", "payee_no_match"),
     ("/v1/recheck", "quote_expired")],
)
def test_examples_work_exactly_as_the_docs_serve_them(client, path, name):
    spec = client.get("/openapi.json").json()
    served = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"][
        "examples"
    ][name]["value"]

    assert client.post(path, json=served).status_code == 200


def test_a_receipt_without_null_fields_is_accepted(client):
    payload = recheck_body(client, 20)
    payload["receipt"] = {k: v for k, v in payload["receipt"].items() if v is not None}

    assert post_recheck(client, payload)["reason"] == "quote_expired"


def test_recheck_docs_example_re_decides(client):
    data = post_recheck(client, web.RECHECK_EXAMPLES["quote_expired"]["value"])
    assert data["reason"] == "quote_expired"


# Public demo


def test_api_works_on_the_public_demo(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    reloaded = importlib.reload(web)
    try:
        monkeypatch.setattr(reloaded, "load_dotenv", lambda: False)
        client = TestClient(reloaded.app, base_url="http://apb-value-guard.onrender.com")

        assert client.get("/docs").status_code == 200
        assert client.get("/v1/reason-codes").status_code == 200
        assert client.post("/v1/check", json=body()).json()["decision"] == "pay"
    finally:
        os.environ.pop("PUBLIC_DEMO", None)
        importlib.reload(web)


# MCP


def test_mcp_server_exposes_both_tools_and_answers(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "apb.mcp_server"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT), "AI_BACKEND": "none"},
    )

    def content(result):
        structured = getattr(result, "structured_content", None) or getattr(
            result, "structuredContent", None
        )
        if structured:
            return structured.get("result", structured)
        return json.loads(result.content[0].text)

    async def session_calls():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                checked = content(await session.call_tool("apb_check", body("a")))
                no_match = content(
                    await session.call_tool(
                        "apb_check",
                        body(
                            context={
                                "payee_check": payee("no_match", "Peter Example Mwangi"),
                                "user_confirmed_payee": True,
                            }
                        ),
                    )
                )
                now = load_quotes()[0].timestamp + timedelta(minutes=20)
                rechecked = content(
                    await session.call_tool(
                        "apb_recheck",
                        {
                            "receipt": checked["receipt"],
                            "mandate": body("a")["mandate"],
                            "quotes": body("a")["quotes"],
                            "now": now.isoformat(),
                        },
                    )
                )
                return tools, checked, no_match, rechecked

    tools, checked, no_match, rechecked = anyio.run(session_calls)

    assert tools == {"apb_check", "apb_recheck"}
    assert (checked["decision"], checked["selected_route"]) == ("pay", "paypal")
    assert checked["notice"] == NOTICE
    assert no_match["decision"] == "stop"
    assert rechecked["reason"] == "quote_expired"
    assert WALLET not in json.dumps([checked, no_match, rechecked])


def test_mcp_tools_call_the_same_function_as_the_api(client):
    from apb.mcp_server import apb_check

    request = CheckRequest.model_validate(body("b"))
    via_mcp = apb_check(request.mandate, request.quotes)

    assert via_mcp == check(request) == post_check(client, body("b"))
