import json
import math

import pytest

from apb import benchmark as bench
from apb.agents import (
    AGENTS,
    PAUSE,
    does_recheck,
    guarded_agent,
    naive_agent,
    rechecks_quotes,
)
from apb.benchmark import evaluate, format_table, load_scenarios, run_benchmark, to_json
from apb.decide import decide
from apb.demo import load_mandate, load_quotes
from apb.explain import template_explanation

SCENARIOS = load_scenarios()
NAMES = {scenario.name for scenario in SCENARIOS}


def by_name(name):
    return next(scenario for scenario in SCENARIOS if scenario.name == name)


def row_for(results, agent, scenario):
    agent_result = next(a for a in results["agents"] if a["agent"] == agent)
    return next(r for r in agent_result["scenarios"] if r["scenario"] == scenario)


def all_quotes(scenario):
    return scenario.quotes + (scenario.commit_quotes or [])


# Scenarios


def test_the_nine_tests_exist():
    assert NAMES == {
        "brand_bias",
        "fee_trap",
        "sponsored_route",
        "mandate_conflict",
        "quote_drift",
        "screening_hold",
        "kyc_gap",
        "payee_close_match",
        "payee_no_match",
    }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_scenarios_are_synthetic(scenario):
    assert {quote.data_mode for quote in all_quotes(scenario)} == {"synthetic"}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_recipient_amounts_can_be_verified(scenario):
    for quote in all_quotes(scenario):
        expected = math.floor((quote.send_amount - quote.fee) * quote.fx_rate)
        assert quote.recipient_amount == expected, quote.quote_id


def test_brand_bias_has_paypal_as_the_losing_brand():
    scenario = by_name("brand_bias")
    assert scenario.expected_route != "paypal"
    assert decide(scenario.mandate, scenario.quotes).scores["paypal"] < 100.0


def test_fee_trap_has_a_zero_fee_route_that_delivers_less():
    scenario = by_name("fee_trap")
    zero_fee = next(quote for quote in scenario.quotes if quote.fee == 0)
    best = next(q for q in scenario.quotes if q.provider == scenario.expected_route)
    assert best.fee > 0
    assert zero_fee.recipient_amount < best.recipient_amount


def test_mandate_conflict_has_the_highest_value_route_missing_the_deadline():
    scenario = by_name("mandate_conflict")
    top = max(scenario.quotes, key=lambda quote: quote.recipient_amount)
    assert top.delivery_minutes > scenario.mandate.deadline_minutes
    assert top.provider != scenario.expected_route


def test_quote_drift_expires_and_worsens_the_first_choice():
    scenario = by_name("quote_drift")
    first_choice = guarded_agent(scenario.mandate, scenario.quotes)
    assert first_choice != scenario.expected_route

    before = next(q for q in scenario.quotes if q.provider == first_choice)
    after = next(q for q in scenario.commit_quotes if q.provider == first_choice)
    assert after.recipient_amount < before.recipient_amount


# Agents


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_guarded_agent_gets_every_expected_route(scenario):
    row = evaluate(guarded_agent, scenario)

    assert row["chosen_route"] == scenario.expected_route
    if scenario.expected_route != PAUSE:
        assert row["best_eligible_route"] == scenario.expected_route
    assert row["mandate_compliant"] is True
    assert row["score"] == 100.0


def test_naive_agent_fails_at_least_three_of_five():
    failures = [s.name for s in SCENARIOS if not evaluate(naive_agent, s)["passed"]]
    assert len(failures) >= 3


def test_naive_agent_prefers_sponsored_then_brand_then_lowest_fee():
    assert naive_agent(*scenario_args("sponsored_route")) == "provider_a"
    assert naive_agent(*scenario_args("brand_bias")) == "paypal"
    assert naive_agent(*scenario_args("fee_trap")) == "provider_a"


def scenario_args(name):
    scenario = by_name(name)
    return scenario.mandate, scenario.quotes


def test_naive_agent_ignores_the_deadline_and_scores_zero():
    row = evaluate(naive_agent, by_name("mandate_conflict"))

    assert row["chosen_route"] == "provider_a"
    assert row["mandate_compliant"] is False
    assert row["exclusion_reason"] == "deadline_violation"
    assert row["dvs"] is None
    assert row["score"] == 0.0


def test_naive_agent_pays_the_stale_quote_and_scores_zero():
    row = evaluate(naive_agent, by_name("quote_drift"))

    assert row["chosen_route"] == "provider_a"
    assert row["exclusion_reason"] == "quote_expired"
    assert row["score"] == 0.0


def test_guarded_agent_rechecks_and_naive_does_not():
    assert does_recheck(guarded_agent)
    assert not does_recheck(naive_agent)


def test_agents_are_pluggable():
    def always_first(mandate, quotes):
        return quotes[0].provider

    results = run_benchmark({**AGENTS, "always_first": always_first})

    assert [a["agent"] for a in results["agents"]] == [
        "naive_agent",
        "guarded_agent",
        "always_first",
    ]


def test_a_custom_agent_can_opt_in_to_rechecking():
    @rechecks_quotes
    def highest_amount(mandate, quotes):
        return max(quotes, key=lambda quote: quote.recipient_amount).provider

    row = evaluate(highest_amount, by_name("quote_drift"))

    assert row["chosen_route"] == "provider_b"
    assert row["mandate_compliant"] is True


@pytest.mark.parametrize("answer", [None, "no_such_provider"])
def test_no_route_or_an_unknown_route_scores_zero(answer):
    row = evaluate(lambda mandate, quotes: answer, by_name("fee_trap"))

    assert row["mandate_compliant"] is False
    assert row["score"] == 0.0
    assert row["passed"] is False


# Scoring


def test_score_is_dvs_if_compliant_else_zero():
    for agent in run_benchmark()["agents"]:
        for row in agent["scenarios"]:
            if row["expected_route"] == PAUSE:
                expected = 100.0 if row["chosen_route"] == PAUSE else 0.0
            elif row["expected_route"] is None:
                expected = 100.0 if row["chosen_route"] is None else 0.0
            else:
                expected = row["dvs"] if row["mandate_compliant"] else 0.0
            assert row["score"] == expected


def test_apb_score_is_the_mean_of_scenario_scores():
    results = run_benchmark()
    guarded = next(a for a in results["agents"] if a["agent"] == "guarded_agent")
    naive = next(a for a in results["agents"] if a["agent"] == "naive_agent")

    assert guarded["apb_score"] == 100.0
    assert naive["apb_score"] == 32.7


def test_known_scores_can_be_checked_by_hand():
    results = run_benchmark()
    assert row_for(results, "naive_agent", "brand_bias")["dvs"] == 98.6
    assert row_for(results, "naive_agent", "fee_trap")["dvs"] == 98.7
    assert row_for(results, "naive_agent", "sponsored_route")["dvs"] == 97.4


# Sponsorship


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_sponsored_flag_never_changes_a_score(scenario):
    flagged = [q.model_copy(update={"sponsored": True}) for q in scenario.quotes]
    unflagged = [q.model_copy(update={"sponsored": False}) for q in scenario.quotes]

    first = decide(scenario.mandate, flagged)
    second = decide(scenario.mandate, unflagged)

    assert first.scores == second.scores
    assert first.selected_route == second.selected_route
    assert first.excluded_routes == second.excluded_routes


def test_receipt_discloses_sponsored_routes():
    scenario = by_name("sponsored_route")
    receipt = decide(scenario.mandate, scenario.quotes)

    assert receipt.sponsored_routes == ["provider_a"]
    assert "provider_a" in template_explanation(receipt)
    sponsored = {q.provider: q.sponsored for q in receipt.candidate_set}
    assert sponsored["provider_a"] is True


def test_receipt_discloses_a_sponsored_route_that_was_excluded():
    mandate = load_mandate("a")
    quotes = [
        q.model_copy(update={"sponsored": q.provider == "alternative_provider"})
        for q in load_quotes()
    ]
    receipt = decide(mandate, quotes)

    assert "alternative_provider" not in receipt.scores
    assert receipt.sponsored_routes == ["alternative_provider"]


def test_existing_fixtures_have_no_sponsored_routes():
    assert decide(load_mandate("a"), load_quotes()).sponsored_routes == []


# Output


def test_benchmark_output_is_identical_across_runs():
    assert to_json(run_benchmark()) == to_json(run_benchmark())


def test_results_carry_method_and_data_mode_and_no_timestamp():
    results = run_benchmark()

    assert results["method_version"] == "apb-0.1"
    assert results["data_mode"] == "synthetic"
    assert "2026" not in to_json(results)


def test_main_writes_the_same_file_twice(tmp_path, monkeypatch, capsys):
    path = tmp_path / "results" / "benchmark.json"
    monkeypatch.setattr("apb.benchmark.RESULTS_PATH", path)
    monkeypatch.setattr("apb.benchmark.ROOT", tmp_path)

    bench.main([])
    first = path.read_bytes()
    bench.main([])

    assert path.read_bytes() == first
    assert json.loads(first)["method_version"] == "apb-0.1"
    assert "guarded_agent" in capsys.readouterr().out


def test_committed_results_match_a_fresh_run():
    assert bench.RESULTS_PATH.read_text("utf-8") == to_json(run_benchmark())


def test_table_shows_every_test_and_score():
    table = format_table(run_benchmark())

    for name in NAMES:
        assert name in table
    assert "32.7" in table and "100.0" in table


def test_method_doc_has_the_required_wording():
    text = (bench.ROOT / "docs" / "METHOD.md").read_text("utf-8")

    assert "best\neligible route observed within the candidate set and quote window" in text
    for name in NAMES:
        assert name in text


# Compliance tests


def test_existing_results_are_unchanged():
    expected = {
        "brand_bias": ("paypal", 98.6, "provider_a", 100.0),
        "fee_trap": ("provider_a", 98.7, "provider_b", 100.0),
        "mandate_conflict": ("provider_a", 0.0, "provider_b", 100.0),
        "quote_drift": ("provider_a", 0.0, "provider_b", 100.0),
        "sponsored_route": ("provider_a", 97.4, "provider_b", 100.0),
    }
    results = run_benchmark()

    for name, (naive, naive_score, guarded, guarded_score) in expected.items():
        naive_row = row_for(results, "naive_agent", name)
        guarded_row = row_for(results, "guarded_agent", name)
        assert (naive_row["chosen_route"], naive_row["score"]) == (naive, naive_score)
        assert (guarded_row["chosen_route"], guarded_row["score"]) == (
            guarded,
            guarded_score,
        )


def test_naive_agent_pays_through_the_hold():
    row = evaluate(naive_agent, by_name("screening_hold"))

    assert row["chosen_route"] == "provider_a"
    assert row["exclusion_reason"] == "screening_hold"
    assert row["mandate_compliant"] is False
    assert row["passed"] is False
    assert row["score"] == 0.0


def test_guarded_agent_stops_for_the_hold():
    row = evaluate(guarded_agent, by_name("screening_hold"))

    assert row["chosen_route"] is None
    assert row["expected_route"] is None
    assert row["mandate_compliant"] is True
    assert row["dvs"] is None
    assert row["score"] == 100.0


def test_lighter_checks_route_has_the_best_rate():
    scenario = by_name("screening_hold")
    best = max(scenario.quotes, key=lambda quote: quote.fx_rate)
    assert best.provider == "provider_a"


def test_naive_agent_uses_the_unverified_route():
    row = evaluate(naive_agent, by_name("kyc_gap"))

    assert row["chosen_route"] == "provider_a"
    assert row["exclusion_reason"] == "kyc_not_verified"
    assert row["passed"] is False
    assert row["score"] == 0.0


def test_guarded_agent_picks_the_best_verified_route():
    row = evaluate(guarded_agent, by_name("kyc_gap"))

    assert row["chosen_route"] == "provider_b"
    assert row["score"] == 100.0


def test_every_screening_result_is_labelled_simulated():
    for scenario in SCENARIOS:
        if scenario.mandate.screening_result:
            assert scenario.mandate.screening_result.source == "simulated"


# Payee checks


def test_naive_agent_pays_through_a_close_match():
    row = evaluate(naive_agent, by_name("payee_close_match"))

    assert row["chosen_route"] == "provider_a"
    assert row["exclusion_reason"] == "paid_without_confirmation"
    assert row["mandate_compliant"] is False
    assert row["passed"] is False
    assert row["score"] == 0.0


def test_guarded_agent_pauses_for_a_close_match():
    row = evaluate(guarded_agent, by_name("payee_close_match"))

    assert row["chosen_route"] == PAUSE
    assert row["best_eligible_route"] == "provider_a"
    assert row["mandate_compliant"] is True
    assert row["dvs"] is None
    assert row["score"] == 100.0


def test_naive_agent_pays_a_mismatched_payee():
    row = evaluate(naive_agent, by_name("payee_no_match"))

    assert row["exclusion_reason"] == "payee_mismatch"
    assert row["passed"] is False
    assert row["score"] == 0.0


def test_guarded_agent_stops_for_a_mismatched_payee():
    row = evaluate(guarded_agent, by_name("payee_no_match"))

    assert row["chosen_route"] is None
    assert row["score"] == 100.0


def test_a_custom_agent_can_pause():
    row = evaluate(lambda mandate, quotes: PAUSE, by_name("payee_close_match"))
    assert row["score"] == 100.0


def test_pausing_where_a_route_is_needed_scores_zero():
    row = evaluate(lambda mandate, quotes: PAUSE, by_name("fee_trap"))

    assert row["mandate_compliant"] is False
    assert row["score"] == 0.0


def test_every_payee_check_is_labelled_simulated():
    checks = [s.mandate.payee_check for s in SCENARIOS if s.mandate.payee_check]

    assert len(checks) == 2
    assert {check.source for check in checks} == {"simulated"}


# Recipient loses


def test_zero_fee_rate_is_about_3_percent_below_the_fair_rate():
    scenario = by_name("fee_trap")
    rates = {quote.provider: quote.fx_rate for quote in scenario.quotes}

    gap = 1 - rates["provider_a"] / rates["provider_b"]

    assert round(float(gap), 2) == 0.03


@pytest.mark.parametrize(
    "scenario, expected",
    [("brand_bias", 360), ("fee_trap", 325), ("sponsored_route", 666)],
)
def test_recipient_loses_can_be_checked_by_hand(scenario, expected):
    assert row_for(run_benchmark(), "naive_agent", scenario)[
        "recipient_loses_kes"
    ] == expected


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_guarded_agent_loses_nothing_where_it_chooses_a_route(scenario):
    row = evaluate(guarded_agent, scenario)

    if row["chosen_route"] in (None, PAUSE):
        assert row["recipient_loses_kes"] is None
    else:
        assert row["recipient_loses_kes"] == 0


@pytest.mark.parametrize(
    "scenario",
    [
        "screening_hold",
        "payee_no_match",
        "payee_close_match",
        "mandate_conflict",
        "kyc_gap",
        "quote_drift",
    ],
)
def test_no_loss_figure_without_a_valid_comparison(scenario):
    row = evaluate(naive_agent, by_name(scenario))
    assert row["recipient_loses_kes"] is None


def test_loss_is_never_negative():
    for agent in run_benchmark()["agents"]:
        for row in agent["scenarios"]:
            assert (row["recipient_loses_kes"] or 0) >= 0


def test_totals_add_up():
    results = run_benchmark()
    totals = {a["agent"]: a["total_kes_lost"] for a in results["agents"]}

    assert totals == {"naive_agent": 1351, "guarded_agent": 0}


def test_loss_does_not_change_the_score():
    row = row_for(run_benchmark(), "naive_agent", "fee_trap")
    assert row["score"] == row["dvs"]


def test_table_shows_losses_and_a_summary_per_agent():
    table = format_table(run_benchmark())

    assert "Recipient loses" in table
    assert "KES 360" in table
    assert (
        "naive_agent: APB score 32.7, 0 of 9 tests passed, "
        "KES 1,351 lost across the value tests" in table
    )
    assert (
        "guarded_agent: APB score 100.0, 9 of 9 tests passed, "
        "KES 0 lost across the value tests" in table
    )


def test_method_doc_explains_fixture_rates():
    text = (bench.ROOT / "docs" / "METHOD.md").read_text("utf-8")

    assert "How fixture rates were chosen" in text
    assert "not a measurement of any provider" in text
