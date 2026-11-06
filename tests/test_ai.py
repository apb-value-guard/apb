import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import pytest

from apb import demo
from apb.decide import decide
from apb.demo import load_mandate, load_quotes
from apb.explain import (
    ai_explanation,
    explain,
    grounding_check,
    template_explanation,
    with_explanation,
)
from apb.parser import MandateExtraction, MissingField, parse_mandate

MUM = "Send $200 to my mum's M-PESA 254700000001, it has to arrive within 2 hours"
KENYA = "Send 200 dollars to Kenya, no rush, within 2 days, M-PESA 254700000001"

GOOD_AI_TEXT = (
    "paypal was selected with a DVS of 100.0, delivering 25709 to the recipient. "
    "alternative_provider was excluded because its 1440 minute delivery misses "
    "the deadline. zero_fee_provider scored 98.9. "
    "All values are synthetic."
)


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    """Keep every test offline, even if a real .env exists."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setattr("apb.ai.load_dotenv", lambda: False)

    def refuse(*args, **kwargs):
        raise AssertionError("a real Anthropic client was created")

    monkeypatch.setattr("apb.ai.Anthropic", refuse)


@pytest.fixture
def receipt():
    return decide(load_mandate("a"), load_quotes())


def extraction(**changes):
    values = {
        "send_amount": "200",
        "send_currency": "USD",
        "receive_country": None,
        "payout_method": "mpesa",
        "recipient_wallet": "254700000001",
        "deadline_amount": 2,
        "deadline_unit": "hours",
        "missing_fields": [],
    }
    return MandateExtraction(**{**values, **changes})


def parse_client(result, stop_reason="end_turn"):
    client = MagicMock()
    client.beta.messages.parse.return_value = SimpleNamespace(
        parsed_output=result, stop_reason=stop_reason
    )
    return client


def explain_client(text, stop_reason="end_turn"):
    client = MagicMock()
    client.beta.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason
    )
    return client


def missing_names(result):
    return [item.field for item in result.missing_fields]


# Parsing


def test_parses_the_first_example(monkeypatch):
    result = parse_mandate(MUM, client=parse_client(extraction()))

    assert result.status == "ok"
    mandate = result.mandate
    assert mandate.send_amount == Decimal("200")
    assert mandate.send_currency == "USD"
    assert mandate.receive_country == "KE"
    assert mandate.payout_method == "mpesa"
    assert mandate.recipient_wallet == "254700000001"
    assert mandate.deadline_minutes == 120


def test_parses_the_second_example():
    result = parse_mandate(
        KENYA,
        client=parse_client(
            extraction(
                receive_country="KE",
                payout_method="M-PESA",
                deadline_unit="days",
            )
        ),
    )

    assert result.status == "ok"
    assert result.mandate.deadline_minutes == 2880
    assert result.mandate.payout_method == "mpesa"


def test_missing_fields_give_questions_and_no_mandate():
    question = "How soon must it arrive?"
    result = parse_mandate(
        "Send $200 to M-PESA 254700000001",
        client=parse_client(
            extraction(
                deadline_amount=None,
                deadline_unit=None,
                missing_fields=[
                    MissingField(field="deadline_minutes", question=question)
                ],
            )
        ),
    )

    assert result.status == "missing_fields"
    assert result.mandate is None
    assert result.missing_fields == [
        MissingField(field="deadline_minutes", question=question)
    ]


def test_code_asks_even_if_the_model_forgets_to():
    result = parse_mandate(
        "Send some money",
        client=parse_client(
            extraction(send_amount=None, send_currency=None, recipient_wallet=None)
        ),
    )

    assert result.mandate is None
    assert {"send_amount", "send_currency", "recipient_wallet"} <= set(
        missing_names(result)
    )
    assert all(item.question for item in result.missing_fields)


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"send_amount": "300"}, "send_amount"),
        ({"recipient_wallet": "254799999999"}, "recipient_wallet"),
        ({"deadline_amount": 5}, "deadline_minutes"),
    ],
)
def test_values_not_in_the_text_are_not_accepted(changes, field):
    result = parse_mandate(MUM, client=parse_client(extraction(**changes)))

    assert result.mandate is None
    assert field in missing_names(result)


def test_a_field_the_model_calls_unclear_stays_missing():
    flagged = MissingField(field="send_amount", question="Is it 200 or 300?")
    result = parse_mandate(
        MUM, client=parse_client(extraction(missing_fields=[flagged]))
    )

    assert result.mandate is None
    assert result.missing_fields == [flagged]


def test_country_is_not_assumed_for_a_bank_payout():
    result = parse_mandate(
        "Send $200 to bank 254700000001 within 2 hours",
        client=parse_client(extraction(payout_method="bank")),
    )

    assert result.mandate is None
    assert missing_names(result) == ["receive_country"]


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"send_currency": "EUR"}, "EUR"),
        ({"receive_country": "UG"}, "UG"),
        ({"payout_method": "cash_pickup"}, "cash_pickup"),
    ],
)
def test_out_of_scope_is_rejected(changes, expected):
    result = parse_mandate(MUM, client=parse_client(extraction(**changes)))

    assert result.status == "out_of_scope"
    assert result.mandate is None
    assert expected in result.message


def test_out_of_scope_wins_over_missing_fields():
    result = parse_mandate(
        "Send 200 euros to Kenya",
        client=parse_client(
            extraction(send_currency="EUR", recipient_wallet=None)
        ),
    )
    assert result.status == "out_of_scope"


def test_gbp_is_in_scope():
    result = parse_mandate(MUM, client=parse_client(extraction(send_currency="GBP")))
    assert result.mandate.send_currency == "GBP"


def test_parse_without_a_key_makes_no_call():
    assert parse_mandate(MUM).status == "ai_unavailable"


def test_empty_text_asks_everything_without_a_call():
    client = MagicMock()
    result = parse_mandate("   ", client=client)

    assert result.status == "missing_fields"
    client.beta.messages.parse.assert_not_called()


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_parse_response_gives_no_mandate(stop_reason):
    client = parse_client(extraction(), stop_reason=stop_reason)
    result = parse_mandate(MUM, client=client)

    assert result.status == "ai_unavailable"
    assert result.mandate is None


def test_api_error_gives_no_mandate():
    client = MagicMock()
    client.beta.messages.parse.side_effect = anthropic.APIConnectionError(
        request=MagicMock()
    )
    assert parse_mandate(MUM, client=client).status == "ai_unavailable"


def test_default_model_and_override(monkeypatch):
    client = parse_client(extraction())
    parse_mandate(MUM, client=client)
    assert client.beta.messages.parse.call_args.kwargs["model"] == "claude-sonnet-5-5"

    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    parse_mandate(MUM, client=client)
    call = client.beta.messages.parse.call_args.kwargs
    assert call["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in call


# Grounding


def test_grounding_rejects_a_number_not_in_the_receipt(receipt):
    assert grounding_check("paypal saves KSh 999.", receipt) is False


def test_grounding_accepts_receipt_numbers_in_any_format(receipt):
    assert grounding_check("paypal delivers KSh 25,709.00 at 130.50.", receipt)


def test_grounding_rejects_spelled_out_numbers(receipt):
    assert grounding_check("It saves nine hundred shillings.", receipt) is False


@pytest.mark.parametrize("number", ["256", "2026", "001"])
def test_grounding_ignores_digits_in_hash_dates_and_ids(receipt, number):
    assert grounding_check(f"paypal saves {number}.", receipt) is False


def test_grounding_rejects_the_recipient_wallet(receipt):
    assert grounding_check("Paid to 254700000001.", receipt) is False


def test_grounding_rejects_the_deadline_because_the_receipt_omits_it(receipt):
    assert grounding_check("It misses the 120 minute deadline.", receipt) is False


def test_previous_explanation_cannot_ground_new_text(receipt):
    stored = receipt.model_copy(update={"explanation": "It saves 999."})
    assert grounding_check("It saves 999.", stored) is False


# Explanations


def test_template_mentions_every_excluded_route_and_reason(receipt):
    text = template_explanation(receipt)

    assert receipt.excluded_routes
    for route in receipt.excluded_routes:
        assert route.provider in text
        assert route.reason.value in text


def test_template_covers_selection_scores_and_data_mode(receipt):
    text = template_explanation(receipt)

    assert "Selected paypal" in text
    assert "paypal 100.0" in text
    assert "zero_fee_provider 98.9" in text
    assert "synthetic" in text


@pytest.mark.parametrize("case", ["a", "b"])
def test_template_passes_its_own_grounding_check(case):
    receipt = decide(load_mandate(case), load_quotes())
    assert grounding_check(template_explanation(receipt), receipt)


def test_template_with_no_selected_route():
    quotes = [q.model_copy(update={"fee": None}) for q in load_quotes()]
    receipt = decide(load_mandate("b"), quotes)

    text = template_explanation(receipt)

    assert "No route was selected" in text
    assert "disclosure_missing" in text


def test_grounded_ai_text_is_used(receipt):
    updated = with_explanation(receipt, client=explain_client(GOOD_AI_TEXT))

    assert updated.explanation == GOOD_AI_TEXT
    assert updated.explanation_source == "ai"


@pytest.mark.parametrize(
    "bad_text",
    [
        GOOD_AI_TEXT + " It saves KSh 999.",
        GOOD_AI_TEXT.replace("All values are synthetic.", ""),
        GOOD_AI_TEXT.replace("paypal", "the winning route"),
    ],
)
def test_bad_ai_text_is_replaced_by_the_template(receipt, bad_text):
    updated = with_explanation(receipt, client=explain_client(bad_text))

    assert updated.explanation == template_explanation(receipt)
    assert updated.explanation_source == "template"


def test_explain_returns_text(receipt):
    assert explain(receipt) == template_explanation(receipt)


def test_explanation_changes_nothing_else(receipt):
    updated = with_explanation(receipt, client=explain_client(GOOD_AI_TEXT))

    ignored = {"explanation", "explanation_source"}
    assert updated.model_dump(exclude=ignored) == receipt.model_dump(exclude=ignored)


def test_model_receives_only_the_receipt_json(receipt):
    client = explain_client(GOOD_AI_TEXT)
    ai_explanation(receipt, client=client)

    messages = client.beta.messages.create.call_args.kwargs["messages"]
    assert messages == [{"role": "user", "content": receipt.model_dump_json()}]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_explanation_falls_back(receipt, stop_reason):
    client = explain_client(GOOD_AI_TEXT, stop_reason=stop_reason)
    assert with_explanation(receipt, client=client).explanation_source == "template"


def test_api_error_falls_back_to_the_template(receipt):
    client = MagicMock()
    client.beta.messages.create.side_effect = anthropic.APIConnectionError(
        request=MagicMock()
    )
    assert with_explanation(receipt, client=client).explanation_source == "template"


# Demo


def run_demo(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["apb.demo", *args])
    return demo.main()


@pytest.mark.parametrize(
    "case, selected", [("a", "paypal"), ("b", "alternative_provider")]
)
def test_demo_runs_with_no_api_key(monkeypatch, capsys, case, selected):
    assert run_demo(monkeypatch, "--case", case) == 0

    output = capsys.readouterr().out
    assert "Explanation (template)" in output
    assert f"Selected {selected}" in output
    assert '"explanation_source": "template"' in output


def test_text_without_a_key_explains_itself(monkeypatch, capsys):
    assert run_demo(monkeypatch, "--text", MUM) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_text_runs_the_engine(monkeypatch, capsys):
    client = parse_client(extraction())
    client.beta.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=GOOD_AI_TEXT)],
        stop_reason="end_turn",
    )
    monkeypatch.setattr("apb.parser.get_client", lambda: client)
    monkeypatch.setattr("apb.explain.get_client", lambda: client)

    assert run_demo(monkeypatch, "--text", MUM) == 0

    output = capsys.readouterr().out
    assert '"selected_route": "paypal"' in output
    assert "Explanation (ai)" in output


def test_text_prints_questions(monkeypatch, capsys):
    client = parse_client(extraction(recipient_wallet=None))
    monkeypatch.setattr("apb.parser.get_client", lambda: client)

    assert run_demo(monkeypatch, "--text", MUM) == 0

    output = capsys.readouterr().out
    assert "recipient_wallet" in output
    assert "Receipt" not in output


def test_gbp_text_is_not_decided_on_usd_fixtures(monkeypatch, capsys):
    client = parse_client(extraction(send_currency="GBP"))
    monkeypatch.setattr("apb.parser.get_client", lambda: client)

    assert run_demo(monkeypatch, "--text", MUM) == 0

    output = capsys.readouterr().out
    assert "no decision was made" in output
    assert "Receipt" not in output
