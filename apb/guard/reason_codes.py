"""The APB Guard reason-code taxonomy.

Each code has a stable machine value, a user-facing explanation, an operational
owner, a severity, the outcomes it may lead to, whether it can be overridden,
and the evidence a receipt must carry for it. docs/REASON_CODES.md is generated
from this table, and a test keeps the two in step.
"""

from typing import Literal, NamedTuple

Action = Literal["PAY", "PAUSE", "STOP", "HANDOFF"]
Severity = Literal["critical", "high", "medium", "low", "info"]
Owner = Literal[
    "security", "payments_operations", "fraud_and_financial_crime", "ai_governance",
    "treasury", "approver",
]


class ReasonCode(NamedTuple):
    code: str
    gate: str
    explanation: str
    owner: Owner
    severity: Severity
    outcomes: tuple[Action, ...]
    override_allowed: bool
    evidence: str


_CODES = [
    # Identity
    ReasonCode("identity_missing", "identity", "No agent identity was presented.",
               "security", "critical", ("STOP",), False, "Request metadata and identity-broker result."),
    ReasonCode("identity_invalid", "identity", "The agent identity is unknown, revoked or does not match the workload.",
               "security", "critical", ("STOP",), False, "Identity-broker result and revocation state."),
    ReasonCode("token_expired", "identity", "The agent's access token has expired.",
               "security", "high", ("STOP",), False, "Token metadata: issued, expiry and evaluation time."),
    ReasonCode("token_wrong_audience", "identity", "The token was issued for another audience than APB Guard.",
               "security", "high", ("STOP",), False, "Token audience and expected audience."),
    # Delegation and authority
    ReasonCode("delegation_missing", "authority", "No delegation links the agent to a principal for this action.",
               "security", "high", ("HANDOFF",), False, "Delegation lookup result."),
    ReasonCode("delegation_expired", "authority", "The delegation has expired and must be re-authorized.",
               "security", "high", ("PAUSE",), True, "Delegation ID, expiry and evaluation time."),
    ReasonCode("action_outside_scope", "authority", "The requested action is not in the delegated scope.",
               "security", "critical", ("STOP",), False, "Requested action and allowed actions."),
    ReasonCode("execution_not_delegated", "authority", "The agent may prepare this payment but was never delegated authority to execute it.",
               "security", "high", ("PAUSE", "STOP"), True, "Delegation scope, requested level and approved level."),
    ReasonCode("approval_threshold_exceeded", "authority", "The amount is above the agent's autonomous limit, so a transaction-bound approval is required.",
               "approver", "high", ("PAUSE",), True, "Amount, delegated limit and policy decision."),
    ReasonCode("approval_missing", "approval", "A required transaction-bound approval has not been captured.",
               "approver", "high", ("PAUSE",), True, "Policy decision with required approvers and bound fields."),
    ReasonCode("approval_expired", "approval", "The approval expired before commit and must be captured again.",
               "approver", "medium", ("PAUSE",), True, "Approval ID, expiry and commit time."),
    # Instruction integrity
    ReasonCode("external_instruction_conflict", "instruction", "Untrusted content conflicts with the authenticated mandate or master data.",
               "security", "critical", ("STOP",), False, "Source hashes, extracted values and the comparison."),
    ReasonCode("prompt_injection_detected", "instruction", "Untrusted content contains instructions aimed at the agent. It is quarantined as data.",
               "security", "high", ("PAY", "PAUSE", "STOP", "HANDOFF"), False, "Source hash, matched pattern and quarantine record."),
    ReasonCode("beneficiary_mutation_detected", "beneficiary", "The beneficiary account differs from the verified supplier master record.",
               "fraud_and_financial_crime", "critical", ("STOP",), False, "Masked accounts, account references and master-data version."),
    ReasonCode("amount_changed", "instruction", "The amount differs between the mandate and another source.",
               "payments_operations", "critical", ("STOP",), False, "Amounts per source and their hashes."),
    ReasonCode("currency_changed", "instruction", "The currency differs between the mandate and another source.",
               "payments_operations", "critical", ("STOP",), False, "Currencies per source and their hashes."),
    # Beneficiary
    ReasonCode("beneficiary_close_match", "beneficiary", "The payee name check returned a close match. The payer must confirm first.",
               "payments_operations", "medium", ("PAUSE",), True, "Expected and returned names, check reference."),
    ReasonCode("beneficiary_no_match", "beneficiary", "The payee name check returned no match. Ordinary chat cannot release it.",
               "fraud_and_financial_crime", "critical", ("STOP",), False, "Expected and returned names, check reference."),
    ReasonCode("screening_hold", "safety", "An external screening result is on hold. APB does not screen; it acts on the signal.",
               "fraud_and_financial_crime", "critical", ("STOP",), False, "Screening reference and source."),
    # Mandate
    ReasonCode("mandate_incomplete", "mandate", "A field the payment needs is missing from the mandate.",
               "payments_operations", "medium", ("PAUSE",), True, "List of missing fields."),
    ReasonCode("required_fact_not_discovered", "mandate", "The agent did not discover a fact the episode requires before acting.",
               "ai_governance", "high", ("PAUSE", "STOP"), False, "Released fact IDs and required fact IDs."),
    # Spend and velocity
    ReasonCode("velocity_limit_exceeded", "spend", "The agent exceeded its payment velocity limit.",
               "fraud_and_financial_crime", "high", ("PAUSE", "STOP"), True, "Counter, window and limit."),
    ReasonCode("delegated_budget_exhausted", "spend", "The delegated budget is fully used.",
               "treasury", "high", ("STOP",), False, "Budget, consumption and window."),
    # Routes and quotes
    ReasonCode("deadline_violation", "route", "The route's delivery time is longer than the mandate allows.",
               "payments_operations", "low", ("PAY", "PAUSE", "STOP", "HANDOFF"), False, "Route delivery time and mandate deadline."),
    ReasonCode("payout_method_unavailable", "route", "The route cannot pay by the mandate's payment method.",
               "payments_operations", "low", ("PAY", "PAUSE", "STOP", "HANDOFF"), False, "Route methods and mandate method."),
    ReasonCode("provider_unavailable", "route", "The provider is unhealthy or not approved, so its route is excluded.",
               "payments_operations", "medium", ("PAY", "PAUSE", "STOP", "HANDOFF"), False, "Provider health and approval state."),
    ReasonCode("unsupported_payment_method", "route", "No approved route supports the payment method, so the case is handed off.",
               "payments_operations", "medium", ("HANDOFF",), False, "Mandate method and every route's methods."),
    ReasonCode("quote_expired", "execution", "The quote expired before it was used.",
               "payments_operations", "medium", ("PAUSE",), True, "Quote ID, expiry and evaluation time."),
    ReasonCode("quote_drift_exceeded", "execution", "The quote at commit is worse than the drift tolerance allows.",
               "payments_operations", "medium", ("PAUSE",), True, "Earlier and fresh recipient amounts, tolerance."),
    # Execution integrity
    ReasonCode("duplicate_request", "execution", "This idempotency key was already executed. The original result is returned and nothing is paid again.",
               "payments_operations", "info", ("PAY",), False, "Idempotency key and original execution reference."),
    ReasonCode("idempotency_conflict", "execution", "This idempotency key was used with a different payload.",
               "security", "critical", ("STOP",), False, "Key, stored request hash and new request hash."),
    # Change control
    ReasonCode("unapproved_model_version", "identity", "The model version is not in the approved inventory. Autonomous execution is suspended.",
               "ai_governance", "high", ("PAUSE",), True, "Model version and approved inventory."),
    ReasonCode("unapproved_prompt_version", "identity", "The prompt version is not approved. Autonomous execution is suspended.",
               "ai_governance", "high", ("PAUSE",), True, "Prompt hash and approved inventory."),
    ReasonCode("unapproved_policy_version", "identity", "The policy bundle is not the approved bundle.",
               "security", "critical", ("STOP",), False, "Bundle version and approved bundle."),
    # Manual
    ReasonCode("manual_review_required", "safety", "The case can't be decided safely within the supported policy and evidence.",
               "payments_operations", "medium", ("HANDOFF",), False, "The unresolved condition."),
]

REASON_CODES: dict[str, ReasonCode] = {item.code: item for item in _CODES}


def describe(code: str) -> ReasonCode:
    return REASON_CODES[code]


def as_table() -> list[dict[str, object]]:
    return [
        {**item._asdict(), "outcomes": list(item.outcomes)} for item in _CODES
    ]


def markdown() -> str:
    """docs/REASON_CODES.md, generated from the table above."""
    lines = [
        "# APB Guard reason codes",
        "",
        "Generated from `apb/guard/reason_codes.py`. Rerun "
        "`python -m apb.guard.docs` after changing the table.",
        "",
        "| Code | Gate | Meaning | Owner | Severity | Outcomes | Override | Evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in _CODES:
        lines.append(
            f"| `{item.code}` | {item.gate} | {item.explanation} | {item.owner} | "
            f"{item.severity} | {', '.join(item.outcomes)} | "
            f"{'yes' if item.override_allowed else 'no'} | {item.evidence} |"
        )
    lines += [
        "",
        "An override is a recorded human decision through the approval workflow, "
        "never a chat reply. Codes marked `no` can't be released at all.",
        "",
    ]
    return "\n".join(lines)
