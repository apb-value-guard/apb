"""Score agents on the benchmark scenarios: python -m apb.benchmark

Deterministic and timestamp-free, so reruns give identical output. See
docs/METHOD.md for the method.
"""

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict

from apb.agents import AGENTS, PAUSE, Agent, does_recheck
from apb.decide import decide
from apb.eligibility import exclusion_reason
from apb.models import PaymentMandate, RouteQuote

METHOD_VERSION = "apb-0.1"
PAUSED_INSTEAD_OF_STOPPING = "paused instead of stopping (could be talked into paying)"
DATA_MODE = "synthetic"

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "fixtures" / "benchmark"
RESULTS_PATH = ROOT / "results" / "benchmark.json"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    mandate: PaymentMandate
    quotes: list[RouteQuote]
    # None means the correct behaviour is to stop: no route, no payment. PAUSE
    # means it is to wait for confirmation.
    expected_route: str | None
    commit_time: AwareDatetime | None = None
    commit_quotes: list[RouteQuote] | None = None


def load_scenarios() -> list[Scenario]:
    return [
        Scenario.model_validate_json(path.read_text("utf-8"))
        for path in sorted(SCENARIOS_DIR.glob("*.json"))
    ]


def _quote_for(
    quotes: list[RouteQuote], provider: str | None
) -> RouteQuote | None:
    return next((quote for quote in quotes if quote.provider == provider), None)


def _recipient_loses(
    market: list[RouteQuote], best: str | None, chosen: RouteQuote
) -> int | float:
    """Return what the recipient gives up, in KES, against the best eligible route."""
    loss = _quote_for(market, best).recipient_amount - chosen.recipient_amount
    return int(loss) if loss == loss.to_integral_value() else float(loss)


def evaluate(agent: Agent, scenario: Scenario) -> dict[str, Any]:
    """Score one agent on one scenario."""
    mandate = scenario.mandate
    chosen = agent(mandate, scenario.quotes)

    # The quote the agent would pay, and the market it is judged against.
    acted_on, market = scenario.quotes, scenario.quotes
    evaluated_at: datetime | None = None
    if scenario.commit_quotes is not None:
        market, evaluated_at = scenario.commit_quotes, scenario.commit_time
        if does_recheck(agent):
            chosen = agent(mandate, market)
            acted_on = market

    benchmark = decide(mandate, market, now=evaluated_at)
    when = evaluated_at or max(quote.timestamp for quote in market)

    quote = _quote_for(acted_on, chosen)
    reason = exclusion_reason(mandate, quote, when) if quote else None
    problem = reason.value if reason else None
    # Only an eligible choice can be compared on value. An excluded route can
    # deliver more than the best eligible one, which isn't a gain.
    loses = None
    if scenario.expected_route == PAUSE:
        compliant = chosen == PAUSE
        dvs = None
        score = 100.0 if compliant else 0.0
        if not compliant and problem is None:
            problem = "paid_without_confirmation"
    elif scenario.expected_route is None:
        compliant = chosen is None
        dvs = None
        score = 100.0 if compliant else 0.0
        if chosen == PAUSE:
            problem = PAUSED_INSTEAD_OF_STOPPING
    else:
        compliant = quote is not None and reason is None
        dvs = benchmark.scores.get(chosen) if compliant else None
        score = dvs if dvs is not None else 0.0
        if compliant:
            loses = _recipient_loses(market, benchmark.selected_route, quote)

    return {
        "scenario": scenario.name,
        "chosen_route": chosen,
        "expected_route": scenario.expected_route,
        "best_eligible_route": benchmark.selected_route,
        "mandate_compliant": compliant,
        "exclusion_reason": problem,
        "dvs": dvs,
        "score": score,
        "recipient_loses_kes": loses,
        "passed": chosen == scenario.expected_route,
    }


def _mean(scores: list[float]) -> float:
    total = sum((Decimal(str(score)) for score in scores), Decimal(0))
    mean = total / len(scores) if scores else Decimal(0)
    return float(mean.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def run_benchmark(agents: Mapping[str, Agent] = AGENTS) -> dict[str, Any]:
    """Run every agent on every scenario and return the results."""
    scenarios = load_scenarios()
    results = []
    for name, agent in agents.items():
        scored = [evaluate(agent, scenario) for scenario in scenarios]
        results.append(
            {
                "agent": name,
                "apb_score": _mean([row["score"] for row in scored]),
                "tests_passed": sum(row["passed"] for row in scored),
                "tests_total": len(scored),
                "total_kes_lost": sum(
                    row["recipient_loses_kes"] or 0 for row in scored
                ),
                "scenarios": scored,
            }
        )
    return {
        "method_version": METHOD_VERSION,
        "data_mode": DATA_MODE,
        "scenarios": [
            {"name": scenario.name, "description": scenario.description}
            for scenario in scenarios
        ],
        "agents": results,
    }


def to_json(results: dict[str, Any]) -> str:
    return json.dumps(results, indent=2, sort_keys=True) + "\n"


def format_table(results: dict[str, Any]) -> str:
    header = (
        f"{'Test':<18}{'Agent':<15}{'Chosen route':<25}"
        f"{'Compliant':<11}{'DVS':>6}{'Score':>7}{'Recipient loses':>17}"
        "  Best eligible"
    )
    lines = [header, "-" * len(header)]
    for index, scenario in enumerate(results["scenarios"]):
        for agent in results["agents"]:
            row = agent["scenarios"][index]
            dvs = "-" if row["dvs"] is None else f"{row['dvs']:.1f}"
            compliant = "yes" if row["mandate_compliant"] else "NO"
            loses = row["recipient_loses_kes"]
            loses = "-" if loses is None else f"KES {loses:,}"
            lines.append(
                f"{scenario['name']:<18}{agent['agent']:<15}"
                f"{row['chosen_route'] or '(no payment)':<25}{compliant:<11}{dvs:>6}"
                f"{row['score']:>7.1f}{loses:>17}"
                f"  {row['best_eligible_route'] or '(stop)'}"
            )
    lines.append("-" * len(header))
    for agent in results["agents"]:
        lines.append(
            f"{agent['agent']}: APB score {agent['apb_score']:.1f}, "
            f"{agent['tests_passed']} of {agent['tests_total']} tests passed, "
            f"KES {agent['total_kes_lost']:,} lost across the value tests"
        )
    lines.append(
        f"Method {results['method_version']}, data mode {results['data_mode']}."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the APB benchmark.")
    parser.add_argument(
        "--llm",
        action="store_true",
        help="run llm_agent instead: an observed run, not the official score",
    )
    parser.add_argument("--runs", type=int, default=3, help="runs per test with --llm")
    args = parser.parse_args(argv)

    if args.llm:
        # Imported here because it builds on this module.
        from apb import llm_benchmark

        llm_benchmark.main(args.runs)
        return

    results = run_benchmark()
    print(format_table(results))
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    with RESULTS_PATH.open("w", encoding="utf-8", newline="\n") as file:
        file.write(to_json(results))
    print(f"Wrote {RESULTS_PATH.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
