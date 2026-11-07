import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from apb import web
from apb.parser import MissingField, ParseResult

CLIENT_ID = "web-test-client-id"
SECRET = "web-test-secret-value"
TOKEN = "web-test-access-token"
RECEIVER = "web-receiver@example.com"
WALLET = "syn-mpesa-wallet-001"
BATCH = "WEBBATCH123"
FOOTER = "All quotes are synthetic. Payouts run in PayPal sandbox. No real money moves."


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.urls = []

    def open(self, request, timeout=None):
        self.urls.append(request.full_url)
        return FakeResponse(*self.replies.pop(0))


def payout_replies():
    return [
        (200, {"access_token": TOKEN}),
        (201, {"batch_header": {"payout_batch_id": BATCH, "batch_status": "PENDING"}}),
        (200, {"batch_header": {"batch_status": "SUCCESS"}, "items": []}),
    ]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("apb.web.load_dotenv", lambda: False)
    monkeypatch.setattr("apb.paypal.load_dotenv", lambda: False)
    monkeypatch.setattr("apb.ai.load_dotenv", lambda: False)
    monkeypatch.setenv("AI_BACKEND", "none")
    monkeypatch.delenv("ALLOW_EXECUTE", raising=False)
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.setenv("PAYPAL_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("PAYPAL_SANDBOX_RECEIVER", RECEIVER)
    web._decisions.clear()


@pytest.fixture
def client():
    return TestClient(web.app, base_url="http://localhost")


@pytest.fixture
def http(monkeypatch):
    opener = FakeOpener(*payout_replies())
    monkeypatch.setattr("apb.paypal._opener", opener)
    return opener


# Pages


def test_page_loads_with_title_tabs_and_footer(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "<title>APB Value Guard, built on PayPal</title>" in response.text
    assert FOOTER in response.text
    assert ">Decide<" in response.text and ">Benchmark<" in response.text


def test_page_has_no_external_resources(client):
    text = client.get("/").text

    assert "https://" not in text and "http://" not in text


def test_page_never_injects_html(client):
    assert "innerHTML" not in client.get("/").text


def test_page_has_the_subtitle(client):
    assert (
        "Picks the best eligible route observed within the candidate set and "
        "quote window, scored on what the recipient receives." in client.get("/").text
    )


def test_readme_says_how_to_turn_on_execution():
    text = (web.ROOT / "README.md").read_text("utf-8")
    assert '$env:ALLOW_EXECUTE="true"; python -m apb.web' in text


def test_method_doc_is_served(client):
    response = client.get("/docs/METHOD.md")

    assert response.status_code == 200
    assert "Delivered Value Score" in response.text


def test_benchmark_endpoint(client):
    response = client.get("/api/benchmark")

    assert response.status_code == 200
    data = response.json()
    assert data["method_version"] == "apb-0.1"
    assert [agent["agent"] for agent in data["agents"]] == [
        "naive_agent",
        "guarded_agent",
    ]


def test_generated_api_docs_are_off(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_foreign_host_is_refused():
    client = TestClient(web.app, base_url="http://evil.example")
    assert client.get("/api/config").status_code == 400


# Deciding


@pytest.mark.parametrize(
    "case, selected", [("a", "paypal"), ("b", "alternative_provider")]
)
def test_decide_a_case(client, case, selected):
    response = client.post("/api/decide", json={"case": case})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["receipt"]["selected_route"] == selected
    assert data["receipt"]["explanation_source"] == "template"


def test_fixture_request_view(client):
    data = client.post("/api/decide", json={"case": "a"}).json()

    assert data["request"] == {
        "source": "fixture",
        "amount": "200.00",
        "currency": "USD",
        "payout_method": "mpesa",
        "wallet": "\u2022\u2022\u2022\u2022 001",
        "deadline_minutes": 120,
    }
    assert data["receive_currency"] == "KES"


def test_text_request_view_is_from_ai_and_masks_the_wallet(client, monkeypatch):
    from apb.demo import load_mandate

    wallet = "254700000001"
    mandate = load_mandate("a").model_copy(update={"recipient_wallet": wallet})
    monkeypatch.setattr(
        "apb.web.parse_mandate", lambda text: ParseResult(status="ok", mandate=mandate)
    )
    response = client.post("/api/decide", json={"text": "Send $200"})

    request = response.json()["request"]
    assert request["source"] == "ai"
    assert request["wallet"] == "\u2022\u2022\u2022\u2022 0001"
    assert wallet not in response.text


@pytest.mark.parametrize(
    "wallet, expected",
    [("+254 700 000 001", "0001"), ("KE12-3456", "3456"), ("12", "12")],
)
def test_mask_wallet_keeps_at_most_the_last_4_digits(wallet, expected):
    assert web.mask_wallet(wallet) == f"\u2022\u2022\u2022\u2022 {expected}"


def test_receipt_values_stay_unformatted(client):
    receipt = client.post("/api/decide", json={"case": "a"}).json()["receipt"]

    paypal = next(q for q in receipt["candidate_set"] if q["provider"] == "paypal")
    assert paypal["recipient_amount"] == "25709"
    assert paypal["fee"] == "2.99"


def test_decide_shows_exclusions_and_scores(client):
    receipt = client.post("/api/decide", json={"case": "a"}).json()["receipt"]

    assert receipt["excluded_routes"] == [
        {"provider": "alternative_provider", "reason": "deadline_violation"}
    ]
    assert receipt["scores"] == {"paypal": 100.0, "zero_fee_provider": 98.9}


def test_decide_text(client, monkeypatch):
    from apb.demo import load_mandate

    monkeypatch.setattr(
        "apb.web.parse_mandate",
        lambda text: ParseResult(status="ok", mandate=load_mandate("a")),
    )
    data = client.post("/api/decide", json={"text": "Send $200"}).json()

    assert data["status"] == "ok"
    assert data["receipt"]["selected_route"] == "paypal"


def test_decide_text_returns_questions(client, monkeypatch):
    question = MissingField(field="send_amount", question="How much?")
    monkeypatch.setattr(
        "apb.web.parse_mandate",
        lambda text: ParseResult(status="missing_fields", missing_fields=[question]),
    )
    data = client.post("/api/decide", json={"text": "Send money"}).json()

    assert data["status"] == "missing_fields"
    assert data["missing_fields"][0]["question"] == "How much?"
    assert "receipt" not in data


def test_decide_text_with_ai_off_asks_what_the_rules_cannot_read(client):
    data = client.post("/api/decide", json={"text": "Send $200"}).json()

    assert data["status"] == "missing_fields"
    assert "receipt" not in data


def test_gbp_is_not_decided_on_usd_fixtures(client, monkeypatch):
    from apb.demo import load_mandate

    mandate = load_mandate("a").model_copy(update={"send_currency": "GBP"})
    monkeypatch.setattr(
        "apb.web.parse_mandate", lambda text: ParseResult(status="ok", mandate=mandate)
    )
    data = client.post("/api/decide", json={"text": "Send 200 pounds"}).json()

    assert data["status"] == "out_of_scope"
    assert "receipt" not in data


@pytest.mark.parametrize(
    "body", [{}, {"case": "a", "text": "both"}, {"case": "z"}, {"text": "x" * 1001}]
)
def test_invalid_decide_requests_are_rejected(client, body):
    assert client.post("/api/decide", json=body).status_code == 422


def test_stored_decisions_are_bounded(client):
    for _ in range(web.MAX_STORED_DECISIONS + 5):
        client.post("/api/decide", json={"case": "a"})
    assert len(web._decisions) == web.MAX_STORED_DECISIONS


# Execution


@pytest.mark.parametrize("value", [None, "", "false", "no", "1", "yes"])
def test_execute_refuses_unless_allow_execute_is_true(client, http, monkeypatch, value):
    decision_id = client.post("/api/decide", json={"case": "a"}).json()["decision_id"]
    if value is not None:
        monkeypatch.setenv("ALLOW_EXECUTE", value)

    response = client.post("/api/execute", json={"decision_id": decision_id})

    assert response.status_code == 403
    assert http.urls == []


def test_config_reports_execute_off_by_default(client):
    assert client.get("/api/config").json() == {
        "allow_execute": False,
        "public_demo": False,
    }


def test_config_reports_execute_on(client, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    assert client.get("/api/config").json()["allow_execute"] is True


def test_execute_uses_fresh_mandate_and_shows_the_batch(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decided = client.post("/api/decide", json={"case": "a"}).json()

    response = client.post("/api/execute", json={"decision_id": decided["decision_id"]})

    assert response.status_code == 200
    assert response.json()["execution"]["payout_batch_id"] == BATCH
    assert "-fresh-" in decided["mandate"]["mandate_id"]
    assert len(http.urls) == 3


def test_execute_returns_the_updated_receipt(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decided = client.post("/api/decide", json={"case": "a"}).json()

    data = client.post(
        "/api/execute", json={"decision_id": decided["decision_id"]}
    ).json()

    assert data["receipt"]["execution"] == data["execution"]
    assert data["receipt"]["quote_rechecked_at_commit"] is True
    assert data["receipt"]["mandate_hash"] == decided["receipt"]["mandate_hash"]


def test_second_click_does_not_pay_again(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decided = client.post("/api/decide", json={"case": "a"}).json()
    body = {"decision_id": decided["decision_id"]}

    first = client.post("/api/execute", json=body).json()
    second = client.post("/api/execute", json=body).json()

    assert first == second
    assert len(http.urls) == 3


def test_case_b_execute_is_a_handoff(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decided = client.post("/api/decide", json={"case": "b"}).json()

    response = client.post("/api/execute", json={"decision_id": decided["decision_id"]})

    assert response.json()["execution"]["status"] == "handoff_logged"
    assert http.urls == []


def test_unknown_decision_is_refused(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")

    response = client.post("/api/execute", json={"decision_id": "made-up"})

    assert response.status_code == 404
    assert http.urls == []


def test_browser_cannot_supply_a_receipt(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")

    response = client.post(
        "/api/execute", json={"decision_id": "x", "receipt": {"selected_route": "paypal"}}
    )

    assert response.status_code == 422
    assert http.urls == []


def test_stale_decision_is_blocked_at_commit(client, http, monkeypatch):
    from datetime import timedelta

    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decided = client.post("/api/decide", json={"case": "a"}).json()
    decision_id = decided["decision_id"]
    mandate, receipt = web._decisions[decision_id]
    stale = [
        quote.model_copy(update={"timestamp": quote.timestamp - timedelta(hours=1)})
        for quote in receipt.candidate_set
    ]
    web._decisions[decision_id] = (
        mandate,
        receipt.model_copy(update={"candidate_set": stale}),
    )

    response = client.post("/api/execute", json={"decision_id": decision_id})

    assert response.json()["execution"]["reason"] == "quote_expired"
    assert http.urls == []


# Secrets


def test_no_endpoint_returns_secrets(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decided = client.post("/api/decide", json={"case": "a"})
    decision_id = decided.json()["decision_id"]

    responses = [
        client.get("/"),
        client.get("/docs/METHOD.md"),
        client.get("/api/config"),
        client.get("/api/benchmark"),
        decided,
        client.post("/api/execute", json={"decision_id": decision_id}),
        client.post("/api/execute", json={"decision_id": "made-up"}),
    ]

    everything = "\n".join(response.text for response in responses)
    for secret in (SECRET, CLIENT_ID, TOKEN, RECEIVER, WALLET):
        assert secret not in everything


def test_parser_is_not_called_for_a_case(client, monkeypatch):
    parser = MagicMock()
    monkeypatch.setattr("apb.web.parse_mandate", parser)

    client.post("/api/decide", json={"case": "a"})

    parser.assert_not_called()


# Screening


def test_case_c_shows_a_stop_and_no_route(client):
    receipt = client.post("/api/decide", json={"case": "c"}).json()["receipt"]

    assert receipt["stop_reason"] == "Payment stopped: screening hold (simulated)"
    assert receipt["selected_route"] is None
    assert receipt["screening_result"]["source"] == "simulated"
    assert {r["reason"] for r in receipt["excluded_routes"]} == {"screening_hold"}


def test_execute_endpoint_never_pays_through_a_hold(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decided = client.post("/api/decide", json={"case": "c"}).json()

    data = client.post(
        "/api/execute", json={"decision_id": decided["decision_id"]}
    ).json()

    assert data["execution"]["status"] == "stopped"
    assert http.urls == []


def test_benchmark_endpoint_has_nine_tests(client):
    data = client.get("/api/benchmark").json()

    names = {scenario["name"] for scenario in data["scenarios"]}
    assert {"screening_hold", "kyc_gap"} <= names
    assert len(names) == 9


# Payee name check


def test_close_match_shows_both_names_with_the_simulated_label(client):
    receipt = client.post("/api/decide", json={"case": "d"}).json()["receipt"]

    check = receipt["payee_check"]
    assert check["result"] == "close_match"
    assert check["source"] == "simulated"
    assert check["expected_name"] != check["returned_name"]
    assert receipt["selected_route"] == "paypal"
    assert "Confirmation needed" in receipt["confirmation_reason"]


def test_ui_needs_a_confirm_step_before_paying(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decision_id = client.post("/api/decide", json={"case": "d"}).json()["decision_id"]

    first = client.post("/api/execute", json={"decision_id": decision_id}).json()
    assert first["execution"]["status"] == "needs_confirmation"
    assert http.urls == []

    second = client.post(
        "/api/execute", json={"decision_id": decision_id, "confirm_payee": True}
    ).json()
    assert second["execution"]["payout_batch_id"] == BATCH
    assert second["receipt"]["user_approved"] is True
    assert len(http.urls) == 3


def test_confirm_cannot_override_a_no_match(client, http, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    decision_id = client.post("/api/decide", json={"case": "e"}).json()["decision_id"]

    data = client.post(
        "/api/execute", json={"decision_id": decision_id, "confirm_payee": True}
    ).json()

    assert data["execution"]["reason"] == "payee_mismatch"
    assert http.urls == []
