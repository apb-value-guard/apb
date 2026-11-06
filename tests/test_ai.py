import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import pytest

from apb import ai, demo
from apb.decide import decide
from apb.demo import load_mandate, load_quotes
from apb.explain import (
    ai_explanation,
    allowed_numbers,
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
    monkeypatch.setenv("AI_BACKEND", "none")
    monkeypatch.setattr("apb.ai.load_dotenv", lambda: False)

    def refuse(*args, **kwargs):
        raise AssertionError("a real AI backend was called")

    monkeypatch.setattr("apb.ai.Anthropic", refuse)
    monkeypatch.setattr("apb.ai.subprocess.run", refuse)


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


def cli_reply(text, returncode=0, **overrides):
    reply = {"is_error": False, "stop_reason": "end_turn", "result": text}
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=json.dumps({**reply, **overrides}),
        stderr="",
    )


@pytest.fixture
def fake_claude(monkeypatch):
    """Select the CLI backend, with the subprocess mocked."""
    run = MagicMock()
    monkeypatch.setenv("AI_BACKEND", "claude_cli")
    monkeypatch.setattr("apb.ai.shutil.which", lambda name: "claude.CMD")
    monkeypatch.setattr("apb.ai.subprocess.run", run)
    return run


def use_api_client(monkeypatch, client):
    monkeypatch.setenv("AI_BACKEND", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("apb.ai.get_client", lambda: client)


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


def test_country_inferred_by_code_overrides_the_models_question():
    # The live model asks for the country here, as the text doesn't state it.
    flagged = MissingField(field="receive_country", question="Which country?")
    result = parse_mandate(
        MUM, client=parse_client(extraction(missing_fields=[flagged]))
    )

    assert result.status == "ok"
    assert result.mandate.receive_country == "KE"


def test_country_question_stays_when_code_cannot_infer_it():
    flagged = MissingField(field="receive_country", question="Which country?")
    result = parse_mandate(
        "Send $200 to M-PESA 255700000001 within 2 hours",
        client=parse_client(
            extraction(recipient_wallet="255700000001", missing_fields=[flagged])
        ),
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


def test_parse_with_ai_off_makes_no_call():
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


def test_grounding_rejects_mandate_numbers_without_the_mandate(receipt):
    assert grounding_check("It misses the 120 minute deadline.", receipt) is False


@pytest.mark.parametrize(
    "text",
    [
        "It misses the 120 minute deadline.",
        "paypal delivers for 200 USD.",
        "The approval limit is 5.",
    ],
)
def test_grounding_accepts_numbers_from_its_mandate(receipt, text):
    assert grounding_check(text, receipt, load_mandate("a"))


def test_grounding_accepts_the_wallet_from_its_mandate():
    mandate = load_mandate("a").model_copy(
        update={"recipient_wallet": "254700000001"}
    )
    receipt = decide(mandate, load_quotes())

    assert grounding_check("Paid to 254700000001.", receipt, mandate)
    assert grounding_check("Paid to 254700000001.", receipt) is False


@pytest.mark.parametrize(
    "text",
    [
        "It must arrive within 2 hours.",
        "Delivery takes 24 hours.",
        "paypal delivers 289 more.",
        "paypal gives 50 less.",
        "zero_fee_provider is 1.1% behind.",
        "Of the two eligible routes, paypal won.",
    ],
)
def test_grounding_rejects_derived_numbers_even_with_the_mandate(receipt, text):
    assert grounding_check(text, receipt, load_mandate("a")) is False


def test_another_mandate_cannot_supply_allowed_numbers(receipt):
    with pytest.raises(ValueError):
        grounding_check("The deadline is 2880 minutes.", receipt, load_mandate("b"))


def test_allowed_numbers_are_as_written_and_exclude_ids_and_dates(receipt):
    numbers = allowed_numbers(receipt, load_mandate("a"))

    assert {"120", "200", "2.99", "130.5", "25709", "98.9", "100.0"} <= set(numbers)
    assert not {"2026", "001", "256"} & set(numbers)


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


def test_model_receives_the_receipt_mandate_and_allowed_numbers(receipt):
    mandate = load_mandate("a")
    client = explain_client(GOOD_AI_TEXT)
    ai_explanation(receipt, mandate, client=client)

    call = client.beta.messages.create.call_args.kwargs
    sent = json.loads(call["messages"][0]["content"])
    decision_only = {"quote_rechecked_at_commit", "user_approved", "execution"}
    assert sent == {
        "receipt": receipt.model_dump(mode="json", exclude=decision_only),
        "mandate": mandate.model_dump(mode="json"),
        "allowed_numbers": allowed_numbers(receipt, mandate),
    }


def test_prompt_states_the_number_rules(receipt):
    client = explain_client(GOOD_AI_TEXT)
    ai_explanation(receipt, client=client)

    system = client.beta.messages.create.call_args.kwargs["system"]
    assert "allowed_numbers" in system
    assert "Copy numbers exactly as written" in system
    assert "No arithmetic, no differences, no percentages" in system
    assert '"120 minutes", not "2 hours"' in system


def test_rejection_reason_is_logged(receipt, caplog):
    text = GOOD_AI_TEXT + " Of the two routes it saves 999."
    with_explanation(receipt, client=explain_client(text))

    assert "number words: two" in caplog.text
    assert "numbers not allowed: 999" in caplog.text


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
def test_demo_runs_with_ai_off(monkeypatch, capsys, case, selected):
    assert run_demo(monkeypatch, "--case", case) == 0

    output = capsys.readouterr().out
    assert "Explanation (template)" in output
    assert f"Selected {selected}" in output
    assert '"explanation_source": "template"' in output


def test_text_with_ai_off_explains_itself(monkeypatch, capsys):
    assert run_demo(monkeypatch, "--text", MUM) == 2
    assert "Use --case a or --case b" in capsys.readouterr().out


def test_text_runs_the_engine(monkeypatch, capsys):
    client = parse_client(extraction())
    client.beta.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=GOOD_AI_TEXT)],
        stop_reason="end_turn",
    )
    use_api_client(monkeypatch, client)

    assert run_demo(monkeypatch, "--text", MUM) == 0

    output = capsys.readouterr().out
    assert "Parsed mandate" in output
    assert '"selected_route": "paypal"' in output
    assert "Explanation (ai)" in output


def test_text_prints_questions(monkeypatch, capsys):
    use_api_client(monkeypatch, parse_client(extraction(recipient_wallet=None)))

    assert run_demo(monkeypatch, "--text", MUM) == 0

    output = capsys.readouterr().out
    assert "recipient_wallet" in output
    assert "Receipt" not in output


def test_gbp_text_is_not_decided_on_usd_fixtures(monkeypatch, capsys):
    use_api_client(monkeypatch, parse_client(extraction(send_currency="GBP")))

    assert run_demo(monkeypatch, "--text", MUM) == 0

    output = capsys.readouterr().out
    assert "no decision was made" in output
    assert "Receipt" not in output


# Backend selection


@pytest.mark.parametrize(
    "value, key, expected",
    [
        ("", None, "claude_cli"),
        ("claude_cli", None, "claude_cli"),
        ("CLAUDE_CLI", None, "claude_cli"),
        ("none", "test-key", "none"),
        ("api", "test-key", "api"),
        ("api", None, "none"),
        ("something_else", "test-key", "none"),
    ],
)
def test_backend_selection(monkeypatch, value, key, expected):
    monkeypatch.setenv("AI_BACKEND", value)
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    assert ai.selected_backend() == expected


# Claude CLI backend


def test_cli_parses_a_mandate(fake_claude):
    fake_claude.return_value = cli_reply(extraction().model_dump_json())

    result = parse_mandate(MUM)

    assert result.status == "ok"
    assert result.mandate.deadline_minutes == 120


def test_cli_accepts_a_fenced_reply(fake_claude):
    fenced = f"```json\n{extraction().model_dump_json()}\n```"
    fake_claude.return_value = cli_reply(fenced)
    assert parse_mandate(MUM).status == "ok"


def test_cli_results_still_pass_through_the_code_checks(fake_claude):
    invented = extraction(recipient_wallet="254799999999")
    fake_claude.return_value = cli_reply(invented.model_dump_json())

    result = parse_mandate(MUM)

    assert result.mandate is None
    assert "recipient_wallet" in missing_names(result)


def test_cli_grounded_explanation_is_used(fake_claude, receipt):
    fake_claude.return_value = cli_reply(GOOD_AI_TEXT)

    updated = with_explanation(receipt)

    assert updated.explanation == GOOD_AI_TEXT
    assert updated.explanation_source == "ai"


def test_cli_explanation_is_grounding_checked(fake_claude, receipt):
    fake_claude.return_value = cli_reply(GOOD_AI_TEXT + " It saves KSh 999.")

    updated = with_explanation(receipt)

    assert updated.explanation == template_explanation(receipt)
    assert updated.explanation_source == "template"


def test_cli_text_goes_through_stdin_not_arguments(fake_claude):
    text = 'Send $200 & del "everything" | %PATH%'
    fake_claude.return_value = cli_reply(extraction().model_dump_json())

    parse_mandate(text)

    call = fake_claude.call_args
    assert call.kwargs["input"] == text
    assert not any("everything" in argument for argument in call.args[0])


def test_cli_runs_without_tools_or_project_context(fake_claude, receipt):
    seen = {}

    def run(arguments, **kwargs):
        seen["arguments"] = arguments
        seen["workspace"] = sorted(path.name for path in Path(kwargs["cwd"]).iterdir())
        return cli_reply(GOOD_AI_TEXT)

    fake_claude.side_effect = run

    with_explanation(receipt)

    arguments = seen["arguments"]
    assert arguments[1:4] == ["-p", "--output-format", "json"]
    assert arguments[arguments.index("--tools") + 1] == ""
    assert "--safe-mode" in arguments
    assert "--strict-mcp-config" in arguments
    assert seen["workspace"] == ["system.txt"]


def test_cli_never_receives_an_api_key(fake_claude, monkeypatch, receipt):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "paid-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "paid-token")
    fake_claude.return_value = cli_reply(GOOD_AI_TEXT)

    with_explanation(receipt)

    environment = fake_claude.call_args.kwargs["env"]
    assert "ANTHROPIC_API_KEY" not in environment
    assert "ANTHROPIC_AUTH_TOKEN" not in environment


def test_cli_model_defaults_and_can_be_overridden(fake_claude, monkeypatch, receipt):
    fake_claude.return_value = cli_reply(GOOD_AI_TEXT)

    def model_argument():
        with_explanation(receipt)
        arguments = fake_claude.call_args.args[0]
        return arguments[arguments.index("--model") + 1]

    assert model_argument() == "claude-sonnet-5-5"
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    assert model_argument() == "claude-haiku-5-5"


def test_cli_rejects_an_unsafe_model_name(fake_claude, monkeypatch, receipt):
    monkeypatch.setenv("ANTHROPIC_MODEL", "sonnet & del x")

    assert with_explanation(receipt).explanation_source == "template"
    fake_claude.assert_not_called()


def test_missing_claude_command_falls_back(monkeypatch, receipt):
    monkeypatch.setenv("AI_BACKEND", "claude_cli")
    monkeypatch.setattr("apb.ai.shutil.which", lambda name: None)

    assert with_explanation(receipt).explanation_source == "template"
    result = parse_mandate(MUM)
    assert result.status == "ai_unavailable"
    assert result.mandate is None


@pytest.mark.parametrize(
    "outcome",
    [
        cli_reply("", returncode=1),
        cli_reply("", is_error=True),
        cli_reply(GOOD_AI_TEXT, stop_reason="max_tokens"),
        cli_reply(GOOD_AI_TEXT, stop_reason="refusal"),
        cli_reply("   "),
        subprocess.CompletedProcess([], 0, stdout="not json", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="[]", stderr=""),
        subprocess.TimeoutExpired(cmd="claude", timeout=1),
        OSError("cannot start"),
    ],
)
def test_cli_failures_fall_back(fake_claude, receipt, outcome):
    fake_claude.side_effect = [outcome, outcome]

    assert with_explanation(receipt).explanation_source == "template"
    result = parse_mandate(MUM)
    assert result.status == "ai_unavailable"
    assert result.mandate is None


def test_cli_reply_that_breaks_the_schema_gives_no_mandate(fake_claude):
    fake_claude.return_value = cli_reply('{"send_amount": "200"}')

    result = parse_mandate(MUM)

    assert result.status == "ai_unavailable"
    assert result.mandate is None


def test_demo_case_works_with_cli_missing(monkeypatch, capsys):
    monkeypatch.setenv("AI_BACKEND", "claude_cli")
    monkeypatch.setattr("apb.ai.shutil.which", lambda name: None)

    assert run_demo(monkeypatch, "--case", "a") == 0
    assert "Explanation (template)" in capsys.readouterr().out


# Screening explanations


def held_receipt():
    mandate = load_mandate("c")
    return decide(mandate, load_quotes()), mandate


def test_template_for_a_hold_passes_grounding():
    receipt, mandate = held_receipt()
    assert grounding_check(template_explanation(receipt), receipt, mandate)


def test_ai_text_must_say_a_simulated_hold_stopped_payment():
    receipt, mandate = held_receipt()
    silent = "No route was chosen. All values are synthetic."

    updated = with_explanation(receipt, mandate, client=explain_client(silent))

    assert updated.explanation_source == "template"


def test_ai_text_about_the_hold_is_used():
    receipt, mandate = held_receipt()
    text = "Payment stopped for a simulated screening hold. All values are synthetic."

    updated = with_explanation(receipt, mandate, client=explain_client(text))

    assert updated.explanation == text
    assert updated.explanation_source == "ai"


# Payee explanations


@pytest.mark.parametrize("case", ["d", "e"])
def test_payee_templates_pass_grounding(case):
    mandate = load_mandate(case)
    receipt = decide(mandate, load_quotes())

    assert grounding_check(template_explanation(receipt), receipt, mandate)


def test_template_shows_both_names_for_a_close_match():
    text = template_explanation(decide(load_mandate("d"), load_quotes()))

    assert "Jane Sample Wanjiku" in text and "J Sample Wanjiku" in text


@pytest.mark.parametrize(
    "case, text",
    [
        ("d", "paypal was selected. All values are synthetic."),
        ("e", "No route was chosen. All values are synthetic."),
    ],
)
def test_ai_text_must_mention_the_payee_result(case, text):
    mandate = load_mandate(case)
    receipt = decide(mandate, load_quotes())

    updated = with_explanation(receipt, mandate, client=explain_client(text))

    assert updated.explanation_source == "template"
