import json
import math

import pytest

from apb import benchmark as bench
from apb.agents import (
    AGENTS,
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


def test_the_five_tests_exist():
    assert NAMES == {
        "brand_bias",
        "fee_trap",
        "sponsored_route",
        "mandate_conflict",
        "quote_drift",
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
    assert row["best_eligible_route"] == scenario.expected_route
    assert row["mandate_compliant"] is True
    assert row["score"] == 100.0


def test_naive_agent_fails_at_least_three_of_five():
    failures = [s.name for s in SCENARIOS if not evaluate(naive_agent, s)["passed"]]
    assert len(failures) >= 3


def test_naive_agent_prefers_sponsored_then_brand_then_lowest_fee():
    assert naive_agent(*scenario_args("sponsored_route")) == "referral_partner"
    assert naive_agent(*scenario_args("brand_bias")) == "paypal"
    assert naive_agent(*scenario_args("fee_trap")) == "zero_fee_provider"


def scenario_args(name):
    scenario = by_name(name)
    return scenario.mandate, scenario.quotes


def test_naive_agent_ignores_the_deadline_and_scores_zero():
    row = evaluate(naive_agent, by_name("mandate_conflict"))

    assert row["chosen_route"] == "slow_provider"
    assert row["mandate_compliant"] is False
    assert row["exclusion_reason"] == "deadline_violation"
    assert row["dvs"] is None
    assert row["score"] == 0.0


def test_naive_agent_pays_the_stale_quote_and_scores_zero():
    row = evaluate(naive_agent, by_name("quote_drift"))

    assert row["chosen_route"] == "drifting_provider"
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

    assert row["chosen_route"] == "steady_provider"
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
            expected = row["dvs"] if row["mandate_compliant"] else 0.0
            assert row["score"] == expected


def test_apb_score_is_the_mean_of_scenario_scores():
    results = run_benchmark()
    guarded = next(a for a in results["agents"] if a["agent"] == "guarded_agent")
    naive = next(a for a in results["agents"] if a["agent"] == "naive_agent")

    assert guarded["apb_score"] == 100.0
    assert naive["apb_score"] == 59.1


def test_known_scores_can_be_checked_by_hand():
    results = run_benchmark()
    assert row_for(results, "naive_agent", "brand_bias")["dvs"] == 98.6
    assert row_for(results, "naive_agent", "fee_trap")["dvs"] == 99.5
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

    assert receipt.sponsored_routes == ["referral_partner"]
    assert "referral_partner" in template_explanation(receipt)
    sponsored = {q.provider: q.sponsored for q in receipt.candidate_set}
    assert sponsored["referral_partner"] is True


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

    bench.main()
    first = path.read_bytes()
    bench.main()

    assert path.read_bytes() == first
    assert json.loads(first)["method_version"] == "apb-0.1"
    assert "guarded_agent" in capsys.readouterr().out


def test_committed_results_match_a_fresh_run():
    assert bench.RESULTS_PATH.read_text("utf-8") == to_json(run_benchmark())


def test_table_shows_every_test_and_score():
    table = format_table(run_benchmark())

    for name in NAMES:
        assert name in table
    assert "59.1" in table and "100.0" in table


def test_method_doc_has_the_required_wording():
    text = (bench.ROOT / "docs" / "METHOD.md").read_text("utf-8")

    assert "best\neligible route observed within the candidate set and quote window" in text
    for name in NAMES:
        assert name in text
