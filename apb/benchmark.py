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
from typing import Any, NamedTuple

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


class Corridor(NamedTuple):
    code: str
    label: str
    currency: str
    country: str


# The method works for any corridor. These are the synthetic examples.
CORRIDORS = (
    Corridor("us_mx", "US→Mexico", "MXN", "MX"),
    Corridor("us_in", "US→India", "INR", "IN"),
    Corridor("us_ke", "US→Kenya", "KES", "KE"),
)
DEFAULT_CORRIDOR = "us_ke"
CURRENCIES = {corridor.country: corridor.currency for corridor in CORRIDORS}
_CENT = Decimal("0.01")


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
    corridor: str = DEFAULT_CORRIDOR


def load_scenarios(corridor: str = DEFAULT_CORRIDOR) -> list[Scenario]:
    return [
        Scenario.model_validate({**json.loads(path.read_text("utf-8")), "corridor": corridor})
        for path in sorted((SCENARIOS_DIR / corridor).glob("*.json"))
    ]


def _quote_for(
    quotes: list[RouteQuote], provider: str | None
) -> RouteQuote | None:
    return next((quote for quote in quotes if quote.provider == provider), None)


def _recipient_loses(
    market: list[RouteQuote], best: str | None, chosen: RouteQuote
) -> tuple[int | float, float]:
    """Return what the recipient gives up against the best eligible route.

    The first value is in the local currency. The second is its USD equivalent
    at the chosen quote's rate, to 2 decimals.
    """
    loss = _quote_for(market, best).recipient_amount - chosen.recipient_amount
    local = int(loss) if loss == loss.to_integral_value() else float(loss)
    usd = float((loss / chosen.fx_rate).quantize(_CENT, rounding=ROUND_HALF_UP))
    return local, usd


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
    loses = loses_usd = None
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
            loses, loses_usd = _recipient_loses(market, benchmark.selected_route, quote)

    return {
        "scenario": scenario.name,
        "chosen_route": chosen,
        "expected_route": scenario.expected_route,
        "best_eligible_route": benchmark.selected_route,
        "mandate_compliant": compliant,
        "exclusion_reason": problem,
        "dvs": dvs,
        "score": score,
        "currency": CURRENCIES.get(mandate.receive_country, mandate.receive_country),
        "recipient_loses_local": loses,
        "recipient_loses_usd": loses_usd,
        "passed": chosen == scenario.expected_route,
    }


def _mean(scores: list[float]) -> float:
    total = sum((Decimal(str(score)) for score in scores), Decimal(0))
    mean = total / len(scores) if scores else Decimal(0)
    return float(mean.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _usd_total(rows: list[dict[str, Any]]) -> float:
    total = sum((Decimal(str(row["recipient_loses_usd"] or 0)) for row in rows), Decimal(0))
    return float(total.quantize(_CENT, rounding=ROUND_HALF_UP))


def _run_corridor(corridor: Corridor, agents: Mapping[str, Agent]) -> dict[str, Any]:
    scenarios = load_scenarios(corridor.code)
    results = []
    for name, agent in agents.items():
        scored = [evaluate(agent, scenario) for scenario in scenarios]
        results.append(
            {
                "agent": name,
                "apb_score": _mean([row["score"] for row in scored]),
                "tests_passed": sum(row["passed"] for row in scored),
                "tests_total": len(scored),
                "total_usd_lost": _usd_total(scored),
                "total_local_lost": sum(row["recipient_loses_local"] or 0 for row in scored),
                "scenarios": scored,
            }
        )
    return {
        "corridor": corridor.code,
        "label": corridor.label,
        "currency": corridor.currency,
        "agents": results,
    }


def run_benchmark(agents: Mapping[str, Agent] = AGENTS) -> dict[str, Any]:
    """Run every agent on every scenario in every corridor.

    An agent's headline APB score is the mean of its corridor scores.
    """
    corridors = [_run_corridor(corridor, agents) for corridor in CORRIDORS]
    headline = []
    for name in agents:
        rows = [
            next(agent for agent in block["agents"] if agent["agent"] == name)
            for block in corridors
        ]
        headline.append(
            {
                "agent": name,
                "apb_score": _mean([row["apb_score"] for row in rows]),
                "by_corridor": {
                    block["corridor"]: row["apb_score"] for block, row in zip(corridors, rows)
                },
                "tests_passed": sum(row["tests_passed"] for row in rows),
                "tests_total": sum(row["tests_total"] for row in rows),
                "total_usd_lost": float(
                    sum((Decimal(str(row["total_usd_lost"])) for row in rows), Decimal(0))
                ),
            }
        )
    return {
        "method_version": METHOD_VERSION,
        "data_mode": DATA_MODE,
        "corridors": corridors,
        "scenarios": [
            {"name": scenario.name, "description": scenario.description}
            for scenario in load_scenarios()
        ],
        "agents": headline,
    }


def format_loss(row: dict[str, Any]) -> str:
    """Show a shortfall in USD first: "$2.76 (KES 360)"."""
    if row["recipient_loses_usd"] is None:
        return "-"
    return f"${row['recipient_loses_usd']:,.2f} ({row['currency']} {row['recipient_loses_local']:,})"


def to_json(results: dict[str, Any]) -> str:
    return json.dumps(results, indent=2, sort_keys=True) + "\n"


def format_table(results: dict[str, Any]) -> str:
    header = (
        f"{'Test':<18}{'Agent':<15}{'Chosen route':<25}"
        f"{'Compliant':<11}{'DVS':>6}{'Score':>7}{'Recipient loses':>21}"
        "  Best eligible"
    )
    lines = []
    for block in results["corridors"]:
        lines += ["", f"{block['label']} ({block['currency']})", header, "-" * len(header)]
        for index, scenario in enumerate(results["scenarios"]):
            for agent in block["agents"]:
                row = agent["scenarios"][index]
                dvs = "-" if row["dvs"] is None else f"{row['dvs']:.1f}"
                compliant = "yes" if row["mandate_compliant"] else "NO"
                lines.append(
                    f"{scenario['name']:<18}{agent['agent']:<15}"
                    f"{row['chosen_route'] or '(no payment)':<25}{compliant:<11}{dvs:>6}"
                    f"{row['score']:>7.1f}{format_loss(row):>21}"
                    f"  {row['best_eligible_route'] or '(stop)'}"
                )
        for agent in block["agents"]:
            lines.append(
                f"  {agent['agent']}: {block['label']} score {agent['apb_score']:.1f}, "
                f"{agent['tests_passed']} of {agent['tests_total']} passed, "
                f"${agent['total_usd_lost']:,.2f} ({block['currency']} "
                f"{agent['total_local_lost']:,}) lost"
            )
    labels = {block["corridor"]: block["label"] for block in results["corridors"]}
    lines += ["", "Headline (mean across corridors)", "-" * len(header)]
    for agent in results["agents"]:
        parts = ", ".join(
            f"{labels[code]} {score:.1f}" for code, score in agent["by_corridor"].items()
        )
        lines.append(
            f"{agent['agent']}: APB score {agent['apb_score']:.1f} ({parts}), "
            f"{agent['tests_passed']} of {agent['tests_total']} tests passed, "
            f"${agent['total_usd_lost']:,.2f} lost across the value tests"
        )
    lines.append(
        f"Method {results['method_version']}, data mode {results['data_mode']}."
    )
    return "\n".join(lines).lstrip("\n")


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
