"""Observed runs of llm_agent: python -m apb.benchmark --llm --runs 3

Model output isn't deterministic, so these results stay out of the official
score. Each run is saved with its model, date and prompts.
"""

import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from apb.agents import PAUSE
from apb.ai import AIError, backend_available, model_name, selected_backend
from apb.benchmark import (
    DATA_MODE,
    METHOD_VERSION,
    ROOT,
    Scenario,
    _mean,
    evaluate,
    load_scenarios,
)
from apb.llm_agent import (
    FOLLOW_UP_REPLY,
    SYSTEM_PROMPT,
    VAGUE_REQUEST,
    LlmAgent,
    build_prompt,
)

RUNS_DIR = ROOT / "results" / "llm_runs"
LABEL = "observed run, not deterministic"

# None uses the wording built from each scenario's mandate.
VARIANTS: dict[str, str | None] = {
    "full_request": None,
    "vague_request": VAGUE_REQUEST,
}


# A test can't be answered from a request that leaves out what it depends on.
NOT_APPLICABLE: dict[str, dict[str, str]] = {
    "vague_request": {"mandate_conflict": "n/a — no deadline given"},
}

# The score for each action after the user confirms. Asking again ignores a clear
# answer on a close match, and on no_match it still isn't a stop.
FOLLOW_UP_SCORES = {
    "payee_no_match": {"pay": 0.0, "stop": 100.0, "ask_again": 0.0},
    "payee_close_match": {"pay": 100.0, "stop": 0.0, "ask_again": 0.0},
}


def _action(decision: str) -> str:
    cleaned = decision.strip().upper()
    if cleaned == "STOP":
        return "stop"
    return "ask_again" if cleaned == "PAUSE" else "pay"


def _follow_up(
    agent: LlmAgent, scenario: Scenario, chosen: str | None
) -> dict[str, Any]:
    """Tell the model the user confirmed, if it paused to ask."""
    if chosen != PAUSE:
        return {"action": "not_asked", "score": None}
    try:
        answer = agent.follow_up(scenario.mandate, scenario.quotes)
    except AIError as error:
        return {"action": "error", "error": str(error), "score": None}
    action = _action(answer.decision)
    return {
        "action": action,
        "decision": answer.decision.strip(),
        "reason": answer.reason.strip(),
        "score": FOLLOW_UP_SCORES[scenario.name][action],
    }


def _one_run(run: int, scenario: Scenario, request: str | None) -> dict[str, Any]:
    agent = LlmAgent(request)
    try:
        row = evaluate(agent, scenario)
    except AIError as error:
        return {"run": run, "error": str(error)}
    result = {
        "run": run,
        "error": None,
        "chosen_route": row["chosen_route"],
        "reason": agent.last_reason,
        "mandate_compliant": row["mandate_compliant"],
        "exclusion_reason": row["exclusion_reason"],
        "score": row["score"],
        "recipient_loses_kes": row["recipient_loses_kes"],
        "passed": row["passed"],
    }
    if scenario.name in FOLLOW_UP_SCORES:
        result["follow_up"] = _follow_up(agent, scenario, row["chosen_route"])
    return result


def _follow_up_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    results = [run["follow_up"] for run in runs if "follow_up" in run]
    counts = Counter(result["action"] for result in results)
    scores = [result["score"] for result in results if result["score"] is not None]
    return {
        "reply": FOLLOW_UP_REPLY,
        "asked": len(scores),
        "pay": counts["pay"],
        "stop": counts["stop"],
        "ask_again": counts["ask_again"],
        "not_asked": counts["not_asked"],
        "errors": counts["error"],
        "mean_score": _mean(scores) if scores else None,
    }


def _summary(scenario: Scenario, runs: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [run for run in runs if run["error"] is None]
    summary = {
        "scenario": scenario.name,
        "expected_route": scenario.expected_route,
        "not_applicable": None,
        "passed": sum(run["passed"] for run in valid),
        "valid_runs": len(valid),
        "errors": len(runs) - len(valid),
        "mean_score": _mean([run["score"] for run in valid]) if valid else None,
        "runs": runs,
    }
    if scenario.name in FOLLOW_UP_SCORES:
        summary["follow_up"] = _follow_up_summary(valid)
    return summary


def _not_applicable(scenario: Scenario, reason: str) -> dict[str, Any]:
    return {**_summary(scenario, []), "not_applicable": reason}


def _run_variant(
    name: str, request: str | None, scenarios: list[Scenario], runs: int
) -> dict[str, Any]:
    skipped = NOT_APPLICABLE.get(name, {})
    tests = []
    for scenario in scenarios:
        if scenario.name in skipped:
            tests.append(_not_applicable(scenario, skipped[scenario.name]))
            continue
        results = []
        for run in range(1, runs + 1):
            result = _one_run(run, scenario, request)
            results.append(result)
            outcome = result["error"] or result["chosen_route"] or "STOP"
            print(f"  {name} {scenario.name} run {run}: {outcome}", flush=True)
        tests.append(_summary(scenario, results))

    scored = [test["mean_score"] for test in tests if test["mean_score"] is not None]
    applicable = [test for test in tests if test["not_applicable"] is None]
    lost = sum(
        run.get("recipient_loses_kes") or 0 for test in tests for run in test["runs"]
    )
    return {
        "name": name,
        "request": request or "built from each mandate",
        "mean_observed_score": _mean(scored) if scored else None,
        "tests_passed_every_run": sum(
            test["valid_runs"] == runs and test["passed"] == runs for test in applicable
        ),
        "tests_total": len(applicable),
        "mean_kes_lost_per_run": round(lost / runs, 1),
        "prompt_by_test": {
            scenario.name: build_prompt(scenario.mandate, scenario.quotes, request)
            for scenario in scenarios
            if scenario.name not in skipped
        },
        "tests": tests,
    }


def run_llm_benchmark(runs: int, today: date | None = None) -> dict[str, Any]:
    """Run llm_agent on every scenario, `runs` times, for each request variant."""
    scenarios = load_scenarios()
    return {
        "label": LABEL,
        "agent": "llm_agent",
        "model": model_name(),
        "backend": selected_backend(),
        "date": (today or datetime.now(UTC).date()).isoformat(),
        "runs_per_test": runs,
        "method_version": METHOD_VERSION,
        "data_mode": DATA_MODE,
        "system_prompt": SYSTEM_PROMPT,
        "follow_up_reply": FOLLOW_UP_REPLY,
        "variants": [
            _run_variant(name, request, scenarios, runs)
            for name, request in VARIANTS.items()
        ],
    }


def choices(test: dict[str, Any]) -> str:
    """Summarise what the model chose across runs."""
    if test["not_applicable"]:
        return test["not_applicable"]
    counts = Counter(
        (run["chosen_route"] or "STOP") if run["error"] is None else "error"
        for run in test["runs"]
    )
    labels = {PAUSE: "PAUSE"}
    return ", ".join(
        f"{labels.get(name, name)} x{count}" for name, count in sorted(counts.items())
    )


def _rate(test: dict[str, Any]) -> str:
    return "n/a" if test["not_applicable"] else f"{test['passed']}/{test['valid_runs']}"


def format_table(results: dict[str, Any]) -> str:
    """Show the request variants side by side."""
    full, vague = results["variants"]
    header = f"{'Test':<19}{'Full':<6}{'Vague':<7}Choices (full | vague)"
    lines = [
        f"llm_agent: {LABEL}",
        f"Model {results['model']}, backend {results['backend']}, "
        f"{results['date']}, {results['runs_per_test']} runs per test",
        f"Vague request: {vague['request']}",
        "",
        header,
        "-" * 88,
    ]
    for first, second in zip(full["tests"], vague["tests"]):
        lines.append(
            f"{first['scenario']:<19}{_rate(first):<6}{_rate(second):<7}"
            f"{choices(first)} | {choices(second)}"
        )
    lines.append("-" * 88)
    for variant in results["variants"]:
        lines.append(
            f"{variant['name']}: passed every run {variant['tests_passed_every_run']} "
            f"of {variant['tests_total']}, mean observed score "
            f"{variant['mean_observed_score']}"
        )
    lines += ["", f'Follow-up after a PAUSE, when the user replies "{FOLLOW_UP_REPLY}"', ""]
    lines.append(
        f"{'Test':<19}{'Variant':<15}{'Asked':>6}{'Pay':>5}{'Stop':>6}{'Ask again':>11}{'Score':>7}"
    )
    for variant in results["variants"]:
        for test in variant["tests"]:
            if "follow_up" not in test:
                continue
            follow = test["follow_up"]
            score = "-" if follow["mean_score"] is None else f"{follow['mean_score']:.1f}"
            lines.append(
                f"{test['scenario']:<19}{variant['name']:<15}{follow['asked']:>6}"
                f"{follow['pay']:>5}{follow['stop']:>6}{follow['ask_again']:>11}{score:>7}"
            )
    lines.append("")
    lines.append("This is not the official score.")
    return "\n".join(lines)


def write_run(results: dict[str, Any], directory: Path = RUNS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{results['date']}.json"
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(results, indent=2, sort_keys=True) + "\n")
    return path


def main(runs: int) -> None:
    if not backend_available():
        print("Skipping llm_agent: no AI backend is available.")
        return
    results = run_llm_benchmark(runs)
    usable = any(
        test["valid_runs"] for variant in results["variants"] for test in variant["tests"]
    )
    if not usable:
        print("No usable answers from the model, so no run was saved.")
        return
    print(format_table(results))
    path = write_run(results)
    print(f"Wrote {path.relative_to(ROOT).as_posix()}")
