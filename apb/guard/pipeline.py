"""The APB Guard decision pipeline, as explicit stages.

resolve identity -> resolve delegation and authority -> normalize mandate
-> classify instruction sources -> compare beneficiary facts -> spend and risk
-> approval -> build eligible candidate set -> evaluate economic objective
-> determine PAY / PAUSE / STOP / HANDOFF -> commit-time recheck
-> execute through the adapter only if PAY still holds -> Decision Receipt v3

Every gate is deterministic code. No model output decides authority, policy,
the score, or whether money moves. Security gates always run in full so the
receipt shows every failure; route scoring is reached only when they all allow.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from apb.guard.authority import approved_level, level_for_action, missing_controls, LEVELS
from apb.guard.reason_codes import REASON_CODES
from apb.guard.schemas import (
    AuthorityProfile,
    DecisionOutcome,
    DecisionReceiptV3,
    EpisodeDefinition,
    ExcludedRoute,
    ExecutionRecheck,
    ExecutionReference,
    GateResult,
    Money,
    PolicyDecision,
    Quote,
    ReceiptAgent,
    ReceiptApproval,
    ReceiptAuthority,
    ReceiptExecutionIntegrity,
    ReceiptInstructionIntegrity,
    ReceiptPolicy,
    RouteScore,
)
from apb.guard.versions import POLICY_BUNDLE

GUARD_AUDIENCE = "apb-guard"
REQUIRED_APPROVERS = 2
APPROVAL_TTL_SECONDS = 300
APPROVAL_BOUND_FIELDS = ["mandate_hash", "beneficiary_id", "amount", "currency"]
PAYPAL = "paypal"

GATE_LABELS = {
    "identity": "Identity and change control",
    "authority": "Delegation and authority",
    "mandate": "Mandate completeness",
    "instruction": "Instruction integrity",
    "beneficiary": "Beneficiary integrity",
    "spend": "Spend, velocity and risk signals",
    "approval": "Approval requirement",
    "route": "Route eligibility",
    "quote": "Quote freshness",
    "execution": "Execution integrity at commit",
}
SECURITY_GATES = ("identity", "authority", "mandate", "instruction", "beneficiary", "spend", "approval")
ROUTE_GATES = ("route", "quote", "execution")


def canonical_hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def mandate_hash(episode: EpisodeDefinition) -> str:
    return canonical_hash(episode.mandate.model_dump(mode="json"))


def config_hash(episode: EpisodeDefinition) -> str:
    identity = episode.identity
    return canonical_hash(
        [identity.model, identity.model_version, identity.prompt_version, identity.toolset_version]
    )


@dataclass
class Gate:
    name: str
    status: str = "pass"
    effect: str | None = "ALLOW"
    codes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    explanation: str = ""

    def deny(self, code: str, why: str) -> None:
        self._add(code, why, "fail", "DENY")

    def step_up(self, code: str, why: str, status: str = "fail") -> None:
        self._add(code, why, status, "STEP_UP")

    def handoff(self, code: str, why: str) -> None:
        self._add(code, why, "fail", "HANDOFF")

    def warn(self, code: str | None, why: str) -> None:
        if code:
            self.notes.append(code)
        if self.status == "pass":
            self.status = "warn"
        self._explain(why)

    def _add(self, code: str, why: str, status: str, effect: str) -> None:
        if code not in REASON_CODES:
            raise KeyError(code)
        self.codes.append(code)
        rank = {"ALLOW": 0, "STEP_UP": 1, "HANDOFF": 2, "DENY": 3}
        if self.effect is None or rank[effect] > rank[self.effect]:
            self.effect = effect
        if status == "fail" or self.status in ("pass", "warn"):
            self.status = status
        self._explain(why)

    def _explain(self, why: str) -> None:
        self.explanation = f"{self.explanation} {why}".strip()


@dataclass
class Trace:
    episode: EpisodeDefinition
    now: datetime
    mandate_hash: str
    gates: dict[str, Gate] = field(default_factory=dict)
    beneficiary_mutation: bool = False
    amount_mutation: bool = False
    prompt_injection: bool = False
    approval_required: bool = False
    step_up_granted: bool = False
    authority: AuthorityProfile | None = None
    eligible: list[RouteScore] = field(default_factory=list)
    excluded: list[ExcludedRoute] = field(default_factory=list)
    selected: RouteScore | None = None
    recheck: ExecutionRecheck | None = None
    policy: PolicyDecision | None = None


# Approval ----------------------------------------------------------------------


def approval_valid(trace: Trace, at: datetime) -> bool:
    """A captured approval bound to this exact transaction, unexpired at `at`."""
    approval, mandate = trace.episode.approval, trace.episode.mandate
    return (
        approval is not None
        and approval.status == "captured"
        and len(approval.approvers) >= REQUIRED_APPROVERS
        and approval.bound_mandate_hash == trace.mandate_hash
        and approval.bound_beneficiary_id == mandate.beneficiary_id
        and approval.bound_amount == mandate.amount
        and approval.expires_at is not None
        and approval.expires_at > at
    )


# Stages ------------------------------------------------------------------------


def resolve_identity(trace: Trace) -> Gate:
    gate, ep = Gate("identity"), trace.episode
    identity, inventory = ep.identity, ep.inventory
    if not identity.agent_id:
        gate.deny("identity_missing", "No agent identity was presented.")
    elif identity.token is None:
        gate.deny("identity_invalid", "The agent presented no access token.")
    else:
        if identity.token.expires_at <= trace.now:
            gate.deny("token_expired", "The access token expired before evaluation.")
        if identity.token.audience != GUARD_AUDIENCE:
            gate.deny("token_wrong_audience", f"The token audience is not {GUARD_AUDIENCE}.")
        if ep.delegation and ep.delegation.agent_id != identity.agent_id:
            gate.deny("identity_invalid", "The delegation names a different agent.")
    bundle = ep.delegation.policy_bundle if ep.delegation else POLICY_BUNDLE
    if bundle not in inventory.policy_bundles:
        gate.deny("unapproved_policy_version", f"Policy bundle {bundle} is not approved.")
    if identity.model_version not in inventory.models:
        gate.step_up("unapproved_model_version",
                     f"Model version {identity.model_version} is not approved; autonomous execution is suspended.")
    if identity.prompt_version not in inventory.prompts:
        gate.step_up("unapproved_prompt_version",
                     f"Prompt {identity.prompt_version} is not approved; autonomous execution is suspended.")
    if gate.status == "pass":
        gate.explanation = "Workload identity, token audience and expiry, and approved versions are valid."
    return gate


def resolve_authority(trace: Trace) -> Gate:
    gate, ep = Gate("authority"), trace.episode
    delegation, mandate = ep.delegation, ep.mandate
    requested = level_for_action(mandate.requested_action)
    approved = approved_level(delegation.allowed_actions, delegation.execution_authority) if delegation else -1
    bound = approval_valid(trace, trace.now)

    if delegation is None:
        gate.handoff("delegation_missing", "No delegation links this agent to a principal.")
    else:
        if delegation.expires_at <= trace.now:
            gate.step_up("delegation_expired", "The delegation has expired and needs re-authorization.")
        if mandate.requested_action not in delegation.allowed_actions:
            if requested == 4 and approved >= 2:
                if bound:
                    trace.step_up_granted = True
                    gate.warn(None, "Execution was not delegated, but a transaction-bound approval authorizes this one payment.")
                else:
                    trace.approval_required = True
                    gate.step_up("execution_not_delegated",
                                 f"The agent is approved for Level {approved} ({LEVELS[approved].name}) "
                                 f"and requested Level {requested} (Execute).")
            else:
                gate.deny("action_outside_scope", f"{mandate.requested_action} is outside the delegated scope.")
        over_limit = (
            mandate.amount is not None
            and mandate.amount.currency == delegation.max_amount.currency
            and mandate.amount.value > delegation.max_amount.value
        )
        if over_limit:
            if bound:
                trace.step_up_granted = True
            elif mandate.requested_action in delegation.allowed_actions:
                trace.approval_required = True
                gate.step_up("approval_threshold_exceeded",
                             f"{mandate.amount.label()} is above the {delegation.max_amount.label()} autonomous limit.")
            else:
                gate._explain(
                    f"Execution needs a transaction-bound approval: the delegated autonomous limit is "
                    f"{delegation.max_amount.label()}."
                )
        if mandate.beneficiary_country and mandate.beneficiary_country not in delegation.allowed_countries:
            gate.deny("action_outside_scope", f"Payments to {mandate.beneficiary_country} are not delegated.")

    gap = requested > max(approved, -1)
    trace.authority = AuthorityProfile(
        agent_id=ep.identity.agent_id or "unknown",
        delegation_id=delegation.delegation_id if delegation else None,
        requested_action=mandate.requested_action,
        requested_level=requested,
        approved_level=approved,
        authority_gap=gap,
        missing_controls=missing_controls(approved, requested) if gap else [],
        evidence_required=list(LEVELS[min(requested, 5)].evidence) if gap else [],
        can_increase=gap and requested <= 4,
        increase_path=(
            "Transaction-bound approval for this payment, or re-evaluation in APB World "
            "for a standing Level 4 profile." if gap else "No increase needed."
        ),
        expires_at=delegation.expires_at if delegation else None,
        reauthorize_by=(delegation.expires_at - timedelta(hours=1)) if delegation else None,
    )
    if gate.status == "pass" and not gate.explanation:
        gate.explanation = "The requested action is inside the delegated scope and limit."
    return gate


def normalize_mandate(trace: Trace) -> Gate:
    gate, mandate = Gate("mandate"), trace.episode.mandate
    missing = [
        name for name in ("amount", "beneficiary_id", "payment_method", "purpose")
        if getattr(mandate, name) in (None, "")
    ]
    if missing:
        gate.step_up("mandate_incomplete", f"Missing: {', '.join(missing)}.")
    else:
        gate.explanation = "The authenticated mandate is complete."
    return gate


def classify_sources(trace: Trace) -> Gate:
    gate, ep = Gate("instruction"), trace.episode
    master = ep.beneficiary
    for source in ep.sources:
        if source.kind != "external_content":
            continue
        facts = source.extracted
        if facts.contains_instructions:
            trace.prompt_injection = True
            gate.warn("prompt_injection_detected",
                      f"{source.label} contains instructions aimed at the agent; quarantined as data.")
        if master and facts.account_ref and facts.account_ref != master.account_ref:
            trace.beneficiary_mutation = True
            gate.deny("external_instruction_conflict",
                      f"{source.label} names account {facts.account_masked}, which conflicts with the authenticated mandate.")
        if facts.amount and ep.mandate.amount:
            if facts.amount.currency != ep.mandate.amount.currency:
                trace.amount_mutation = True
                gate.deny("currency_changed", f"{source.label} changes the currency.")
            elif facts.amount.value != ep.mandate.amount.value:
                trace.amount_mutation = True
                gate.deny("amount_changed", f"{source.label} changes the amount to {facts.amount.label()}.")
    if gate.status == "pass":
        gate.explanation = "External content agrees with the mandate and master data."
    return gate


def compare_beneficiary(trace: Trace) -> Gate:
    gate, ep = Gate("beneficiary"), trace.episode
    master = ep.beneficiary
    if master is None:
        gate.step_up("mandate_incomplete", "No beneficiary record to compare against.")
        return gate
    if trace.beneficiary_mutation:
        gate.deny("beneficiary_mutation_detected",
                  f"The instruction changes the account away from the verified {master.account_masked}.")
    if master.name_check == "no_match":
        gate.deny("beneficiary_no_match", f"Name check returned {master.returned_name!r} for {master.name!r}.")
    elif master.name_check == "close_match":
        if approval_valid(trace, trace.now):
            gate.warn(None, "Close name match confirmed through the bound approval.")
        else:
            trace.approval_required = True
            gate.step_up("beneficiary_close_match",
                         f"Name check returned {master.returned_name!r} for {master.name!r}; the payer must confirm.")
    if master.first_time and not master.verified:
        trace.approval_required = True
        gate.warn(None, "First-time beneficiary: verification and a transaction-bound approval are required.")
    if gate.status == "pass":
        gate.explanation = f"Beneficiary {master.name} matches verified master data ({master.account_masked})."
    return gate


def check_spend_and_risk(trace: Trace) -> Gate:
    gate = Gate("spend")
    for signal in trace.episode.risk_signals:
        if signal.kind == "screening_hold" and signal.status == "hold":
            gate.deny("screening_hold", f"External screening hold ({signal.source}): {signal.reference}.")
        elif signal.kind == "velocity" and signal.status == "exceeded":
            gate.step_up("velocity_limit_exceeded", "Payment velocity limit exceeded.")
        elif signal.kind == "budget" and signal.status == "exhausted":
            gate.deny("delegated_budget_exhausted", "Delegated budget is fully used.")
    if gate.status == "pass":
        gate.explanation = "Spend, velocity and external risk signals permit the payment."
    return gate


def check_approval(trace: Trace, upstream_denied: bool) -> Gate:
    gate, approval = Gate("approval"), trace.episode.approval
    if not trace.approval_required and not trace.step_up_granted:
        gate.explanation = "No transaction-bound approval is required."
        return gate
    if upstream_denied:
        # The requirement is recorded, but no approval can release a stopped instruction.
        gate.status, gate.effect = "missing", None
        gate.explanation = "A transaction-bound approval would be required, but no approval can release this instruction."
        return gate
    if approval_valid(trace, trace.now):
        gate.explanation = f"Approval {approval.approval_id} is captured and bound to this transaction."
        return gate
    if approval and approval.status == "captured" and approval.expires_at and approval.expires_at <= trace.now:
        gate.step_up("approval_expired", "The approval expired.", status="missing")
    else:
        gate.step_up("approval_missing",
                     f"Requires {REQUIRED_APPROVERS} approvers bound to {', '.join(APPROVAL_BOUND_FIELDS)}.",
                     status="missing")
    return gate


def _quote_for(quotes: list[Quote], route_id: str) -> Quote | None:
    return next((quote for quote in quotes if quote.route_id == route_id), None)


def _fresh(quote: Quote, at: datetime) -> bool:
    return quote.quoted_at + timedelta(seconds=quote.valid_for_seconds) > at


def build_candidates(trace: Trace) -> Gate:
    """Eligibility first, then the economic objective among eligible routes only."""
    gate, ep = Gate("route"), trace.episode
    method, deadline = ep.mandate.payment_method, ep.mandate.deadline_minutes
    eligible = []
    for route in sorted(ep.routes, key=lambda r: r.route_id):
        quote = _quote_for(ep.quotes, route.route_id)
        reason = None
        if not (route.approved and route.healthy):
            reason = "provider_unavailable"
        elif method not in route.payment_methods:
            reason = "payout_method_unavailable"
        elif deadline is not None and route.delivery_minutes > deadline:
            reason = "deadline_violation"
        elif quote is None or not _fresh(quote, trace.now):
            reason = "quote_expired"
        if reason:
            trace.excluded.append(ExcludedRoute(route_id=route.route_id, reason=reason))
        else:
            eligible.append((route, quote))
    if not eligible:
        reasons = {item.reason for item in trace.excluded}
        if reasons == {"payout_method_unavailable"}:
            gate.handoff("unsupported_payment_method", f"No approved route supports {method}.")
        else:
            gate.handoff("manual_review_required", "No route meets the mandate within the declared candidate set.")
        return gate
    best = max(quote.recipient_amount.value for _, quote in eligible)
    trace.eligible = [
        RouteScore(
            route_id=route.route_id, provider=route.provider, recipient_amount=quote.recipient_amount,
            fee=quote.fee, delivery_minutes=route.delivery_minutes,
            dvs=float((quote.recipient_amount.value / best * 100).quantize(Decimal("0.1"))),
        )
        for route, quote in eligible
    ]
    trace.selected = min(
        trace.eligible,
        key=lambda s: (-s.recipient_amount.value, s.delivery_minutes, s.fee.value, s.route_id),
    )
    gate.explanation = (
        f"{len(trace.eligible)} of {len(ep.routes)} routes are eligible. Best observed eligible route "
        f"within the declared candidate set and quote window: {trace.selected.route_id}."
    )
    return gate


def check_quote(trace: Trace) -> Gate:
    gate = Gate("quote")
    quote = _quote_for(trace.episode.quotes, trace.selected.route_id)
    gate.explanation = f"Quote {quote.quote_id} is fresh at decision time."
    return gate


def idempotency_key(trace: Trace) -> str:
    approval = trace.episode.approval.approval_id if trace.episode.approval else "none"
    route = trace.selected.route_id if trace.selected else "none"
    return "idem-" + canonical_hash([trace.mandate_hash, route, approval])[7:31]


def recheck_at_commit(trace: Trace, store: "IdempotencyStore") -> Gate:
    gate, ep = Gate("execution"), trace.episode
    commit, route = ep.commit, trace.selected.route_id
    fresh_quotes = ep.commit.quotes if ep.commit.quotes is not None else ep.quotes
    earlier = _quote_for(ep.quotes, route)
    fresh = _quote_for(fresh_quotes, route)
    status = "fresh"
    if fresh is None or not _fresh(fresh, commit.at):
        status = "expired"
        gate.step_up("quote_expired", "The selected route's quote is not valid at commit.")
    elif fresh.recipient_amount.value < earlier.recipient_amount.value * (1 - commit.drift_tolerance):
        status = "drifted"
        gate.step_up("quote_drift_exceeded",
                     f"Recipient amount fell from {earlier.recipient_amount.label()} to {fresh.recipient_amount.label()}.")
    approval_ok = None
    if trace.approval_required or trace.step_up_granted:
        approval_ok = approval_valid(trace, commit.at)
        if not approval_ok:
            gate.step_up("approval_expired", "The approval is not valid at commit.")
    key = idempotency_key(trace)
    duplicate = store.check(key, trace.mandate_hash)
    if duplicate == "conflict":
        gate.deny("idempotency_conflict", "This idempotency key was used for a different request.")
    trace.recheck = ExecutionRecheck(
        performed=True, at=commit.at, route_id=route, quote_rechecked=True, quote_status=status,
        approval_valid=approval_ok, idempotency_key=key,
        duplicate_check="duplicate" if duplicate == "duplicate" else "conflict" if duplicate == "conflict" else "passed",
    )
    if gate.status == "pass":
        gate.explanation = (
            "Identity, authority, approval, beneficiary and quote still hold at commit; "
            + ("this key was already executed, so the original result is returned." if duplicate == "duplicate"
               else "the idempotency key is unused.")
        )
    return gate


# Policy, outcome, execution ------------------------------------------------------


def policy_decision(trace: Trace) -> PolicyDecision:
    effects = [gate.effect for gate in trace.gates.values() if gate.effect]
    for effect in ("DENY", "HANDOFF", "STEP_UP"):
        if effect in effects:
            break
    else:
        effect = "ALLOW"
    reason = next(
        (code for gate in trace.gates.values() if gate.effect == effect for code in gate.codes),
        None,
    )
    decision_id = "policy-dec-" + canonical_hash([trace.mandate_hash, effect, reason])[7:19]
    if effect == "STEP_UP" and trace.approval_required:
        return PolicyDecision(
            decision_id=decision_id, effect=effect, reason=reason, bundle_version=POLICY_BUNDLE,
            required_approvers=REQUIRED_APPROVERS, bound_fields=APPROVAL_BOUND_FIELDS,
            expires_in_seconds=APPROVAL_TTL_SECONDS,
        )
    return PolicyDecision(decision_id=decision_id, effect=effect, reason=reason, bundle_version=POLICY_BUNDLE)


SECURITY_OUTCOMES = {
    "beneficiary_mutation_detected": "UNAUTHORIZED_BENEFICIARY_MUTATION",
    "amount_changed": "UNAUTHORIZED_AMOUNT_MUTATION",
    "currency_changed": "UNAUTHORIZED_AMOUNT_MUTATION",
    "screening_hold": "BLOCKED_SCREENING_HOLD",
    "beneficiary_no_match": "BLOCKED_BENEFICIARY_NO_MATCH",
    "idempotency_conflict": "BLOCKED_IDEMPOTENCY_CONFLICT",
    "unapproved_policy_version": "BLOCKED_UNAPPROVED_POLICY",
    "action_outside_scope": "BLOCKED_OUTSIDE_SCOPE",
    "delegated_budget_exhausted": "BLOCKED_BUDGET_EXHAUSTED",
}


def _outcome(trace: Trace, effect: str, codes: list[str]) -> tuple[str, str]:
    if effect == "DENY":
        found = next((SECURITY_OUTCOMES[c] for c in codes if c in SECURITY_OUTCOMES), None)
        if found is None and any(c.startswith(("identity", "token")) for c in codes):
            found = "BLOCKED_INVALID_IDENTITY"
        return "STOP", found or "BLOCKED_BY_POLICY"
    if effect == "HANDOFF":
        return "HANDOFF", "HANDOFF_MANUAL_REVIEW"
    if effect == "STEP_UP":
        if any(c.startswith("unapproved_") for c in codes):
            return "PAUSE", "CHANGE_CONTROL_HOLD"
        return "PAUSE", "AUTHORIZED_REQUIRES_STEP_UP"
    if trace.selected and trace.selected.provider == PAYPAL:
        return "PAY", "AUTHORIZED_EXECUTION"
    return "HANDOFF", "HANDOFF_SELECTED_ROUTE"


EXPLANATIONS = {
    "STOP": "APB stops this payment before route scoring or PayPal execution. Nothing executes.",
    "PAUSE": "A resolvable control needs step-up approval, confirmation or fresh evidence before anything executes.",
    "PAY": "Every gate passed and the PayPal sandbox route is the best observed eligible route; it may execute.",
    "HANDOFF": "The decision is handed off. PayPal is not called.",
}


@dataclass
class Decision:
    trace: Trace
    outcome: DecisionOutcome
    policy: PolicyDecision
    gates: list[GateResult]


class IdempotencyStore:
    """Execution results by idempotency key. One process; a demo, not a ledger."""

    def __init__(self) -> None:
        self._results: dict[str, tuple[str, ExecutionReference]] = {}

    def check(self, key: str, request_hash: str) -> str:
        stored = self._results.get(key)
        if stored is None:
            return "new"
        return "duplicate" if stored[0] == request_hash else "conflict"

    def get(self, key: str) -> ExecutionReference | None:
        stored = self._results.get(key)
        return stored[1] if stored else None

    def put(self, key: str, request_hash: str, reference: ExecutionReference) -> None:
        self._results[key] = (request_hash, reference)


def decide(episode: EpisodeDefinition, store: IdempotencyStore | None = None) -> Decision:
    """Run every stage in order and return the decision. Nothing executes here."""
    store = store or IdempotencyStore()
    trace = Trace(episode=episode, now=episode.evaluated_at, mandate_hash=mandate_hash(episode))
    for stage in (resolve_identity, resolve_authority, normalize_mandate, classify_sources,
                  compare_beneficiary, check_spend_and_risk):
        gate = stage(trace)
        trace.gates[gate.name] = gate
    denied = any(g.effect == "DENY" for g in trace.gates.values())
    trace.gates["approval"] = check_approval(trace, denied)

    if all(g.effect in ("ALLOW", None) for g in trace.gates.values()):
        trace.gates["route"] = build_candidates(trace)
        if trace.selected:
            trace.gates["quote"] = check_quote(trace)
            trace.gates["execution"] = recheck_at_commit(trace, store)

    policy = policy_decision(trace)
    trace.policy = policy
    # Codes that drive the outcome lead, strongest effect first, then gate order.
    # Recorded-only notes stay on their gate unless nothing else applies.
    rank = {"DENY": 0, "HANDOFF": 1, "STEP_UP": 2}
    ordered = sorted(
        (g for g in trace.gates.values() if g.effect in rank and g.codes),
        key=lambda g: (rank[g.effect], (SECURITY_GATES + ROUTE_GATES).index(g.name)),
    )
    headline = list(dict.fromkeys(c for g in ordered for c in g.codes))
    if not headline:
        headline = [c for g in trace.gates.values() for c in g.notes]
    action, security = _outcome(trace, policy.effect, headline)
    if trace.recheck and trace.recheck.duplicate_check == "duplicate" and action == "PAY":
        headline = headline + ["duplicate_request"]
        security = "DUPLICATE_SUPPRESSED"

    gates = []
    for order, name in enumerate(SECURITY_GATES + ROUTE_GATES, start=1):
        gate = trace.gates.get(name)
        if gate is None:
            gates.append(GateResult(
                order=order, gate=name, label=GATE_LABELS[name], status="not_reached", effect=None,
                reason_codes=[], evidence_ref=f"evidence:{name}:not_reached", policy_version=POLICY_BUNDLE,
                explanation="Not reached: economic value cannot compensate for a failed security gate.",
            ))
            continue
        gates.append(GateResult(
            order=order, gate=name, label=GATE_LABELS[name], status=gate.status, effect=gate.effect,
            reason_codes=gate.codes + gate.notes, policy_version=POLICY_BUNDLE, explanation=gate.explanation,
            evidence_ref=f"evidence:{name}:" + canonical_hash([trace.mandate_hash, name, gate.codes])[7:19],
        ))
    outcome = DecisionOutcome(
        action=action, reason_codes=headline,
        selected_route=trace.selected.route_id if trace.selected and action in ("PAY", "HANDOFF") else None,
        selected_provider=trace.selected.provider if trace.selected and action in ("PAY", "HANDOFF") else None,
        paypal_called=False, security_outcome=security, explanation=EXPLANATIONS[action],
    )
    return Decision(trace=trace, outcome=outcome, policy=policy, gates=gates)


def build_receipt(decision: Decision, execution: ExecutionReference | None) -> DecisionReceiptV3:
    trace, ep = decision.trace, decision.trace.episode
    outcome = decision.outcome
    approval = ep.approval
    approval_status = "not_required"
    if trace.approval_required or trace.step_up_granted:
        approval_status = approval.status if approval and approval_valid(trace, ep.evaluated_at) else (
            "expired" if approval and approval.status == "captured" else "missing"
        )
    delegation = ep.delegation
    body: dict[str, Any] = {
        "episode_id": ep.episode_id,
        "evaluated_at": ep.evaluated_at,
        "mandate_hash": trace.mandate_hash,
        "agent": ReceiptAgent(
            id=ep.identity.agent_id, model=ep.identity.model, model_version=ep.identity.model_version,
            prompt_version=ep.identity.prompt_version, toolset_version=ep.identity.toolset_version,
            config_hash=config_hash(ep),
        ),
        "authority": ReceiptAuthority(
            delegation_id=delegation.delegation_id if delegation else None,
            principal_id=ep.principal.principal_id,
            requested_level=trace.authority.requested_level,
            approved_level=trace.authority.approved_level,
            execute=bool(delegation and delegation.execution_authority),
            max_amount=f"{delegation.max_amount.currency} {delegation.max_amount.value}" if delegation else None,
            step_up_granted=trace.step_up_granted,
        ),
        "instruction_integrity": ReceiptInstructionIntegrity(
            external_content_untrusted=True,
            prompt_injection=trace.prompt_injection,
            beneficiary_mutation=trace.beneficiary_mutation,
            amount_mutation=trace.amount_mutation,
            sources=[
                {"source_id": s.source_id, "kind": s.kind, "priority": s.priority, "content_hash": s.content_hash}
                for s in ep.sources
            ],
        ),
        "policy": ReceiptPolicy(
            engine="APB reference policy", bundle_version=decision.policy.bundle_version,
            decision_id=decision.policy.decision_id, effect=decision.policy.effect,
        ),
        "approval": ReceiptApproval(
            required=trace.approval_required or trace.step_up_granted, status=approval_status,
            approval_id=approval.approval_id if approval and approval_status == "captured" else None,
            bound_mandate_hash=approval.bound_mandate_hash if approval and approval_status == "captured" else None,
        ),
        "execution_integrity": ReceiptExecutionIntegrity(
            idempotency_key=trace.recheck.idempotency_key if trace.recheck else None,
            quote_rechecked=bool(trace.recheck and trace.recheck.quote_rechecked),
            duplicate_check=trace.recheck.duplicate_check if trace.recheck else "not_reached",
        ),
        "gates": decision.gates,
        "candidate_set": sorted(route.route_id for route in ep.routes),
        "excluded_routes": trace.excluded,
        "route_scores": trace.eligible,
        "selected_route": outcome.selected_route,
        "selected_action": outcome.action,
        "security_outcome": outcome.security_outcome,
        "reason_codes": outcome.reason_codes,
        "paypal_called": outcome.paypal_called,
        "trace_manifest_hash": canonical_hash({
            "episode": ep.episode_id,
            "sources": [s.content_hash for s in ep.sources],
            "gates": [g.model_dump(mode="json") for g in decision.gates],
        }),
        "data_mode": ep.data_mode,
        "execution_reference": execution,
    }
    pending = DecisionReceiptV3(**body, receipt_id="pending", receipt_hash="pending")
    digest = receipt_digest(pending)
    return pending.model_copy(update={"receipt_id": "apb-rec-" + digest[7:19], "receipt_hash": digest})


def receipt_digest(receipt: DecisionReceiptV3) -> str:
    """The hash of everything in the receipt except its own id and hash."""
    data = receipt.model_dump(mode="json")
    data.pop("receipt_id")
    data.pop("receipt_hash")
    return canonical_hash(data)


def verify_receipt(receipt: DecisionReceiptV3) -> bool:
    """Recompute the content hash. True if nothing in the receipt changed."""
    return receipt.receipt_hash == receipt_digest(receipt) and receipt.receipt_id == "apb-rec-" + receipt.receipt_hash[7:19]
