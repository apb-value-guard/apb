"""Connect and bind: the stage before the payment gates."""

import pytest

from apb.guard import reason_codes
from apb.guard.connect import (
    MANUAL_MAX_AUTO_LEVEL,
    create_binding,
    domain_matches,
)
from apb.guard.episodes import _merge, load
from apb.guard.execution import SimulatedSandboxAdapter, run_episode
from apb.guard.pipeline import IdempotencyStore, decide, verify_receipt
from apb.guard.schemas import EpisodeDefinition

NEW_CODES = ("connector_domain_mismatch", "oauth_redirect_mismatch", "scope_exceeds_purpose",
             "connector_unreviewed", "binding_expired", "payee_outside_binding")
EXPECTED = {
    "E-CONN-001": ("STOP", ["connector_domain_mismatch"]),
    "E-CONN-002": ("PAUSE", ["scope_exceeds_purpose"]),
    "E-CONN-003": ("PAY", []),
    "E-CONN-004": ("PAUSE", ["connector_unreviewed"]),
    "E-BIND-001": ("STOP", ["binding_expired"]),
    "E-BIND-002": ("PAUSE", ["payee_outside_binding"]),
}


def variant(episode_id: str, patch: dict) -> EpisodeDefinition:
    return EpisodeDefinition.model_validate(_merge(load(episode_id).model_dump(mode="json"), patch))


def run(episode: EpisodeDefinition):
    adapter = SimulatedSandboxAdapter()
    return run_episode(episode, adapter, IdempotencyStore())[-1], adapter


def connect_gate(receipt):
    return next(g for g in receipt.connection.gates if g.gate == "connect")


@pytest.mark.parametrize("episode_id", sorted(EXPECTED))
def test_episode_outcome_and_answer_key(episode_id):
    episode = load(episode_id)
    receipt, adapter = run(episode)
    action, codes = EXPECTED[episode_id]
    assert (receipt.selected_action, receipt.reason_codes) == (action, codes)
    assert (episode.answer_key.action, episode.answer_key.reason_codes) == (action, codes)
    assert receipt.security_outcome == episode.answer_key.security_outcome
    assert adapter.calls == (1 if action == "PAY" else 0)
    assert verify_receipt(receipt)


def test_reason_codes_have_explanations_and_owners():
    for code in NEW_CODES:
        item = reason_codes.describe(code)
        assert item.explanation and item.owner and item.evidence
    markdown = reason_codes.markdown()
    assert all(f"`{code}`" in markdown for code in NEW_CODES)


# Connect checks


@pytest.mark.parametrize("domain, expected, ok", [
    ("ledgerline.example", "ledgerline.example", True),
    ("mcp.ledgerline.example", "ledgerline.example", True),
    ("MCP.Ledgerline.Example.", "ledgerline.example", True),
    ("mcp.ledger1ine.example", "ledgerline.example", False),
    ("ledgerline.example.evil.example", "ledgerline.example", False),
    ("notledgerline.example", "ledgerline.example", False),
])
def test_domain_matching_rejects_lookalikes(domain, expected, ok):
    assert domain_matches(domain, expected) is ok


@pytest.mark.parametrize("patch", [
    {"oauth_login_domain": "login.bank-secure.example"},
    {"oauth_redirect_domain": "callback.collector.example"},
])
def test_oauth_must_log_in_at_the_bank_and_return_to_the_platform(patch):
    receipt, adapter = run(variant("E-CONN-003", {"connection": patch}))
    assert receipt.selected_action == "STOP"
    assert receipt.reason_codes == ["oauth_redirect_mismatch"]
    assert adapter.calls == 0


def test_scopes_may_not_exceed_the_requested_level():
    receipt, _ = run(variant("E-CONN-003", {"connection": {"requested_level": 3}}))
    assert receipt.reason_codes == ["scope_exceeds_purpose"]
    assert "payment.execute" in connect_gate(receipt).explanation


def test_scopes_inside_purpose_and_level_bind_at_that_level():
    episode = variant("E-CONN-002", {"connection": {"requested_level": 1,
                                                    "requested_scopes": ["payment.read", "payment.quote"]}})
    decision = decide(episode)
    binding = decision.trace.binding
    assert binding is not None and binding.authority_level == 1
    assert binding.allowed_actions == ["payment.read", "payment.quote"]
    # The payment itself then needs Level 4, which a Level 1 binding never reaches.
    assert decision.outcome.action == "STOP"
    assert "action_outside_scope" in decision.outcome.reason_codes


def test_manual_connector_is_accepted_up_to_level_2_only():
    scopes = ["payment.read", "payment.quote", "payment.propose"]
    at_two = decide(variant("E-CONN-004", {"connection": {"requested_level": MANUAL_MAX_AUTO_LEVEL,
                                                          "requested_scopes": scopes}}))
    assert at_two.trace.binding.authority_level == 2
    gate = next(g for g in at_two.pre_gates if g.gate == "connect")
    assert gate.status == "warn" and gate.effect == "ALLOW"
    at_three = decide(variant("E-CONN-004", {"connection": {"requested_level": 3,
                                                            "requested_scopes": scopes + ["payment.initiate"]}}))
    assert at_three.trace.binding is None
    assert at_three.outcome.reason_codes == ["connector_unreviewed"]


def test_a_failed_connection_reaches_no_payment_gate():
    receipt, adapter = run(load("E-CONN-001"))
    assert [g.status for g in receipt.gates] == ["not_reached"] * 10
    assert receipt.connection.binding is None
    assert next(g for g in receipt.connection.gates if g.gate == "bind").status == "not_reached"
    assert adapter.calls == 0 and not receipt.paypal_called


# Bind


def test_binding_record_carries_every_field_and_a_content_hash():
    episode = load("E-CONN-003")
    binding = create_binding(episode.connection, episode.grant)
    assert binding == create_binding(episode.connection, episode.grant)
    assert (binding.user_id, binding.agent_id, binding.vendor) == (
        "corporate-user-118", "treasury-agent-42", "Ledgerline (synthetic)")
    assert binding.authority_level == 4
    assert binding.spending_limit.label() == "€10,000"
    assert binding.allowed_actions[-1] == "payment.execute"
    assert binding.allowed_payees == ["sup-0017", "sup-0042"]
    assert binding.expires_at.isoformat() == "2026-12-09T09:00:00+00:00"
    assert binding.hash.startswith("sha256:") and binding.binding_id == "bnd-" + binding.hash[7:19]
    other = create_binding(episode.connection, episode.grant.model_copy(update={"allowed_payees": ["sup-0042"]}))
    assert other.hash != binding.hash


def test_receipt_carries_the_binding_and_payment_gates_use_it():
    receipt, _ = run(load("E-CONN-003"))
    binding = receipt.connection.binding
    assert receipt.authority.delegation_id == binding.binding_id
    assert receipt.authority.approved_level == 4 and receipt.authority.execute
    assert receipt.authority.max_amount == "EUR 10000"
    assert next(g for g in receipt.connection.gates if g.gate == "bind").evidence_ref == (
        "evidence:bind:" + binding.hash[7:19])


def test_payment_over_the_binding_limit_needs_approval():
    receipt, _ = run(variant("E-CONN-003", {"grant": {"spending_limit": {"currency": "EUR", "value": "5000"}}}))
    assert receipt.selected_action == "PAUSE"
    assert receipt.reason_codes[0] == "approval_threshold_exceeded"


def test_binding_expiry_is_checked_at_payment_time_not_bind_time():
    expired = load("E-BIND-001")
    decision = decide(expired)
    assert decision.trace.binding is not None  # bind succeeded when it was granted
    authority = next(g for g in decision.gates if g.gate == "authority")
    assert authority.effect == "DENY" and authority.reason_codes == ["binding_expired"]


def test_tampering_with_the_connection_breaks_the_receipt():
    receipt, _ = run(load("E-CONN-003"))
    binding = receipt.connection.binding.model_copy(update={"allowed_payees": ["sup-9999"]})
    tampered = receipt.model_copy(update={"connection": receipt.connection.model_copy(update={"binding": binding})})
    assert verify_receipt(receipt) and not verify_receipt(tampered)


def test_receipts_without_a_connection_hash_as_before():
    """The stage is optional: an episode without it has no connection block, and the
    digest leaves the empty block out, so earlier receipts still verify."""
    receipt, _ = run(load("E-AUTH-004"))
    assert receipt.connection is None and verify_receipt(receipt)
    assert len(receipt.gates) == 10
