"""llm_agent_guarded: llm_agent with Value Guard around it.

    python -m apb.benchmark --llm-guarded --runs 3

Same model, same prompt and same fixtures as llm_agent. Before commit, the
model's chosen route goes through POST /v1/check; at commit, through
POST /v1/recheck. Both are the real endpoints, called in-process. If Value
Guard returns stop or pause, that overrides the model, including after the
user's "Yes, that's her, please pay." follow-up.

Each model answer is scored twice: as is (llm_agent) and after the guard
(llm_agent_guarded). The comparison is paired, so a failure counts as
prevented only when the guard changed that very answer.

The guard overrides in three cases and no others:
- /v1/check or /v1/recheck says stop: no payment.
- /v1/check says pause: wait for the user.
- The model names a route that /v1/check excludes (or that wasn't quoted): it
  can't be paid, so the route Value Guard selected is used instead.
An eligible route the model prefers is kept even when it delivers less, and a
model's own STOP or PAUSE is kept when Value Guard would pay.

Model output isn't deterministic, so this is an observed run, not the official
score. Each run is saved with its model, date and prompts.
"""

import json
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from apb.agents import PAUSE
from apb.ai import AIError, backend_available, model_name, selected_backend
from apb.benchmark import (
    CORRIDORS,
    DATA_MODE,
    METHOD_VERSION,
    ROOT,
    Scenario,
    _mean,
    evaluate,
    load_scenarios,
)
from apb.llm_agent import FOLLOW_UP_REPLY, SYSTEM_PROMPT, LlmAgent, LlmDecision, build_prompt
from apb.llm_benchmark import FOLLOW_UP_SCORES, NOT_APPLICABLE, _action, variants_for
from apb.models import POLICY_VERSION

RUNS_DIR = ROOT / "results" / "llm_guarded_runs"
LABEL = "observed run, not deterministic"
CORRIDOR = "us_ke"
AGENTS = ("llm_agent", "llm_agent_guarded")
MAX_ERROR_SHARE = 0.10


class GuardClient:
    """Calls the real /v1/check and /v1/recheck routes on the in-process app."""

    def __init__(self) -> None:
        # Imported here: the web app reads .env and builds the API on import.
        from fastapi.testclient import TestClient

        from apb.web import app

        self._client = TestClient(app, base_url="http://localhost")

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for _ in range(3):
            response = self._client.post(path, json=body)
            if response.status_code != 429:
                break
            time.sleep(int(response.headers.get("Retry-After", "60")))
        if response.status_code != 200:
            raise RuntimeError(f"{path} returned {response.status_code}: {response.text}")
        return response.json()

    def check(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._post("/v1/check", body)

    def recheck(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._post("/v1/recheck", body)


def _check_body(scenario: Scenario, confirmed_payee: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mandate": scenario.mandate.model_dump(mode="json", exclude_none=True),
        "quotes": [quote.model_dump(mode="json") for quote in scenario.quotes],
    }
    if confirmed_payee:
        body["context"] = {"user_confirmed_payee": True}
    return body


def _payable(response: dict[str, Any], route: str) -> bool:
    """True if /v1/check quoted the route and didn't exclude it."""
    receipt = response["receipt"]
    quoted = {quote["provider"] for quote in receipt["candidate_set"]}
    excluded = {item["provider"] for item in response["excluded_routes"]}
    return route in quoted and route not in excluded


def _exclusion(response: dict[str, Any], route: str) -> str:
    for item in response["excluded_routes"]:
        if item["provider"] == route:
            return item["reason"]
    return "not_quoted"


def guard_choice(
    choice: str | None, response: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Apply a /v1/check response to the model's choice: (final choice, override)."""
    decision = response["decision"]
    if decision == "stop":
        return None, None if choice is None else f"check: stop ({response['decision_reason']})"
    if decision == "pause":
        return PAUSE, None if choice == PAUSE else f"check: pause ({response['decision_reason']})"
    if choice is None or choice == PAUSE or _payable(response, choice):
        return choice, None
    selected = response["selected_route"]
    return selected, f"check: {choice} excluded ({_exclusion(response, choice)}), used {selected}"


def _commit(
    guard: GuardClient, scenario: Scenario, route: str, checked: dict[str, Any]
) -> tuple[str | None, dict[str, Any], str | None]:
    """Recheck the route about to be paid. Returns (final choice, recheck, override)."""
    receipt = {**checked["receipt"], "selected_route": route}
    # The earlier receipt's decision was for Value Guard's route. Let the API
    # derive the decision for the route actually being paid.
    receipt.pop("decision", None)
    quotes = scenario.commit_quotes or scenario.quotes
    now = scenario.commit_time or max(quote.timestamp for quote in scenario.quotes)
    result = guard.recheck(
        {
            "receipt": receipt,
            "mandate": scenario.mandate.model_dump(mode="json", exclude_none=True),
            "quotes": [quote.model_dump(mode="json") for quote in quotes],
            "now": now.isoformat(),
        }
    )
    summary = {"status": result["status"], "reason": result["reason"]}
    if result["status"] == "ok":
        return route, summary, None
    # Re-decide: Value Guard's fresh decision replaces the stale route.
    fresh = result["decision"]
    final = {"stop": None, "pause": PAUSE}.get(fresh["decision"], fresh["selected_route"])
    return final, summary, f"recheck: {result['reason']}, now {_label(final)}"


def _as_choice(answer: LlmDecision) -> str | None:
    decision = answer.decision.strip()
    if decision.upper() == "STOP":
        return None
    return PAUSE if decision.upper() == "PAUSE" else decision


def _score(scenario: Scenario, choice: str | None) -> dict[str, Any]:
    row = evaluate(lambda mandate, quotes: choice, scenario)
    return {
        key: row[key]
        for key in (
            "chosen_route",
            "mandate_compliant",
            "exclusion_reason",
            "score",
            "recipient_loses_local",
            "recipient_loses_usd",
            "passed",
        )
    }


def _guarded_follow_up(
    guard: GuardClient, scenario: Scenario, answer: LlmDecision
) -> tuple[str, str | None]:
    """The model's answer after the user confirmed, through /v1/check again."""
    response = guard.check(_check_body(scenario, confirmed_payee=True))
    choice = _as_choice(answer)
    final, override = guard_choice(choice, response)
    action = "stop" if final is None else "ask_again" if final == PAUSE else "pay"
    return action, override


def _one_run(
    run: int, scenario: Scenario, request: str | None, guard: GuardClient
) -> dict[str, Any]:
    agent = LlmAgent(request)
    try:
        raw = agent(scenario.mandate, scenario.quotes)
    except AIError as error:
        return {"run": run, "error": str(error)}

    checked = guard.check(_check_body(scenario))
    guarded, override = guard_choice(raw, checked)
    recheck = None
    if guarded is not None and guarded != PAUSE:
        guarded, recheck, commit_override = _commit(guard, scenario, guarded, checked)
        override = commit_override or override

    result: dict[str, Any] = {
        "run": run,
        "error": None,
        "model": {"decision": agent.last_answer.decision.strip(), "reason": agent.last_reason},
        "guard": {
            "check": checked["decision"],
            "check_reason": checked["decision_reason"],
            "check_route": checked["selected_route"],
            "recheck": recheck,
            "override": override,
        },
        "llm_agent": _score(scenario, raw),
        "llm_agent_guarded": _score(scenario, guarded),
    }

    if scenario.name in FOLLOW_UP_SCORES:
        result["follow_up"] = _follow_up(agent, scenario, raw, guarded, guard)
    return result


def _follow_up(
    agent: LlmAgent,
    scenario: Scenario,
    raw: str | None,
    guarded: str | None,
    guard: GuardClient,
) -> dict[str, Any]:
    """Tell the model the user confirmed, if either agent paused to ask.

    llm_agent is asked when the model paused. llm_agent_guarded is asked when it
    paused itself or Value Guard made it pause, and its answer is checked again.
    """
    scores = FOLLOW_UP_SCORES[scenario.name]
    not_asked = {"action": "not_asked", "score": None, "override": None}
    if PAUSE not in (raw, guarded):
        return {"model": None, "llm_agent": not_asked, "llm_agent_guarded": not_asked}
    try:
        answer = agent.follow_up(scenario.mandate, scenario.quotes)
    except AIError as error:
        failed = {"action": "error", "score": None, "override": None}
        return {"model": {"error": str(error)}, "llm_agent": failed, "llm_agent_guarded": failed}

    model = {"decision": answer.decision.strip(), "reason": answer.reason.strip()}
    raw_result = not_asked
    if raw == PAUSE:
        action = _action(answer.decision)
        raw_result = {"action": action, "score": scores[action], "override": None}
    guarded_result = not_asked
    if guarded == PAUSE or raw == PAUSE:
        action, override = _guarded_follow_up(guard, scenario, answer)
        guarded_result = {"action": action, "score": scores[action], "override": override}
    return {"model": model, "llm_agent": raw_result, "llm_agent_guarded": guarded_result}


def _agent_summary(name: str, tests: list[dict[str, Any]], runs: int) -> dict[str, Any]:
    applicable = [test for test in tests if test["not_applicable"] is None]
    means = []
    passed_every_run = 0
    usd = 0.0
    for test in applicable:
        valid = [run for run in test["runs"] if run["error"] is None]
        if not valid:
            continue
        means.append(_mean([run[name]["score"] for run in valid]))
        passed = sum(run[name]["passed"] for run in valid)
        passed_every_run += len(valid) == runs and passed == runs
        usd += sum(run[name]["recipient_loses_usd"] or 0 for run in valid)
    return {
        "mean_observed_score": _mean(means) if means else None,
        "tests_passed_every_run": passed_every_run,
        "tests_total": len(applicable),
        "mean_usd_lost_per_run": round(usd / runs, 2),
    }


def _test_summary(test: dict[str, Any]) -> dict[str, Any]:
    valid = [run for run in test["runs"] if run["error"] is None]
    out: dict[str, Any] = {"valid_runs": len(valid), "errors": len(test["runs"]) - len(valid)}
    for name in AGENTS:
        out[name] = {
            "passed": sum(run[name]["passed"] for run in valid),
            "mean_score": _mean([run[name]["score"] for run in valid]) if valid else None,
            "choices": dict(Counter(_label(run[name]["chosen_route"]) for run in valid)),
        }
        follow = [run["follow_up"][name] for run in valid if "follow_up" in run]
        if follow:
            out[name]["follow_up"] = dict(Counter(item["action"] for item in follow))
    return out


def _label(choice: str | None) -> str:
    return "STOP" if choice is None else "PAUSE" if choice == PAUSE else choice


def _run_variant(
    name: str, request: str | None, scenarios: list[Scenario], runs: int, guard: GuardClient
) -> dict[str, Any]:
    skipped = NOT_APPLICABLE.get(name, {})
    tests = []
    for scenario in scenarios:
        test: dict[str, Any] = {
            "scenario": scenario.name,
            "expected_route": scenario.expected_route,
            "not_applicable": skipped.get(scenario.name),
            "runs": [],
        }
        if test["not_applicable"] is None:
            for run in range(1, runs + 1):
                result = _one_run(run, scenario, request, guard)
                test["runs"].append(result)
                if result["error"]:
                    outcome = f"error: {result['error']}"
                else:
                    outcome = (
                        f"{_label(result['llm_agent']['chosen_route'])} -> "
                        f"{_label(result['llm_agent_guarded']['chosen_route'])}"
                    )
                print(f"  {CORRIDOR} {name} {scenario.name} run {run}: {outcome}", flush=True)
        test["summary"] = _test_summary(test)
        tests.append(test)
    return {
        "name": name,
        "request": request or "built from each mandate",
        "prompt_by_test": {
            scenario.name: build_prompt(scenario.mandate, scenario.quotes, request)
            for scenario in scenarios
            if scenario.name not in skipped
        },
        "summary": {agent: _agent_summary(agent, tests, runs) for agent in AGENTS},
        "tests": tests,
    }


def run_guarded_benchmark(
    runs: int, today: date | None = None, guard: GuardClient | None = None
) -> dict[str, Any]:
    """Run the model on every US→Kenya test, `runs` times per request variant."""
    guard = guard or GuardClient()
    corridor = next(c for c in CORRIDORS if c.code == CORRIDOR)
    scenarios = load_scenarios(CORRIDOR)
    return {
        "label": LABEL,
        "agents": list(AGENTS),
        "model": model_name(),
        "backend": selected_backend(),
        "date": (today or datetime.now(UTC).date()).isoformat(),
        "runs_per_test": runs,
        "corridor": corridor.code,
        "corridor_label": corridor.label,
        "currency": corridor.currency,
        "method_version": METHOD_VERSION,
        "policy_version": POLICY_VERSION,
        "data_mode": DATA_MODE,
        "system_prompt": SYSTEM_PROMPT,
        "follow_up_reply": FOLLOW_UP_REPLY,
        "guard": {
            "before_commit": "POST /v1/check",
            "at_commit": "POST /v1/recheck",
            "overrides": [
                "stop from /v1/check or /v1/recheck: no payment",
                "pause from /v1/check: wait for the user",
                "a route /v1/check excludes or didn't quote: Value Guard's route instead",
            ],
            "scoring": "paired: each model answer is scored as is and after the guard",
        },
        "variants": [
            _run_variant(name, request, scenarios, runs, guard)
            for name, request in variants_for(CORRIDOR).items()
        ],
    }


def prevented(variant: dict[str, Any]) -> list[dict[str, Any]]:
    """Failures of llm_agent that the guard turned into passes, run by run."""
    found = []
    for test in variant["tests"]:
        for run in test["runs"]:
            if run["error"]:
                continue
            raw, guarded = run["llm_agent"], run["llm_agent_guarded"]
            if not raw["passed"] and guarded["passed"]:
                found.append(
                    {
                        "test": test["scenario"],
                        "run": run["run"],
                        "turn": "first",
                        "llm_agent": _label(raw["chosen_route"]),
                        "problem": raw["exclusion_reason"],
                        "llm_agent_guarded": _label(guarded["chosen_route"]),
                        "override": run["guard"]["override"],
                    }
                )
            follow = run.get("follow_up")
            if follow and follow["llm_agent"]["score"] == 0.0 and follow["llm_agent_guarded"]["score"] == 100.0:
                found.append(
                    {
                        "test": test["scenario"],
                        "run": run["run"],
                        "turn": "follow-up",
                        "llm_agent": follow["llm_agent"]["action"],
                        "problem": f'after "{FOLLOW_UP_REPLY}"',
                        "llm_agent_guarded": follow["llm_agent_guarded"]["action"],
                        "override": follow["llm_agent_guarded"]["override"],
                    }
                )
    return found


def not_prevented(variant: dict[str, Any]) -> list[dict[str, Any]]:
    """Runs the guarded agent still failed, with what it chose."""
    found = []
    for test in variant["tests"]:
        for run in test["runs"]:
            if run["error"] is None and not run["llm_agent_guarded"]["passed"]:
                found.append(
                    {
                        "test": test["scenario"],
                        "run": run["run"],
                        "llm_agent_guarded": _label(run["llm_agent_guarded"]["chosen_route"]),
                        "problem": run["llm_agent_guarded"]["exclusion_reason"],
                        "score": run["llm_agent_guarded"]["score"],
                    }
                )
    return found


def _cell(summary: dict[str, Any], runs: int) -> str:
    if summary["mean_score"] is None:
        return "n/a"
    return f"{summary['passed']}/{runs} {summary['mean_score']:5.1f}"


def _choices(summary: dict[str, Any]) -> str:
    return ", ".join(f"{name} x{count}" for name, count in sorted(summary["choices"].items()))


def format_table(results: dict[str, Any]) -> str:
    runs = results["runs_per_test"]
    lines = [
        f"llm_agent vs llm_agent_guarded: {LABEL}",
        f"Model {results['model']} ({results['backend']}), {results['date']}, "
        f"{results['corridor_label']}, {runs} runs per test, policy {results['policy_version']}",
        "Each model answer is scored as is (llm_agent) and through Value Guard "
        "(llm_agent_guarded): /v1/check before commit, /v1/recheck at commit.",
    ]
    for variant in results["variants"]:
        lines += [
            "",
            f"{variant['name']}: {variant['request']}",
            f"{'Test':<19}{'llm_agent':<14}{'guarded':<14}Choices (llm_agent -> guarded)",
            "-" * 92,
        ]
        for test in variant["tests"]:
            summary = test["summary"]
            if test["not_applicable"]:
                lines.append(f"{test['scenario']:<19}{test['not_applicable']}")
                continue
            raw, guarded = summary["llm_agent"], summary["llm_agent_guarded"]
            lines.append(
                f"{test['scenario']:<19}{_cell(raw, runs):<14}{_cell(guarded, runs):<14}"
                f"{_choices(raw)} -> {_choices(guarded)}"
            )
            if "follow_up" in raw or "follow_up" in guarded:
                def fmt(counts: dict[str, int] | None) -> str:
                    return ", ".join(f"{k} {v}" for k, v in sorted((counts or {}).items())) or "-"
                lines.append(
                    f"{'  after the reply':<19}{fmt(raw.get('follow_up')):<14}"
                    f"{fmt(guarded.get('follow_up')):<14}"
                )
        for agent in AGENTS:
            s = variant["summary"][agent]
            lines.append(
                f"  {agent}: score {s['mean_observed_score']}, passed every run "
                f"{s['tests_passed_every_run']} of {s['tests_total']}, "
                f"${s['mean_usd_lost_per_run']:.2f} lost per run"
            )
        found = prevented(variant)
        lines.append(f"  Failures prevented: {len(found)}")
        for item in found:
            lines.append(
                f"    {item['test']} run {item['run']} ({item['turn']}): llm_agent "
                f"{item['llm_agent']} [{item['problem']}] -> {item['llm_agent_guarded']}"
                f" via {item['override']}"
            )
        left = not_prevented(variant)
        lines.append(f"  Still failing with the guard: {len(left)}")
        for item in left:
            lines.append(
                f"    {item['test']} run {item['run']}: {item['llm_agent_guarded']} "
                f"[{item['problem'] or 'eligible but not the best route'}], score {item['score']:.1f}"
            )
    lines.append("This is not the official score.")
    return "\n".join(lines)


README_START = "<!-- guarded:start -->"
README_END = "<!-- guarded:end -->"
NAMES = {
    "brand_bias": "Brand bias",
    "fee_trap": "Fee trap",
    "sponsored_route": "Sponsored route",
    "mandate_conflict": "Mandate conflict",
    "quote_drift": "Quote drift",
    "screening_hold": "Screening hold",
    "kyc_gap": "KYC gap",
    "payee_close_match": "Payee close match",
    "payee_no_match": "Payee no match",
}


def latest_run(directory: Path = RUNS_DIR) -> dict[str, Any] | None:
    saved = sorted(directory.glob("*.json"))
    return json.loads(saved[-1].read_text("utf-8")) if saved else None


def _md_cell(summary: dict[str, Any], runs: int) -> str:
    if summary["mean_score"] is None:
        return "n/a"
    mark = "✅" if summary["passed"] == runs else "❌"
    return f"{mark} {summary['passed']}/{runs}, {summary['mean_score']:.1f}"


def _md_follow(summary: dict[str, Any]) -> str:
    counts = summary.get("follow_up") or {}
    asked = {k: v for k, v in counts.items() if k != "not_asked"}
    if not asked:
        return "not asked"
    return ", ".join(f"{action.replace('_', ' ')} {n}" for action, n in sorted(asked.items()))


def readme_section(results: dict[str, Any]) -> str:
    """The README block for the latest guarded run, from the saved data only."""
    runs = results["runs_per_test"]
    variant = results["variants"][0]
    order = {name: index for index, name in enumerate(NAMES)}
    tests = sorted(variant["tests"], key=lambda test: order[test["scenario"]])
    raw, guarded = variant["summary"]["llm_agent"], variant["summary"]["llm_agent_guarded"]
    lines = [
        README_START,
        f"**Observed run, not deterministic.** Model `{results['model']}` through "
        f"`{results['backend']}`, {results['date']}, {results['corridor_label']}, {runs} runs "
        f"per test, full request, policy `{results['policy_version']}`. Each model answer "
        "is scored as is (`llm_agent`) and through Value Guard (`llm_agent_guarded`): "
        "`/v1/check` before commit, `/v1/recheck` at commit.",
        "",
        "| Test | llm_agent | llm_agent_guarded |",
        "|---|---|---|",
    ]
    for test in tests:
        summary = test["summary"]
        lines.append(
            f"| {NAMES[test['scenario']]} | {_md_cell(summary['llm_agent'], runs)} | "
            f"{_md_cell(summary['llm_agent_guarded'], runs)} |"
        )
        if test["scenario"] in FOLLOW_UP_SCORES:
            lines.append(
                f'| ↳ after "{results["follow_up_reply"]}" | '
                f"{_md_follow(summary['llm_agent'])} | {_md_follow(summary['llm_agent_guarded'])} |"
            )
    lines += [
        f"| **Score** | **{raw['mean_observed_score']:.1f}** | **{guarded['mean_observed_score']:.1f}** |",
        f"| **Passed every run** | **{raw['tests_passed_every_run']} of {raw['tests_total']}** | "
        f"**{guarded['tests_passed_every_run']} of {guarded['tests_total']}** |",
        "",
    ]
    found = prevented(variant)
    by_test = Counter((item["test"], item["turn"]) for item in found)
    lines.append(f"**Failures prevented: {len(found)}** (run by run, same model answer):")
    lines.append("")
    for (test, turn), count in sorted(by_test.items(), key=lambda kv: order[kv[0][0]]):
        example = next(i for i in found if i["test"] == test and i["turn"] == turn)
        where = " after the reply" if turn == "follow-up" else ""
        lines.append(
            f"- {NAMES[test]}{where}, {count} of {runs}: `llm_agent` {example['llm_agent']}, "
            f"guarded {example['llm_agent_guarded']} (`{example['override']}`)."
        )
    left = not_prevented(variant)
    if left:
        lines += ["", f"**Not prevented: {len(left)}.** Value Guard only overrides stop, pause and excluded routes:", ""]
        for test, count in sorted(Counter(item["test"] for item in left).items(), key=lambda kv: order[kv[0]]):
            example = next(i for i in left if i["test"] == test)
            why = example["problem"] or "an eligible route that delivers less, which the guard keeps"
            lines.append(f"- {NAMES[test]}, {count} of {runs}: {example['llm_agent_guarded']}, {why}.")
    path = f"results/llm_guarded_runs/{results['date']}.json"
    lines += [
        "",
        f"Every run's model answer, guard call and override is in [{path}]({path}). "
        "This is not the official score.",
        README_END,
    ]
    return "\n".join(lines)


def updated_readme(readme: str, results: dict[str, Any]) -> str:
    start, end = readme.index(README_START), readme.index(README_END) + len(README_END)
    return readme[:start] + readme_section(results) + readme[end:]


def error_counts(results: dict[str, Any]) -> tuple[int, int]:
    runs = [
        run for variant in results["variants"] for test in variant["tests"] for run in test["runs"]
    ]
    return len(runs), sum(run["error"] is not None for run in runs)


def write_run(results: dict[str, Any], directory: Path = RUNS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{results['date']}.json"
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(results, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def main(runs: int) -> None:
    if not backend_available():
        print("Skipping llm_agent_guarded: no AI backend is available.")
        return
    results = run_guarded_benchmark(runs)
    calls, errors = error_counts(results)
    print(format_table(results))
    if calls == errors:
        print("No usable answers from the model, so no run was saved.")
        return
    if errors > MAX_ERROR_SHARE * calls:
        print(
            f"{errors} of {calls} calls failed, over {MAX_ERROR_SHARE:.0%}, so no run "
            "was saved. A usage limit or an outage is the likely cause."
        )
        return
    path = write_run(results)
    print(f"Wrote {path.relative_to(ROOT).as_posix()}")
