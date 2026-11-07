import importlib
import sys
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from apb import demo
from apb.parser import parse_mandate
from apb.rule_parser import LABEL, extract, parse_with_rules

MUM = "Send $200 to my mum's M-PESA 254700000001, it has to arrive within 2 hours"
KENYA = "Send 200 dollars to Kenya, no rush, within 2 days, M-PESA 254700000001"


@pytest.fixture(autouse=True)
def ai_off(monkeypatch):
    monkeypatch.setattr("apb.ai.load_dotenv", lambda: False)
    monkeypatch.setattr("apb.web.load_dotenv", lambda: False)
    monkeypatch.setenv("AI_BACKEND", "none")
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.delenv("ALLOW_EXECUTE", raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("an AI backend was called")

    monkeypatch.setattr("apb.ai.subprocess.run", refuse)
    monkeypatch.setattr("apb.ai.Anthropic", refuse)


def missing(result):
    return [item.field for item in result.missing_fields]


# The two examples


def test_first_example():
    result = parse_with_rules(MUM)

    assert result.status == "ok"
    assert result.parser == "rules"
    mandate = result.mandate
    assert mandate.send_amount == Decimal("200")
    assert mandate.send_currency == "USD"
    assert mandate.receive_country == "KE"
    assert mandate.payout_method == "mpesa"
    assert mandate.recipient_wallet == "254700000001"
    assert mandate.deadline_minutes == 120


def test_second_example():
    result = parse_with_rules(KENYA)

    assert result.status == "ok"
    assert result.mandate.deadline_minutes == 2880


# What it reads


@pytest.mark.parametrize(
    "text, amount, currency",
    [
        ("Send $200", "200", "USD"),
        ("Send US$ 200", "200", "USD"),
        ("Send £150.50", "150.50", "GBP"),
        ("Send 1,500 USD", "1500", "USD"),
        ("Send GBP 75", "75", "GBP"),
        ("Send 40 pounds", "40", "GBP"),
    ],
)
def test_amount_and_currency(text, amount, currency):
    extraction = extract(text)
    assert (extraction.send_amount, extraction.send_currency) == (amount, currency)


@pytest.mark.parametrize(
    "text, minutes",
    [
        ("within 90 minutes", 90),
        ("in 45 mins", 45),
        ("within 3 hours", 180),
        ("within 3 hrs", 180),
        ("in 2h", 120),
        ("within 1 day", 1440),
    ],
)
def test_deadline_units(text, minutes):
    result = parse_with_rules(f"Send $200 to M-PESA 254700000001 {text}")
    assert result.mandate.deadline_minutes == minutes


@pytest.mark.parametrize(
    "wallet_text, wallet",
    [
        ("M-PESA 254700000001", "254700000001"),
        ("mpesa 254700000001", "254700000001"),
        ("M PESA number +254 700 000 001", "+254 700 000 001"),
        ("M-PESA: 0712 345 678", "0712 345 678"),
    ],
)
def test_mpesa_number_forms(wallet_text, wallet):
    assert extract(f"Send $200 to {wallet_text} within 2 hours").recipient_wallet == wallet


def test_a_trailing_number_is_not_glued_onto_the_wallet():
    extraction = extract("Send $200 to M-PESA 254700000001 24 hours")

    assert extraction.recipient_wallet == "254700000001"
    assert extraction.deadline_amount == 24


# Never guess


def test_nothing_readable_becomes_questions():
    result = parse_with_rules("Send some money to my mum")

    assert result.status == "missing_fields"
    assert result.mandate is None
    assert {"send_amount", "send_currency", "payout_method", "recipient_wallet",
            "deadline_minutes"} <= set(missing(result))
    assert all(item.question for item in result.missing_fields)


@pytest.mark.parametrize(
    "text, field",
    [
        ("Send $200 or $300 to M-PESA 254700000001 within 2 hours", "send_amount"),
        ("Send 200 to M-PESA 254700000001 within 2 hours", "send_amount"),
        ("Send $200 to M-PESA 254700000001 within 2 hours or 3 hours", "deadline_minutes"),
        ("Send $200 to M-PESA 254700000001 within two hours", "deadline_minutes"),
        ("Send $200 to M-PESA 254700000001 within 1.5 hours", "deadline_minutes"),
        ("Send $200 to M-PESA 254700000001, no rush", "deadline_minutes"),
        ("Send $200 to M-PESA within 2 hours", "recipient_wallet"),
        (
            "Send $200 to M-PESA 254700000001 or M-PESA 254700000002 within 2 hours",
            "recipient_wallet",
        ),
        ("Send $200 to my mum's bank within 2 hours", "recipient_wallet"),
    ],
)
def test_unclear_values_become_questions(text, field):
    result = parse_with_rules(text)

    assert result.mandate is None
    assert field in missing(result)


def test_bank_and_mpesa_together_is_unclear():
    result = parse_with_rules("Send $200 by bank or M-PESA 254700000001 within 2 hours")

    assert "payout_method" in missing(result)


def test_country_is_not_assumed_without_a_kenyan_number():
    result = parse_with_rules("Send $200 to M-PESA 0712345678 within 2 hours")

    assert result.mandate is None
    assert missing(result) == ["receive_country"]


@pytest.mark.parametrize(
    "text, word",
    [
        ("Send €200 to M-PESA 254700000001 within 2 hours", "EUR"),
        ("Send 200 euros to M-PESA 254700000001 within 2 hours", "EUR"),
        ("Send $200 to Uganda by M-PESA 256700000001 within 2 hours", "UG"),
    ],
)
def test_out_of_scope_is_rejected(text, word):
    result = parse_with_rules(text)

    assert result.status == "out_of_scope"
    assert word in result.message


def test_gbp_is_in_scope():
    result = parse_with_rules("Send £200 to M-PESA 254700000001 within 2 hours")
    assert result.mandate.send_currency == "GBP"


# Where it is used


def test_ai_off_uses_the_rule_parser_without_calling_ai():
    result = parse_mandate(MUM)

    assert result.status == "ok"
    assert result.parser == "rules"


def test_a_failing_ai_is_reported_not_replaced(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "claude_cli")
    monkeypatch.setattr("apb.ai.shutil.which", lambda name: None)

    result = parse_mandate(MUM)

    assert result.status == "ai_unavailable"
    assert result.parser is None


def test_cli_text_labels_the_parser(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["apb.demo", "--text", MUM])

    assert demo.main() == 0

    output = capsys.readouterr().out
    assert f"Parsed mandate ({LABEL})" in output
    assert '"selected_route": "paypal"' in output


# Web


def client_for(monkeypatch, public=False):
    from apb import web

    if public:
        monkeypatch.setenv("PUBLIC_DEMO", "true")
    reloaded = importlib.reload(web)
    monkeypatch.setattr(reloaded, "load_dotenv", lambda: False)
    host = "apb-value-guard.onrender.com" if public else "localhost"
    return reloaded, TestClient(reloaded.app, base_url=f"http://{host}")


@pytest.fixture
def restore_web():
    yield
    import os

    from apb import web

    # This runs before monkeypatch undoes its changes, so clear the flag first,
    # or later tests would get the public app with no host check.
    os.environ.pop("PUBLIC_DEMO", None)
    importlib.reload(web)
    assert not web.public_demo()


def test_web_text_uses_the_rule_parser_and_labels_it(monkeypatch, restore_web):
    _, client = client_for(monkeypatch)

    data = client.post("/api/decide", json={"text": MUM}).json()

    assert data["status"] == "ok"
    assert data["request"]["source"] == "rules"
    assert data["receipt"]["selected_route"] == "paypal"


def test_page_has_the_rule_parser_label(monkeypatch, restore_web):
    _, client = client_for(monkeypatch)
    assert LABEL in client.get("/").text


def test_healthz(monkeypatch, restore_web):
    _, client = client_for(monkeypatch)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_and_pages_work_on_the_public_host(monkeypatch, restore_web):
    _, client = client_for(monkeypatch, public=True)

    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/benchmark").status_code == 200
    for case in ("a", "b"):
        data = client.post("/api/decide", json={"case": case}).json()
        assert data["status"] == "ok"


def test_local_server_still_refuses_other_hosts(monkeypatch, restore_web):
    from apb import web

    reloaded = importlib.reload(web)
    client = TestClient(reloaded.app, base_url="http://apb-value-guard.onrender.com")

    assert client.get("/healthz").status_code == 400


def test_public_demo_shows_the_banner(monkeypatch, restore_web):
    _, client = client_for(monkeypatch, public=True)

    assert client.get("/api/config").json() == {
        "allow_execute": False,
        "public_demo": True,
    }
    assert (
        "Public demo: synthetic quotes, no payments. Sandbox payouts run locally "
        "— see the video." in client.get("/").text
    )


def test_public_demo_never_executes_even_if_allowed(monkeypatch, restore_web):
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    _, client = client_for(monkeypatch, public=True)
    decision_id = client.post("/api/decide", json={"case": "a"}).json()["decision_id"]

    assert client.get("/api/config").json()["allow_execute"] is False
    response = client.post("/api/execute", json={"decision_id": decision_id})
    assert response.status_code == 403


def test_render_blueprint_keeps_the_demo_safe():
    import yaml_free_check

    text = (demo.FIXTURES.parent / "render.yaml").read_text("utf-8")
    service = yaml_free_check.values(text)

    assert service["startCommand"] == "uvicorn apb.web:app --host 0.0.0.0 --port $PORT"
    assert service["buildCommand"] == "pip install -r requirements.txt"
    assert service["healthCheckPath"] == "/healthz"
    assert service["AI_BACKEND"] == "none"
    assert service["ALLOW_EXECUTE"] == "false"
    assert service["PUBLIC_DEMO"] == "true"
    assert service["PYTHON_VERSION"].startswith("3.11")
    assert "PAYPAL" not in text.replace("Never add PayPal", "")
    assert "ANTHROPIC" not in text
