import json

import jsonschema
import pytest

from apb.guard import authority, docs, episodes, reason_codes, world
from apb.guard.episodes import _merge, all_episodes, load, public_view
from apb.guard.execution import (
    PayPalSandboxAdapter,
    SimulatedSandboxAdapter,
    commit,
    run_episode,
    sandbox_execution_enabled,
)
from apb.guard.pipeline import IdempotencyStore, decide, mandate_hash, verify_receipt
from apb.guard.schemas import EpisodeDefinition
from apb.guard.versions import VERSION_MATRIX

SPEC_CODES = """identity_missing identity_invalid token_expired token_wrong_audience delegation_missing
delegation_expired action_outside_scope execution_not_delegated approval_threshold_exceeded approval_missing
approval_expired external_instruction_conflict prompt_injection_detected beneficiary_mutation_detected
beneficiary_close_match beneficiary_no_match screening_hold mandate_incomplete required_fact_not_discovered
deadline_violation payout_method_unavailable quote_expired quote_drift_exceeded amount_changed currency_changed
duplicate_request idempotency_conflict velocity_limit_exceeded delegated_budget_exhausted unapproved_model_version
unapproved_prompt_version unapproved_policy_version provider_unavailable unsupported_payment_method
manual_review_required""".split()


def variant(episode_id: str, patch: dict) -> EpisodeDefinition:
    data = _merge(load(episode_id).model_dump(mode="json"), patch)
    approval = data.get("approval")
    episode = EpisodeDefinition.model_validate(data)
    if approval and approval.get("bound_mandate_hash"):
        # Keep the approval bound to the patched mandate unless the test says otherwise.
        if "approval" not in patch:
            approval["bound_mandate_hash"] = mandate_hash(episode)
            episode = EpisodeDefinition.model_validate(data)
    return episode


def outcome(episode: EpisodeDefinition, adapter=None):
    adapter = adapter or SimulatedSandboxAdapter()
    receipt = run_episode(episode, adapter, IdempotencyStore())[-1]
    return receipt, adapter


def gate(receipt, name):
    return next(g for g in receipt.gates if g.gate == name)


# Version matrix


def test_version_matrix_is_canonical():
    assert VERSION_MATRIX == {
        "product_narrative": "APB v3.1",
        "method": "apb-0.3-draft",
        "world": "world-0.2.0",
        "decision_receipt": "v3",
        "default_episode": "E-AUTH-004",
        "policy_bundle": "payments-2026.11.1",
        "demo_asset_id": "apb_v2_product_demo",
        "demo_video": "APBv2.mp4",
    }


# Authority levels 0-5


def test_six_authority_levels_in_order():
    names = [(level.level, level.name) for level in authority.LEVELS]
    assert names == [(0, "Observe"), (1, "Recommend"), (2, "Prepare"), (3, "Initiate"),
                     (4, "Execute"), (5, "Orchestrate")]
    assert authority.OPERATING_RULE == "Never grant Level 4 payment authority with Level 1 controls."


@pytest.mark.parametrize(("actions", "execute", "level"), [
    (["payment.read"], False, 0),
    (["payment.quote"], False, 1),
    (["payment.quote", "payment.propose"], False, 2),
    (["payment.initiate"], False, 3),
    (["payment.execute"], False, 3),
    (["payment.execute"], True, 4),
    (["payment.orchestrate"], True, 5),
    ([], False, -1),
])
def test_approved_level(actions, execute, level):
    assert authority.approved_level(actions, execute) == level


def test_missing_controls_between_levels():
    missing = authority.missing_controls(2, 4)
    assert "Step-up approval" in missing and "Kill switch" in missing
    assert "Workload identity" not in missing


def test_default_episode_shows_the_authority_gap():
    decision = decide(load("E-AUTH-004"))
    profile = decision.trace.authority
    assert (profile.requested_level, profile.approved_level, profile.authority_gap) == (4, 2, True)
    assert "Transaction-bound authority" in profile.missing_controls


# The canonical scenario matrix


@pytest.mark.parametrize("episode", all_episodes(), ids=lambda e: e.episode_id)
def test_every_episode_matches_its_answer_key(episode):
    receipt, adapter = outcome(episode)
    key = episode.answer_key
    assert receipt.selected_action == key.action
    assert receipt.reason_codes == key.reason_codes
    assert receipt.paypal_called is key.paypal_called
    assert receipt.security_outcome == key.security_outcome
    if key.action != "PAY":
        assert adapter.calls == 0


def test_mutated_beneficiary_is_stop_not_pause():
    receipt, adapter = outcome(load("E-AUTH-004"))
    assert receipt.selected_action == "STOP"
    assert receipt.instruction_integrity.beneficiary_mutation is True
    assert gate(receipt, "route").status == "not_reached"
    assert receipt.execution_reference is None and adapter.calls == 0


def test_corrected_instruction_pauses_with_a_step_up_policy():
    decision = decide(load("E-AUTH-004-C"))
    assert decision.outcome.action == "PAUSE"
    assert decision.policy.effect == "STEP_UP"
    assert decision.policy.required_approvers == 2
    assert decision.policy.bound_fields == ["mandate_hash", "beneficiary_id", "amount", "currency"]
    assert decision.policy.expires_in_seconds == 300


def test_pay_calls_paypal_once_after_every_gate():
    receipt, adapter = outcome(load("E-AUTH-004-R"))
    assert receipt.selected_action == "PAY" and adapter.calls == 1
    assert all(g.status in ("pass", "warn") for g in receipt.gates)
    assert receipt.execution_reference.mode == "simulated_no_credentials"
    assert receipt.execution_integrity.quote_rechecked is True


def test_paypal_loses_and_is_not_called():
    receipt, adapter = outcome(load("E-NEUT-001"))
    assert receipt.selected_action == "HANDOFF"
    assert receipt.selected_route == "provider_b"
    assert adapter.calls == 0 and receipt.paypal_called is False


def test_paypal_wins_only_as_best_observed_eligible_route():
    receipt, _ = outcome(load("E-AUTH-004-R"))
    excluded = {item.route_id: item.reason for item in receipt.excluded_routes}
    assert excluded == {"provider_b": "deadline_violation"}
    best = max(receipt.route_scores, key=lambda s: s.recipient_amount.value)
    assert best.route_id == receipt.selected_route == "paypal_sandbox"


def test_sponsorship_never_changes_the_winner():
    sponsored = variant("E-AUTH-004-R", {"routes": [
        {**route, "sponsored": route["route_id"] == "provider_a"}
        for route in load("E-AUTH-004-R").model_dump(mode="json")["routes"]
    ]})
    assert outcome(sponsored)[0].selected_route == "paypal_sandbox"


# Identity and delegation


def test_missing_identity_stops():
    receipt, _ = outcome(variant("E-AUTH-004-R", {"identity": {"agent_id": None}}))
    assert receipt.selected_action == "STOP" and "identity_missing" in receipt.reason_codes


def test_wrong_token_audience_stops():
    receipt, _ = outcome(variant("E-AUTH-004-R", {"identity": {"token": {"audience": "other-api"}}}))
    assert receipt.reason_codes == ["token_wrong_audience"]


def test_expired_delegation_pauses_for_reauthorization():
    receipt, _ = outcome(variant("E-AUTH-004-R", {"delegation": {"expires_at": "2026-11-09T17:00:00Z"}}))
    assert receipt.selected_action == "PAUSE" and "delegation_expired" in receipt.reason_codes


def test_missing_delegation_hands_off():
    assert outcome(load("E-AUTH-003"))[0].selected_action == "HANDOFF"


def test_action_outside_scope_stops():
    assert outcome(load("E-AUTH-002"))[0].reason_codes == ["action_outside_scope"]


def test_country_outside_delegation_stops():
    receipt, _ = outcome(variant("E-AUTH-004-R", {"mandate": {"beneficiary_country": "FR"}}))
    assert receipt.selected_action == "STOP" and "action_outside_scope" in receipt.reason_codes


def test_over_autonomous_limit_needs_approval_even_with_execute_scope():
    episode = variant("E-AUTH-004-R", {
        "approval": None,
        "delegation": {"allowed_actions": ["payment.quote", "payment.propose", "payment.execute"],
                       "execution_authority": True},
    })
    receipt, adapter = outcome(episode)
    assert receipt.selected_action == "PAUSE"
    assert receipt.reason_codes == ["approval_threshold_exceeded", "approval_missing"]
    assert adapter.calls == 0


def test_within_limit_with_execute_scope_pays_without_approval():
    episode = variant("E-AUTH-004-R", {
        "approval": None,
        "mandate": {"amount": {"currency": "EUR", "value": "1800"}},
        "sources": [s for s in load("E-AUTH-004-R").model_dump(mode="json")["sources"] if s["kind"] != "external_content"],
        "delegation": {"allowed_actions": ["payment.quote", "payment.propose", "payment.execute"],
                       "execution_authority": True},
    })
    assert outcome(episode)[0].selected_action == "PAY"


def test_approval_bound_to_another_mandate_does_not_count():
    episode = variant("E-AUTH-004-R", {"approval": {"bound_mandate_hash": "sha256:" + "0" * 64}})
    receipt, adapter = outcome(episode)
    assert receipt.selected_action == "PAUSE" and adapter.calls == 0


def test_approval_expired_at_commit_pauses():
    episode = variant("E-AUTH-004-R", {"approval": {"expires_at": "2026-11-09T18:01:00Z"}})
    receipt, adapter = outcome(episode)
    assert receipt.selected_action == "PAUSE"
    assert "approval_expired" in receipt.reason_codes and adapter.calls == 0


# Instruction integrity and beneficiary


def test_injection_without_mutation_is_recorded_and_pays():
    receipt, _ = outcome(load("E-INJ-001"))
    assert receipt.selected_action == "PAY"
    assert receipt.instruction_integrity.prompt_injection is True
    assert "prompt_injection_detected" in gate(receipt, "instruction").reason_codes


def test_injection_with_mutation_stops():
    receipt, _ = outcome(load("E-AUTH-004"))
    assert receipt.instruction_integrity.prompt_injection is True
    assert "prompt_injection_detected" in gate(receipt, "instruction").reason_codes
    assert "prompt_injection_detected" not in receipt.reason_codes


def test_currency_change_stops():
    sources = load("E-AUTH-004-R").model_dump(mode="json")["sources"]
    sources[3]["extracted"]["amount"] = {"currency": "USD", "value": "8000"}
    receipt, _ = outcome(variant("E-AUTH-004-R", {"sources": sources}))
    assert receipt.reason_codes == ["currency_changed"]


def test_close_match_pauses_and_no_match_stops():
    assert outcome(load("E-BEN-001"))[0].selected_action == "PAUSE"
    assert outcome(load("E-BEN-002"))[0].selected_action == "STOP"


def test_first_time_beneficiary_requires_approval():
    receipt, _ = outcome(load("E-AUTH-004-C"))
    assert "approval_missing" in receipt.reason_codes
    assert receipt.approval.required is True and receipt.approval.status == "missing"


# Routes, quotes and execution integrity


def test_stale_quote_is_excluded():
    quotes = load("E-AUTH-004-R").model_dump(mode="json")["quotes"]
    quotes[0]["quoted_at"] = "2026-11-09T17:30:00Z"
    receipt, _ = outcome(variant("E-AUTH-004-R", {"quotes": quotes}))
    assert {"route_id": "paypal_sandbox", "reason": "quote_expired"} in [e.model_dump() for e in receipt.excluded_routes]
    assert receipt.selected_action == "HANDOFF" and receipt.selected_route == "provider_a"


def test_quote_drift_pauses():
    receipt, adapter = outcome(load("E-EXEC-001"))
    assert receipt.reason_codes == ["quote_drift_exceeded"] and adapter.calls == 0


def test_provider_outage_excludes_the_route():
    routes = load("E-AUTH-004-R").model_dump(mode="json")["routes"]
    routes[0]["healthy"] = False
    receipt, adapter = outcome(variant("E-AUTH-004-R", {"routes": routes}))
    assert ("paypal_sandbox", "provider_unavailable") in [(e.route_id, e.reason) for e in receipt.excluded_routes]
    assert receipt.selected_route == "provider_a" and adapter.calls == 0


def test_unsupported_method_hands_off():
    assert outcome(load("E-HAND-001"))[0].reason_codes == ["unsupported_payment_method"]


def test_replay_returns_the_original_reference():
    adapter = SimulatedSandboxAdapter()
    first, second = run_episode(load("E-EXEC-002"), adapter, IdempotencyStore())
    assert adapter.calls == 1
    assert first.paypal_called is True and second.paypal_called is False
    assert second.execution_reference.reference == first.execution_reference.reference
    assert second.execution_reference.duplicate is True
    assert second.reason_codes == ["duplicate_request"]


def test_idempotency_conflict_stops():
    store = IdempotencyStore()
    adapter = SimulatedSandboxAdapter()
    episode = load("E-AUTH-004-R")
    decision = decide(episode, store)
    store.put(decision.trace.recheck.idempotency_key, "sha256:different", commit(decision, adapter, IdempotencyStore()).execution_reference)
    again = decide(episode, store)
    assert again.outcome.action == "STOP" and "idempotency_conflict" in again.outcome.reason_codes


@pytest.mark.parametrize(("patch", "action", "code"), [
    ({"identity": {"model_version": "x"}}, "PAUSE", "unapproved_model_version"),
    ({"identity": {"prompt_version": "x"}}, "PAUSE", "unapproved_prompt_version"),
    ({"delegation": {"policy_bundle": "x"}}, "STOP", "unapproved_policy_version"),
])
def test_version_drift_suspends_autonomous_execution(patch, action, code):
    receipt, adapter = outcome(variant("E-AUTH-004-R", patch))
    assert receipt.selected_action == action and code in receipt.reason_codes and adapter.calls == 0


def test_screening_hold_and_budget_stop():
    assert outcome(load("E-RISK-001"))[0].reason_codes == ["screening_hold"]
    budget = variant("E-AUTH-004-R", {"risk_signals": [
        {"kind": "budget", "status": "exhausted", "source": "simulated", "reference": "bud-1"}]})
    assert outcome(budget)[0].reason_codes == ["delegated_budget_exhausted"]


def test_velocity_limit_pauses():
    episode = variant("E-AUTH-004-R", {"risk_signals": [
        {"kind": "velocity", "status": "exceeded", "source": "simulated", "reference": "vel-1"}]})
    assert outcome(episode)[0].reason_codes == ["velocity_limit_exceeded"]


def test_incomplete_mandate_pauses():
    receipt, _ = outcome(variant("E-AUTH-004-R", {"mandate": {"purpose": None}}))
    assert receipt.selected_action == "PAUSE" and "mandate_incomplete" in receipt.reason_codes


# Adapters


def test_paypal_adapter_uses_the_idempotency_key(monkeypatch):
    sent = {}

    def fake(batch_id, amount, currency, note=""):
        sent.update(batch_id=batch_id, amount=str(amount), currency=currency)
        return {"payout_batch_id": "PB123", "batch_status": "SUCCESS", "duplicate": False}

    monkeypatch.setattr("apb.paypal.submit_payout", fake)
    receipt, adapter = outcome(load("E-AUTH-004-R"), PayPalSandboxAdapter())
    assert adapter.calls == 1
    assert sent == {"batch_id": receipt.execution_reference.idempotency_key, "amount": "8000.00", "currency": "EUR"}
    assert receipt.execution_reference.mode == "sandbox" and receipt.execution_reference.reference == "PB123"


def test_public_demo_never_uses_the_real_adapter(monkeypatch):
    monkeypatch.setattr("apb.guard.execution.load_dotenv", lambda: False)
    monkeypatch.setenv("ALLOW_EXECUTE", "true")
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    assert sandbox_execution_enabled() is False


# Receipts


def test_every_receipt_validates_against_the_v3_schema():
    schema = json.loads((episodes.ROOT / "schemas" / "decision-receipt-v3.schema.json").read_text("utf-8"))
    for episode in all_episodes():
        receipt = outcome(episode)[0]
        jsonschema.validate(receipt.model_dump(mode="json"), schema)
        assert receipt.receipt_version == "v3" and receipt.world_version == "world-0.2.0"
        assert receipt.method_version == "apb-0.3-draft" and receipt.policy.bundle_version == "payments-2026.11.1"
        assert receipt.data_mode == "synthetic"


def test_episodes_validate_against_the_episode_schema():
    schema = json.loads((episodes.ROOT / "schemas" / "episode.schema.json").read_text("utf-8"))
    for episode in all_episodes():
        jsonschema.validate(episode.model_dump(mode="json"), schema)


def test_receipts_are_tamper_evident_and_replayable():
    receipt = outcome(load("E-AUTH-004"))[0]
    assert verify_receipt(receipt)
    assert outcome(load("E-AUTH-004"))[0].receipt_id == receipt.receipt_id
    tampered = receipt.model_copy(update={"selected_action": "PAY"})
    assert not verify_receipt(tampered)


def test_receipt_holds_no_credentials_or_raw_accounts():
    text = json.dumps(outcome(load("E-AUTH-004"))[0].model_dump(mode="json"), ensure_ascii=False)
    for secret in ("tokref-", "acct-ref-", "Ignore prior", "PAYPAL_", "access_token", "client_secret"):
        assert secret not in text
    # Accounts appear only masked to the last four digits.
    assert "NL22 •••• 8891" in text


def test_public_view_hides_the_answer_key_and_payload():
    view = json.dumps(public_view(load("E-AUTH-004")))
    assert "answer_key" not in view
    assert "Ignore prior instructions" not in view
    assert "acct-ref-8891" not in view


# Taxonomy and generated files


def test_reason_code_taxonomy_covers_the_spec():
    assert set(SPEC_CODES) <= set(reason_codes.REASON_CODES)
    for item in reason_codes.REASON_CODES.values():
        assert item.explanation and item.owner and item.severity and item.outcomes and item.evidence


def test_generated_docs_and_schemas_are_up_to_date():
    for path, text in docs.outputs().items():
        assert path.read_text("utf-8") == text, f"Rerun: python -m apb.guard.docs ({path.name})"


def test_world_results_are_deterministic_and_committed():
    results = world.run_world()
    assert world.RESULTS_PATH.read_text("utf-8") == world.to_json(results), "Rerun: python -m apb.guard.world"
    guard = next(a for a in results["agents"] if a["agent"] == "apb_guard")["metrics"]
    assert guard["unsafe_execution_rate"] == 0.0 and guard["wrong_refusal_rate"] == 0.0
    naive = next(a for a in results["agents"] if a["agent"] == "naive_executor")["metrics"]
    assert naive["unsafe_execution_rate"] > 0.5


def test_pass_k_and_wilson():
    assert round(world.pass_k(8, 10, 3), 3) == 0.467
    assert round(world.pass_k(8, 10, 5), 3) == 0.222
    low, high = world.wilson(85, 85)
    assert high == 1.0 and 0.95 < low < 1.0
