import io
import json
import logging
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apb import demo, paypal
from apb.decide import decide
from apb.demo import load_mandate, load_quotes
from apb.execute import SANDBOX_NOTE, execute
from apb.explain import with_explanation
from apb.paypal import PayPalError, require_sandbox, sender_batch_id

CLIENT_ID = "test-client-id"
SECRET = "test-secret-value"
TOKEN = "test-access-token"
RECEIVER = "receiver@example.com"
BATCH = "5UXD2E8A7EBQJ"

# Inside the fixture quotes' 900 second window, which opens at 12:00.
NOW = datetime(2026, 11, 6, 12, 5, tzinfo=UTC)
EXPIRED = datetime(2026, 11, 6, 12, 15, tzinfo=UTC)

TOKEN_REPLY = (200, {"access_token": TOKEN})
CREATED_REPLY = (
    201,
    {"batch_header": {"payout_batch_id": BATCH, "batch_status": "PENDING"}},
)
STATUS_REPLY = (
    200,
    {
        "batch_header": {"batch_status": "SUCCESS"},
        "items": [{"transaction_status": "SUCCESS"}],
    },
)


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
    """Stands in for HTTP. Each reply is (status, body) or an exception."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "error",
                {},
                io.BytesIO(json.dumps(body).encode()),
            )
        return FakeResponse(status, body)

    @property
    def urls(self):
        return [request.full_url for request in self.requests]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("apb.paypal.load_dotenv", lambda: False)
    monkeypatch.setattr("apb.ai.load_dotenv", lambda: False)
    monkeypatch.setenv("AI_BACKEND", "none")
    monkeypatch.setenv("PAYPAL_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("PAYPAL_SANDBOX_RECEIVER", RECEIVER)


@pytest.fixture
def http(monkeypatch):
    def install(*replies):
        opener = FakeOpener(*replies)
        monkeypatch.setattr("apb.paypal._opener", opener)
        return opener

    install()
    return install


def decided(case="a", **mandate_changes):
    mandate = load_mandate(case).model_copy(update=mandate_changes)
    return decide(mandate, load_quotes()), mandate


def successful(http):
    return http(TOKEN_REPLY, CREATED_REPLY, STATUS_REPLY)


# Paying


def test_case_a_pays_through_the_sandbox(http):
    opener = successful(http)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    assert opener.urls == [
        f"{paypal.API_BASE}/v1/oauth2/token",
        f"{paypal.API_BASE}/v1/payments/payouts",
        f"{paypal.API_BASE}/v1/payments/payouts/{BATCH}",
    ]
    execution = result.execution
    assert execution["status"] == "SUCCESS"
    assert execution["payout_batch_id"] == BATCH
    assert execution["data_mode"] == "sandbox"
    assert execution["executed"] is True
    assert result.quote_rechecked_at_commit is True


def test_payout_is_one_item_for_the_usd_send_amount(http):
    opener = successful(http)
    receipt, mandate = decided("a")

    execute(receipt, mandate, NOW)

    body = json.loads(opener.requests[1].data)
    assert body["sender_batch_header"]["sender_batch_id"] == sender_batch_id(receipt)
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["amount"] == {"value": "200.00", "currency": "USD"}
    assert item["receiver"] == RECEIVER


def test_receipt_says_the_payout_is_a_stand_in_and_kes_is_synthetic(http):
    successful(http)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    note = result.execution["note"]
    assert note == SANDBOX_NOTE
    assert "stand-in for M-PESA" in note
    assert "KES amounts" in note and "synthetic" in note
    assert {quote.data_mode for quote in result.candidate_set} == {"synthetic"}


def test_only_execution_fields_change(http):
    successful(http)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    changed = {"execution", "quote_rechecked_at_commit", "user_approved"}
    assert result.model_dump(exclude=changed) == receipt.model_dump(exclude=changed)


# Routes other than PayPal


def test_case_b_never_calls_paypal(http):
    opener = http()
    receipt, mandate = decided("b")

    result = execute(receipt, mandate, NOW, approve=True)

    assert opener.requests == []
    assert result.execution == {
        "status": "handoff_logged",
        "provider": "alternative_provider",
        "executed": False,
    }


def test_no_selected_route_never_calls_paypal(http):
    opener = http()
    mandate = load_mandate("a")
    quotes = [quote.model_copy(update={"fee": None}) for quote in load_quotes()]

    result = execute(decide(mandate, quotes), mandate, NOW)

    assert opener.requests == []
    assert result.execution["status"] == "no_route"
    assert result.execution["executed"] is False


def test_create_payout_refuses_another_route(http):
    opener = http()
    receipt, mandate = decided("b")

    with pytest.raises(PayPalError):
        paypal.create_payout(receipt, mandate)
    assert opener.requests == []


# Recheck at commit


def test_expired_quote_at_commit_blocks_payment(http):
    opener = http()
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, EXPIRED, approve=True)

    assert opener.requests == []
    assert result.quote_rechecked_at_commit is True
    assert result.execution["status"] == "blocked"
    assert result.execution["reason"] == "quote_expired"
    assert result.execution["executed"] is False


def test_quote_is_valid_until_just_before_expiry(http):
    successful(http)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, EXPIRED - timedelta(seconds=1))

    assert result.execution["executed"] is True


def test_recheck_runs_the_whole_gate(http):
    opener = http()
    receipt, mandate = decided("a")
    changed = receipt.model_copy(
        update={
            "candidate_set": [
                quote.model_copy(update={"payout_method": "bank"})
                for quote in receipt.candidate_set
            ]
        }
    )

    result = execute(changed, mandate, NOW)

    assert opener.requests == []
    assert result.execution["reason"] == "payout_unsupported"


# Approval


def test_fee_above_threshold_without_approval_blocks_payment(http):
    opener = http()
    receipt, mandate = decided("a", approval_required_above_fee=Decimal("1.00"))

    result = execute(receipt, mandate, NOW)

    assert opener.requests == []
    assert result.execution["status"] == "blocked"
    assert result.execution["reason"] == "approval_required"
    assert result.user_approved is False


def test_fee_above_threshold_with_approval_pays(http):
    successful(http)
    receipt, mandate = decided("a", approval_required_above_fee=Decimal("1.00"))

    result = execute(receipt, mandate, NOW, approve=True)

    assert result.execution["executed"] is True
    assert result.user_approved is True


def test_fee_equal_to_threshold_needs_no_approval(http):
    successful(http)
    receipt, mandate = decided("a", approval_required_above_fee=Decimal("2.99"))

    assert execute(receipt, mandate, NOW).execution["executed"] is True


# Idempotency


def test_same_mandate_twice_gives_the_same_sender_batch_id():
    first, _ = decided("a")
    second, _ = decided("a")
    assert sender_batch_id(first) == sender_batch_id(second)


def test_restamped_quotes_keep_the_same_sender_batch_id():
    mandate = load_mandate("a")
    fixed = decide(mandate, load_quotes())
    restamped = decide(mandate, load_quotes(NOW), now=NOW)
    assert sender_batch_id(fixed) == sender_batch_id(restamped)


def test_a_different_mandate_gets_a_different_sender_batch_id():
    first, _ = decided("a")
    second, _ = decided("a", mandate_id="another-mandate")
    assert sender_batch_id(first) != sender_batch_id(second)


def test_rerun_reports_the_original_batch(http):
    rejection = (
        400,
        {
            "name": "DUPLICATE",
            "links": [
                {
                    "href": f"{paypal.API_BASE}/v1/payments/payouts/{BATCH}",
                    "rel": "self",
                }
            ],
        },
    )
    opener = http(TOKEN_REPLY, rejection, STATUS_REPLY)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    assert result.execution["payout_batch_id"] == BATCH
    assert result.execution["duplicate_rerun"] is True
    assert len([r for r in opener.requests if r.method == "POST"]) == 2


def test_duplicate_without_a_link_is_not_reported_as_failed(http):
    receipt, mandate = decided("a")
    batch_id = sender_batch_id(receipt)
    rejection = (
        400,
        {
            "name": "USER_BUSINESS_ERROR",
            "message": f"Batch {batch_id} already exists",
            "details": [{"issue": "DUPLICATE_BATCH", "description": SECRET}],
        },
    )
    http(TOKEN_REPLY, rejection)

    result = execute(receipt, mandate, NOW)

    execution = result.execution
    assert execution["status"] == "duplicate_rejected"
    assert execution["executed"] is None
    assert execution["sender_batch_id"] == batch_id
    assert execution["paypal_details"] == ["DUPLICATE_BATCH"]
    assert SECRET not in result.model_dump_json()


def test_unrecognised_rejection_keeps_safe_details_only(http):
    rejection = (
        400,
        {
            "name": "USER_BUSINESS_ERROR",
            "message": SECRET,
            "details": [{"issue": "SOME_ISSUE"}, {"issue": SECRET.lower()}],
            "links": [{"href": "https://example.test/help", "rel": "information_link"}],
        },
    )
    http(TOKEN_REPLY, rejection)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    execution = result.execution
    assert execution["status"] == "failed"
    assert execution["reason"] == "paypal_user_business_error"
    assert execution["paypal_details"] == ["SOME_ISSUE", "link:information_link"]
    assert execution["sender_batch_id"] == sender_batch_id(receipt)
    assert SECRET not in result.model_dump_json()


@pytest.mark.parametrize(
    "href",
    [
        f"{paypal.API_BASE}/v1/payments/payouts/{BATCH}",
        f"{paypal.API_BASE}/v1/payments/payouts/{BATCH}?fields=batch_header",
        f"{paypal.API_BASE}/v1/payments/payouts/{BATCH}/",
    ],
)
def test_original_batch_id_is_found_in_link_forms(href):
    assert paypal._original_batch_id({"links": [{"href": href}]}) == BATCH


def test_item_links_are_not_mistaken_for_a_batch():
    href = f"{paypal.API_BASE}/v1/payments/payouts-item/{BATCH}"
    assert paypal._original_batch_id({"links": [{"href": href}]}) is None


def test_rejection_link_is_never_followed(http):
    rejection = (
        400,
        {"links": [{"href": f"https://evil.example/v1/payments/payouts/{BATCH}"}]},
    )
    opener = http(TOKEN_REPLY, rejection, STATUS_REPLY)
    receipt, mandate = decided("a")

    execute(receipt, mandate, NOW)

    assert all(url.startswith(paypal.API_BASE) for url in opener.urls)


# Sandbox only


@pytest.mark.parametrize(
    "url",
    [
        "https://api-m.paypal.com/v1/oauth2/token",
        "https://api.paypal.com/v1/oauth2/token",
        "http://api-m.sandbox.paypal.com/v1/oauth2/token",
        "https://api-m.sandbox.paypal.com.evil.example/v1/oauth2/token",
        "https://evil.example/api-m.sandbox.paypal.com",
        "https://api-m.sandbox.paypal.com@evil.example/",
        "https://user:pass@api-m.sandbox.paypal.com/",
        "https://api-m.sandbox.paypal.com:8443/",
    ],
)
def test_non_sandbox_url_is_refused(http, url):
    opener = http()

    with pytest.raises(PayPalError) as caught:
        paypal._call("POST", url, {"Authorization": "Bearer x"}, b"{}")

    assert caught.value.code == "non_sandbox_url"
    assert opener.requests == []


def test_sandbox_url_is_accepted():
    require_sandbox(f"{paypal.API_BASE}/v1/oauth2/token")


def test_changed_base_url_stops_the_payout(http, monkeypatch):
    opener = http()
    monkeypatch.setattr("apb.paypal.API_BASE", "https://api-m.paypal.com")
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    assert opener.requests == []
    assert result.execution["reason"] == "non_sandbox_url"
    assert result.execution["executed"] is False


def test_redirects_are_not_followed():
    assert paypal._NoRedirects().redirect_request() is None


# Failures


@pytest.mark.parametrize(
    "variable",
    ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SANDBOX_RECEIVER"],
)
def test_missing_configuration_makes_no_payment(http, monkeypatch, variable):
    opener = http()
    monkeypatch.delenv(variable)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    assert opener.requests == []
    assert result.execution["reason"] == "paypal_not_configured"
    assert result.execution["executed"] is False


def test_gbp_is_never_paid(http):
    opener = http()
    receipt, mandate = decided("a", send_currency="GBP")

    result = execute(receipt, mandate, NOW)

    assert opener.requests == []
    assert result.execution["reason"] == "currency_unsupported"


def test_an_amount_paypal_would_round_is_not_paid(http):
    opener = http()
    mandate = load_mandate("a").model_copy(update={"send_amount": Decimal("200.005")})
    quotes = [q.model_copy(update={"send_amount": Decimal("200.005")}) for q in load_quotes()]

    result = execute(decide(mandate, quotes), mandate, NOW)

    assert opener.requests == []
    assert result.execution["reason"] == "amount_invalid"


def test_bad_credentials_fail_before_any_payout(http):
    opener = http((401, {"error": "invalid_client"}))
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    assert len(opener.requests) == 1
    assert result.execution["status"] == "failed"
    assert result.execution["reason"] == "paypal_auth_failed"
    assert result.execution["executed"] is False


def test_payout_rejection_is_a_failure(http):
    http(TOKEN_REPLY, (422, {"name": "INSUFFICIENT_FUNDS"}))
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    assert result.execution["status"] == "failed"
    assert result.execution["reason"] == "paypal_insufficient_funds"
    assert result.execution["executed"] is False


@pytest.mark.parametrize(
    "reply", [(503, {"name": "SERVICE_UNAVAILABLE"}), TimeoutError("slow")]
)
def test_unclear_outcome_is_not_reported_as_failed_or_paid(http, reply):
    http(TOKEN_REPLY, reply)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    assert result.execution["status"] == "outcome_unknown"
    assert result.execution["executed"] is None
    assert result.execution["sender_batch_id"] == sender_batch_id(receipt)


def test_batch_id_is_kept_if_the_status_lookup_fails(http):
    http(TOKEN_REPLY, CREATED_REPLY, TimeoutError("slow"))
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)

    assert result.execution["payout_batch_id"] == BATCH
    assert result.execution["status"] == "PENDING"
    assert result.execution["executed"] is True


@pytest.mark.parametrize(
    "status_reply",
    [
        (200, {"batch_header": {"batch_status": "DENIED"}}),
        (200, {"batch_header": {"batch_status": "CANCELED"}}),
        (
            200,
            {
                "batch_header": {"batch_status": "SUCCESS"},
                "items": [{"transaction_status": "FAILED"}],
            },
        ),
    ],
)
def test_denied_or_failed_payouts_are_not_executed(http, status_reply):
    http(TOKEN_REPLY, CREATED_REPLY, status_reply)
    receipt, mandate = decided("a")

    assert execute(receipt, mandate, NOW).execution["executed"] is False


def test_wrong_mandate_is_refused(http):
    opener = http()
    receipt, _ = decided("a")

    with pytest.raises(ValueError):
        execute(receipt, load_mandate("b"), NOW)
    assert opener.requests == []


# Secrets


@pytest.mark.parametrize(
    "replies",
    [
        (TOKEN_REPLY, CREATED_REPLY, STATUS_REPLY),
        ((401, {"error": SECRET, "name": SECRET}),),
        (TOKEN_REPLY, (400, {"name": TOKEN, "message": SECRET})),
        (TOKEN_REPLY, (500, {"name": TOKEN, "message": SECRET})),
    ],
)
def test_secret_never_appears_in_the_receipt_or_logs(http, caplog, capsys, replies):
    caplog.set_level(logging.DEBUG)
    http(*replies)
    receipt, mandate = decided("a")

    result = execute(receipt, mandate, NOW)
    demo.print_report(mandate, result)

    captured = capsys.readouterr()
    everything = result.model_dump_json() + caplog.text + captured.out + captured.err
    assert SECRET not in everything
    assert TOKEN not in everything
    assert CLIENT_ID not in everything
    assert RECEIVER not in everything


def test_exception_does_not_carry_the_response(http):
    http((401, {"error": SECRET}))

    with pytest.raises(PayPalError) as caught:
        paypal._access_token()

    assert SECRET not in str(caught.value)
    assert SECRET not in repr(caught.value)


def test_credentials_are_not_kept_on_the_module(http):
    successful(http)
    receipt, mandate = decided("a")

    execute(receipt, mandate, NOW)

    assert SECRET not in repr(vars(paypal))
    assert TOKEN not in repr(vars(paypal))


def test_claude_command_gets_no_paypal_variables(monkeypatch):
    from apb.ai import _cli_environment

    environment = _cli_environment()
    assert not [key for key in environment if key.upper().startswith("PAYPAL_")]


def test_ai_input_has_no_execution_data(http, monkeypatch):
    from apb.explain import _model_input

    successful(http)
    receipt, mandate = decided("a")
    result = execute(receipt, mandate, NOW)

    sent = _model_input(result, mandate)

    assert BATCH not in sent
    assert "execution" not in json.loads(sent)["receipt"]
    assert SECRET not in sent


def test_explaining_never_triggers_paypal(http):
    opener = http()
    receipt, mandate = decided("a")

    with_explanation(receipt, mandate)

    assert opener.requests == []


# Re-stamping


def test_fixtures_keep_their_fixed_time_by_default():
    assert {quote.timestamp for quote in load_quotes()} == {
        datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    }


def test_live_load_restamps_synthetic_quotes():
    assert {quote.timestamp for quote in load_quotes(NOW)} == {NOW}


# CLI


def run_demo(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["apb.demo", *args])
    return demo.main()


@pytest.mark.parametrize("flags", [(), ("--approve",)])
def test_nothing_is_paid_without_execute(http, monkeypatch, capsys, flags):
    opener = http()

    assert run_demo(monkeypatch, "--case", "a", *flags) == 0

    assert opener.requests == []
    assert '"execution": null' in capsys.readouterr().out


def test_cli_case_a_execute_pays(http, monkeypatch, capsys):
    opener = successful(http)

    assert run_demo(monkeypatch, "--case", "a", "--execute") == 0

    output = capsys.readouterr().out
    assert len(opener.requests) == 3
    assert "\nExecution\n" in output
    assert f'"payout_batch_id": "{BATCH}"' in output


def test_cli_case_b_execute_makes_no_call(http, monkeypatch, capsys):
    opener = http()

    assert run_demo(monkeypatch, "--case", "b", "--execute") == 0

    assert opener.requests == []
    assert '"status": "handoff_logged"' in capsys.readouterr().out


def posted_batch_id(opener):
    payout = next(r for r in opener.requests if r.full_url.endswith("/payouts"))
    return json.loads(payout.data)["sender_batch_header"]["sender_batch_id"]


def test_fresh_makes_a_new_batch_and_plain_reruns_do_not(http, monkeypatch, capsys):
    plain = []
    for _ in range(2):
        opener = successful(http)
        run_demo(monkeypatch, "--case", "a", "--execute")
        plain.append(posted_batch_id(opener))

    opener = successful(http)
    run_demo(monkeypatch, "--case", "a", "--execute", "--fresh")

    assert plain[0] == plain[1]
    assert posted_batch_id(opener) != plain[0]
    assert "-fresh-" in capsys.readouterr().out


def test_fresh_changes_only_the_mandate_id():
    mandate = load_mandate("a")

    fresh = demo.fresh_mandate(mandate, NOW)

    assert fresh.mandate_id == "syn-mandate-case-a-fresh-20261106T120500Z"
    ignored = {"mandate_id"}
    assert fresh.model_dump(exclude=ignored) == mandate.model_dump(exclude=ignored)


def test_fresh_runs_at_different_times_get_different_batches():
    mandate = load_mandate("a")
    quotes = load_quotes()

    first = decide(demo.fresh_mandate(mandate, NOW), quotes)
    later = NOW + timedelta(seconds=1)
    second = decide(demo.fresh_mandate(mandate, later), quotes)

    assert sender_batch_id(first) != sender_batch_id(second)


def test_fresh_needs_execute(http, monkeypatch):
    opener = http()

    with pytest.raises(SystemExit):
        run_demo(monkeypatch, "--case", "a", "--fresh")
    assert opener.requests == []


def test_fresh_still_makes_no_paypal_call_for_case_b(http, monkeypatch):
    opener = http()

    run_demo(monkeypatch, "--case", "b", "--execute", "--fresh")

    assert opener.requests == []


def test_cli_text_execute_pays(http, monkeypatch, capsys):
    from apb.parser import ParseResult

    opener = successful(http)
    mandate = load_mandate("a")
    monkeypatch.setattr(
        "apb.demo.parse_mandate",
        lambda text: ParseResult(status="ok", mandate=mandate),
    )

    assert run_demo(monkeypatch, "--text", "Send it", "--execute") == 0

    assert len(opener.requests) == 3
