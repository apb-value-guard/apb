"""Write the README results section from results/benchmark.json.

Rerun after the benchmark changes: python -m scripts.render_readme_results
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results" / "benchmark.json"
README = ROOT / "README.md"

START = "<!-- results:start -->"
END = "<!-- results:end -->"

VALUE_GROUP = "Value"
COMPLIANCE_GROUP = "Compliance behaviour, simulated"

TESTS = [
    ("brand_bias", "Brand bias", VALUE_GROUP),
    ("fee_trap", "Fee trap", VALUE_GROUP),
    ("sponsored_route", "Sponsored route", VALUE_GROUP),
    ("mandate_conflict", "Mandate conflict", VALUE_GROUP),
    ("quote_drift", "Quote drift", VALUE_GROUP),
    ("screening_hold", "Screening hold", COMPLIANCE_GROUP),
    ("kyc_gap", "KYC gap", COMPLIANCE_GROUP),
    ("payee_close_match", "Payee close match", COMPLIANCE_GROUP),
    ("payee_no_match", "Payee no match", COMPLIANCE_GROUP),
]


def _rows_by_test(agent: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["scenario"]: row for row in agent["scenarios"]}


def _cell(row: dict[str, Any]) -> str:
    return f"{'✅' if row['passed'] else '❌'} {row['score']:.1f}"


def _loss(row: dict[str, Any]) -> str:
    loses = row["recipient_loses_kes"]
    return "-" if loses is None else f"KES {loses:,}"


def render(results: dict[str, Any]) -> str:
    agents = {agent["agent"]: agent for agent in results["agents"]}
    naive = _rows_by_test(agents["naive_agent"])
    guarded = _rows_by_test(agents["guarded_agent"])

    lines = [
        "| Test | Group | naive_agent | guarded_agent | Recipient loses (naive) |",
        "|---|---|---|---|---|",
    ]
    for name, label, group in TESTS:
        lines.append(
            f"| {label} | {group} | {_cell(naive[name])} | {_cell(guarded[name])} "
            f"| {_loss(naive[name])} |"
        )
    lines.append("")
    lines.append(
        "✅ means the agent chose the expected route or behaviour, and ❌ means it "
        "didn't. The number is the test score."
    )
    lines.append("")
    for agent in results["agents"]:
        lines.append(
            f"- **{agent['agent']}:** APB score {agent['apb_score']:.1f}, "
            f"{agent['tests_passed']}/{agent['tests_total']} tests passed, "
            f"KES {agent['total_kes_lost']:,} lost across the value tests"
        )
    return "\n".join(lines)


def updated_readme(readme: str, results: dict[str, Any]) -> str:
    """Return the README with its results section replaced."""
    before, rest = readme.split(START, 1)
    _, after = rest.split(END, 1)
    return f"{before}{START}\n{render(results)}\n{END}{after}"


def main() -> None:
    results = json.loads(RESULTS.read_text("utf-8"))
    text = updated_readme(README.read_text("utf-8"), results)
    with README.open("w", encoding="utf-8", newline="\n") as file:
        file.write(text)
    print("Updated README.md")


if __name__ == "__main__":
    main()
