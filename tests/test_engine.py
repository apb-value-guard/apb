import math
from datetime import timedelta

import pytest

from apb.decide import decide, mandate_hash
from apb.demo import load_mandate, load_quotes
from apb.models import ExcludedRoute, ReasonCode
from apb.scoring import score_routes


@pytest.fixture
def quotes():
    return load_quotes()


@pytest.fixture
def case_a():
    return load_mandate("a")


@pytest.fixture
def case_b():
    return load_mandate("b")


def with_changes(quotes, provider, **changes):
    return [
        quote.model_copy(update=changes) if quote.provider == provider else quote
        for quote in quotes
    ]


def reason_for(receipt, provider):
    return {route.provider: route.reason for route in receipt.excluded_routes}[provider]


def test_all_fixture_routes_are_synthetic(quotes):
    assert {quote.data_mode for quote in quotes} == {"synthetic"}


def test_fixture_recipient_amounts_match_fee_and_rate(quotes):
    for quote in quotes:
        expected = math.floor((quote.send_amount - quote.fee) * quote.fx_rate)
        assert quote.recipient_amount == expected, quote.provider


def test_case_a(case_a, quotes):
    receipt = decide(case_a, quotes)

    assert receipt.excluded_routes == [
        ExcludedRoute(
            provider="alternative_provider", reason=ReasonCode.DEADLINE_VIOLATION
        )
    ]
    assert receipt.selected_route == "paypal"
    assert receipt.delivered_value_score == 100.0
    assert receipt.scores == {"paypal": 100.0, "zero_fee_provider": 98.9}
    assert receipt.mandate_compliant is True
    assert receipt.disclosure_passed is True


def test_case_b(case_b, quotes):
    receipt = decide(case_b, quotes)

    assert receipt.excluded_routes == []
    assert receipt.selected_route == "alternative_provider"
    assert receipt.delivered_value_score == 100.0
    assert receipt.scores == {
        "alternative_provider": 100.0,
        "paypal": 99.8,
        "zero_fee_provider": 98.7,
    }


def test_excluded_routes_never_appear_in_scores(case_a, quotes):
    receipt = decide(case_a, quotes)

    excluded = {route.provider for route in receipt.excluded_routes}
    assert excluded == {"alternative_provider"}
    assert excluded.isdisjoint(receipt.scores)
    assert excluded.isdisjoint(receipt.eligible_routes)


def test_excluded_route_does_not_set_the_benchmark(case_a, quotes):
    # alternative_provider has the highest amount but is excluded in case A,
    # so the best eligible route must still score exactly 100.0.
    assert max(decide(case_a, quotes).scores.values()) == 100.0


def test_same_inputs_give_the_same_receipt(case_a, quotes):
    first = decide(case_a, quotes).model_dump_json()
    second = decide(case_a, quotes).model_dump_json()
    assert first == second


def test_quote_order_does_not_change_the_receipt(case_b, quotes):
    forward = decide(case_b, quotes).model_dump_json()
    backward = decide(case_b, list(reversed(quotes))).model_dump_json()
    assert forward == backward


def test_mandate_hash_ignores_decimal_formatting(case_a):
    reformatted = case_a.model_copy(
        update={"send_amount": case_a.send_amount.quantize(1)}
    )
    assert str(reformatted.send_amount) != str(case_a.send_amount)
    assert mandate_hash(reformatted) == mandate_hash(case_a)


def test_mandate_hash_changes_with_the_mandate(case_a, case_b):
    assert mandate_hash(case_a) != mandate_hash(case_b)


def test_payout_unsupported(case_b, quotes):
    changed = with_changes(quotes, "paypal", payout_method="bank_transfer")
    receipt = decide(case_b, changed)
    assert reason_for(receipt, "paypal") == ReasonCode.PAYOUT_UNSUPPORTED


def test_amount_out_of_limits(case_b, quotes):
    changed = with_changes(quotes, "paypal", send_amount=case_b.send_amount + 1)
    receipt = decide(case_b, changed)
    assert reason_for(receipt, "paypal") == ReasonCode.AMOUNT_OUT_OF_LIMITS


def test_quote_expires_at_its_expiry_time(case_b, quotes):
    paypal = next(quote for quote in quotes if quote.provider == "paypal")
    expiry = paypal.timestamp + timedelta(seconds=paypal.quote_expiry_seconds)

    just_before = decide(case_b, quotes, now=expiry - timedelta(seconds=1))
    at_expiry = decide(case_b, quotes, now=expiry)

    assert "paypal" in just_before.eligible_routes
    assert reason_for(at_expiry, "paypal") == ReasonCode.QUOTE_EXPIRED


@pytest.mark.parametrize(
    "field", ["fee", "fx_rate", "recipient_amount", "delivery_minutes"]
)
def test_disclosure_missing(case_b, quotes, field):
    changed = with_changes(quotes, "paypal", **{field: None})
    receipt = decide(case_b, changed)
    assert reason_for(receipt, "paypal") == ReasonCode.DISCLOSURE_MISSING


def test_zero_fee_counts_as_disclosed(case_b, quotes):
    assert "zero_fee_provider" in decide(case_b, quotes).eligible_routes


def test_no_eligible_routes(case_b, quotes):
    changed = [quote.model_copy(update={"fee": None}) for quote in quotes]
    receipt = decide(case_b, changed)

    assert receipt.selected_route is None
    assert receipt.delivered_value_score is None
    assert receipt.scores == {}
    assert receipt.mandate_compliant is False
    assert receipt.disclosure_passed is False


def test_no_quotes(case_b):
    receipt = decide(case_b, [])
    assert receipt.selected_route is None
    assert receipt.evidence_refs == []


def test_equal_value_selects_the_faster_route(case_b, quotes):
    changed = with_changes(
        quotes, "zero_fee_provider", recipient_amount=25759, delivery_minutes=5
    )
    assert decide(case_b, changed).selected_route == "zero_fee_provider"


def test_duplicate_provider_is_rejected(case_b, quotes):
    with pytest.raises(ValueError):
        decide(case_b, [quotes[0], quotes[0]])


def test_score_routes_with_nothing_eligible():
    assert score_routes([]) == {}


def test_receipt_leaves_later_stages_unset(case_a, quotes):
    receipt = decide(case_a, quotes)
    assert receipt.execution is None
    assert receipt.explanation is None
    assert receipt.quote_rechecked_at_commit is False
    assert receipt.user_approved is False
