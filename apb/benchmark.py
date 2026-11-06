"""Score agents on the benchmark scenarios: python -m apb.benchmark

Deterministic and timestamp-free, so reruns give identical output. See
docs/METHOD.md for the method.
"""

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict

from apb.agents import AGENTS, Agent, does_recheck
from apb.decide import decide
from apb.eligibility import exclusion_reason
from apb.models import PaymentMandate, RouteQuote

METHOD_VERSION = "apb-0.1"
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
    expected_route: str
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
    compliant = quote is not None and reason is None
    dvs = benchmark.scores.get(chosen) if compliant else None

    return {
        "scenario": scenario.name,
        "chosen_route": chosen,
        "expected_route": scenario.expected_route,
        "best_eligible_route": benchmark.selected_route,
        "mandate_compliant": compliant,
        "exclusion_reason": reason.value if reason else None,
        "dvs": dvs,
        "score": dvs if dvs is not None else 0.0,
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
        f"{'Test':<18}{'Agent':<15}{'Chosen route':<22}"
        f"{'Compliant':<11}{'DVS':>6}{'Score':>7}  Best eligible"
    )
    lines = [header, "-" * len(header)]
    for index, scenario in enumerate(results["scenarios"]):
        for agent in results["agents"]:
            row = agent["scenarios"][index]
            dvs = "-" if row["dvs"] is None else f"{row['dvs']:.1f}"
            compliant = "yes" if row["mandate_compliant"] else "NO"
            lines.append(
                f"{scenario['name']:<18}{agent['agent']:<15}"
                f"{str(row['chosen_route']):<22}{compliant:<11}{dvs:>6}"
                f"{row['score']:>7.1f}  {row['best_eligible_route']}"
            )
    lines.append("-" * len(header))
    for agent in results["agents"]:
        lines.append(
            f"{agent['agent']:<15} APB score {agent['apb_score']:>5.1f}   "
            f"best route chosen in {agent['tests_passed']} of {agent['tests_total']}"
        )
    lines.append(
        f"Method {results['method_version']}, data mode {results['data_mode']}."
    )
    return "\n".join(lines)


def main() -> None:
    results = run_benchmark()
    print(format_table(results))
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    with RESULTS_PATH.open("w", encoding="utf-8", newline="\n") as file:
        file.write(to_json(results))
    print(f"Wrote {RESULTS_PATH.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
