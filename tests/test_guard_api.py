import json

import pytest
from fastapi.testclient import TestClient

from apb import web
from apb.guard import api as guard_api


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("apb.web.load_dotenv", lambda: False)
    monkeypatch.setattr("apb.guard.execution.load_dotenv", lambda: False)
    monkeypatch.delenv("ALLOW_EXECUTE", raising=False)
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    web._hits.clear()


@pytest.fixture
def client():
    return TestClient(web.app, base_url="http://localhost")


def test_episode_list_has_no_answer_keys(client):
    data = client.get("/api/v3/episodes").json()
    assert len(data["episodes"]) == 17
    assert data["episodes"][0]["episode_id"] == "E-AUTH-004"
    assert "answer_key" not in json.dumps(data)


def test_public_view_hides_the_hidden_payload(client):
    text = client.get("/api/v3/episodes/E-AUTH-004").text
    assert "answer_key" not in text
    assert "Ignore prior instructions" not in text
    assert "acct-ref-8891" not in text


def test_page_and_static_files_hold_no_hidden_facts(client):
    for path in ("/", "/static/app.js", "/static/app.css"):
        text = client.get(path).text
        for hidden in ("Ignore prior instructions", "acct-ref-", "answer_key\":", "NL22"):
            assert hidden not in text


def test_run_returns_receipt_and_reveals_after_deciding(client):
    data = client.post("/api/v3/episodes/E-AUTH-004/run", json={}).json()
    receipt = data["receipts"][-1]
    assert receipt["selected_action"] == "STOP"
    assert receipt["reason_codes"] == ["external_instruction_conflict", "beneficiary_mutation_detected", "execution_not_delegated"]
    assert receipt["paypal_called"] is False
    assert data["answer_key"]["action"] == "STOP"
    assert data["authority"]["requested_level"] == 4 and data["authority"]["approved_level"] == 2
    assert "Ignore prior instructions" in json.dumps(data["episode"]["sources"])
    assert data["execution_mode"] == "simulated_no_credentials"


def test_pay_on_the_demo_is_simulated_not_sent(client):
    receipt = client.post("/api/v3/episodes/E-AUTH-004-R/run", json={}).json()["receipts"][-1]
    assert receipt["selected_action"] == "PAY"
    assert receipt["execution_reference"]["mode"] == "simulated_no_credentials"
    assert receipt["execution_reference"]["status"] == "SIMULATED_NOT_SENT"


def test_repeat_runs_on_the_demo_start_clean(client):
    for _ in range(2):
        receipt = client.post("/api/v3/episodes/E-AUTH-004-R/run", json={}).json()["receipts"][-1]
        assert receipt["security_outcome"] == "AUTHORIZED_EXECUTION"


def test_public_demo_never_calls_paypal_even_if_execution_is_allowed(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    monkeypatch.setenv("ALLOW_EXECUTE", "true")

    def boom(*args, **kwargs):
        raise AssertionError("PayPal must not be called on the public demo")

    monkeypatch.setattr("apb.paypal.submit_payout", boom)
    receipt = client.post("/api/v3/episodes/E-AUTH-004-R/run", json={}).json()["receipts"][-1]
    assert receipt["execution_reference"]["mode"] == "simulated_no_credentials"


def test_local_sandbox_pays_once_across_requests(client, monkeypatch):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    calls = []

    def fake(batch_id, amount, currency, note=""):
        calls.append(batch_id)
        return {"payout_batch_id": "PB1", "batch_status": "SUCCESS", "duplicate": False}

    monkeypatch.setattr("apb.paypal.submit_payout", fake)
    monkeypatch.setattr(guard_api, "_store", guard_api.IdempotencyStore())
    first = client.post("/api/v3/episodes/E-AUTH-004-R/run", json={}).json()["receipts"][-1]
    second = client.post("/api/v3/episodes/E-AUTH-004-R/run", json={}).json()["receipts"][-1]
    assert len(calls) == 1
    assert first["execution_reference"]["mode"] == "sandbox"
    assert second["reason_codes"] == ["duplicate_request"]
    assert second["execution_reference"]["reference"] == "PB1"


def test_unknown_episode_is_404(client):
    assert client.get("/api/v3/episodes/E-NOPE").status_code == 404
    assert client.post("/api/v3/episodes/E-NOPE/run", json={}).status_code == 404


def test_reference_endpoints(client):
    assert client.get("/api/v3/versions").json()["policy_bundle"] == "payments-2026.11.1"
    levels = client.get("/api/v3/authority").json()["levels"]
    assert [level["name"] for level in levels] == ["Observe", "Recommend", "Prepare", "Initiate", "Execute", "Orchestrate"]
    assert len(client.get("/api/v3/reason-codes").json()["reason_codes"]) >= 35
    assert client.get("/api/v3/world").json()["world_version"] == "world-0.2.0"
    monitoring = client.get("/api/v3/monitoring").json()
    assert monitoring["data_mode"] == "synthetic"
    assert "customer-configurable" in monitoring["note"]


def test_events_accept_only_known_names_and_no_payload(client):
    before = guard_api.event_counts().get("video_play", 0)
    assert client.post("/api/v3/events", json={"event": "video_play", "asset_id": "apb_v2_product_demo"}).status_code == 204
    assert guard_api.event_counts()["video_play"] == before + 1
    assert client.post("/api/v3/events", json={"event": "steal"}).status_code == 422
    assert client.post("/api/v3/events", json={"event": "video_play", "iban": "NL00"}).status_code == 422


def test_v3_posts_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(web, "RATE_LIMIT", 2)
    codes = [client.post("/api/v3/events", json={"event": "video_play"}).status_code for _ in range(3)]
    assert codes == [204, 204, 429]


@pytest.mark.parametrize("path", ["/decision-lab", "/authority", "/episodes", "/world", "/receipt", "/demo"])
def test_deep_links_serve_the_app(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "<title>APB Guard | Zero-Trust Controls for Agentic Payments</title>" in response.text


def test_demo_assets_are_served_and_labelled(client):
    page = client.get("/").text
    assert 'preload="metadata"' in page and "playsinline" in page and "autoplay" not in page
    assert 'kind="captions"' in page
    assert "Synthetic fixture · PayPal sandbox · No real money moves" in page
    vtt = client.get("/static/media/APBv2.en.vtt")
    assert vtt.status_code == 200 and vtt.text.startswith("WEBVTT")
    assert client.get("/static/media/apb-demo-poster.jpg").status_code == 200


def test_structured_data_makes_no_claims(client):
    page = client.get("/").text.lower()
    for claim in ("aggregaterating", "\"review", "award", "certified", "endorsed", "partner"):
        assert claim not in page
