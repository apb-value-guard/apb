"""Typed schemas for APB Guard v3. Runtime-validated with Pydantic.

Every model forbids unknown fields. Money is a Decimal that serializes as a
string. JSON Schemas in schemas/ are generated from these models.
"""

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer

from apb.guard.versions import METHOD_VERSION, POLICY_BUNDLE, RECEIPT_VERSION, WORLD_VERSION

Decimal_ = Annotated[
    Decimal, PlainSerializer(lambda v: format(v.normalize(), "f"), return_type=str, when_used="json")
]
Action = Literal["PAY", "PAUSE", "STOP", "HANDOFF"]
DataMode = Literal["synthetic", "sandbox", "live"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Money(_Model):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    value: Annotated[Decimal_, Field(ge=0)]

    def label(self) -> str:
        symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(self.currency)
        number = f"{self.value:,.2f}".removesuffix(".00")
        return f"{symbol}{number}" if symbol else f"{self.currency} {number}"


class AccessToken(_Model):
    """Token metadata only. The token itself never enters APB's receipts or UI."""

    token_ref: str
    audience: str
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    sender_constrained: bool = True


class AgentIdentity(_Model):
    agent_id: str | None
    workload_id: str | None = None
    model: str
    model_version: str
    prompt_version: str
    toolset_version: str
    token: AccessToken | None = None


class Principal(_Model):
    principal_id: str
    kind: Literal["corporate_user", "business", "consumer"]
    organization: str


class Delegation(_Model):
    delegation_id: str
    principal_id: str
    agent_id: str
    allowed_actions: list[str]
    execution_authority: bool
    max_amount: Money
    allowed_beneficiaries: list[str]
    allowed_countries: list[str]
    expires_at: AwareDatetime
    policy_bundle: str


class AuthorityProfile(_Model):
    agent_id: str
    delegation_id: str | None
    requested_action: str
    requested_level: int
    approved_level: int
    authority_gap: bool
    missing_controls: list[str]
    evidence_required: list[str]
    can_increase: bool
    increase_path: str
    expires_at: AwareDatetime | None
    reauthorize_by: AwareDatetime | None


class PaymentInstruction(_Model):
    """The authenticated mandate: what the principal actually asked for."""

    mandate_id: str
    request_text: str
    amount: Money | None
    beneficiary_id: str | None
    beneficiary_country: str | None
    purpose: str | None
    payment_method: str | None
    deadline_minutes: int | None
    objective: Literal["maximize_recipient_value", "minimize_cost", "fastest"] = "maximize_recipient_value"
    requested_action: str = "payment.execute"


class ExtractedFacts(_Model):
    beneficiary_name: str | None = None
    account_ref: str | None = None
    account_masked: str | None = None
    amount: Money | None = None
    contains_instructions: bool = False


class InstructionSource(_Model):
    source_id: str
    kind: Literal["signed_policy", "authenticated_mandate", "approved_workflow", "external_content"]
    priority: Annotated[int, Field(ge=1, le=4)]
    label: str
    content_hash: str
    excerpt: str | None = None
    hidden_excerpt: str | None = None
    extracted: ExtractedFacts = ExtractedFacts()


class ProvenanceRecord(_Model):
    fact: str
    source_id: str
    trust: Literal["authoritative", "trusted", "untrusted"]
    value_ref: str


class BeneficiaryState(_Model):
    beneficiary_id: str
    name: str
    account_ref: str
    account_masked: str
    country: str
    verified: bool
    first_time: bool
    name_check: Literal["match", "close_match", "no_match"] | None = None
    returned_name: str | None = None
    master_version: str


class CandidateRoute(_Model):
    route_id: str
    provider: str
    label: str
    payment_methods: list[str]
    delivery_minutes: int
    sponsored: bool = False
    healthy: bool = True
    approved: bool = True


class Quote(_Model):
    quote_id: str
    route_id: str
    fee: Money
    recipient_amount: Money
    quoted_at: AwareDatetime
    valid_for_seconds: int


class RiskSignal(_Model):
    kind: Literal["screening_hold", "velocity", "budget", "new_payee_spike", "provider_health"]
    status: Literal["clear", "hold", "exceeded", "exhausted", "degraded"]
    source: Literal["simulated", "caller_supplied"]
    reference: str


class Approval(_Model):
    approval_id: str
    status: Literal["captured", "missing", "expired", "rejected"]
    approvers: list[str] = []
    bound_fields: list[str] = []
    bound_mandate_hash: str | None = None
    bound_beneficiary_id: str | None = None
    bound_amount: Money | None = None
    captured_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None


class PolicyDecision(_Model):
    decision_id: str
    effect: Literal["ALLOW", "DENY", "STEP_UP", "HANDOFF"]
    reason: str | None
    bundle_version: str
    required_approvers: int | None = None
    bound_fields: list[str] = []
    expires_in_seconds: int | None = None


GateName = Literal[
    "identity", "authority", "mandate", "instruction", "beneficiary", "spend",
    "approval", "route", "quote", "execution",
]
GateStatus = Literal["pass", "fail", "warn", "missing", "not_reached"]


class GateResult(_Model):
    order: int
    gate: GateName
    label: str
    status: GateStatus
    effect: Literal["ALLOW", "DENY", "STEP_UP", "HANDOFF"] | None
    reason_codes: list[str]
    evidence_ref: str
    policy_version: str
    explanation: str


class ExecutionRecheck(_Model):
    performed: bool
    at: AwareDatetime | None
    route_id: str | None
    quote_rechecked: bool
    quote_status: Literal["fresh", "expired", "drifted", "not_reached"]
    approval_valid: bool | None
    idempotency_key: str | None
    duplicate_check: Literal["passed", "duplicate", "conflict", "not_reached"]


class ExcludedRoute(_Model):
    route_id: str
    reason: str


class RouteScore(_Model):
    route_id: str
    provider: str
    recipient_amount: Money
    fee: Money
    delivery_minutes: int
    dvs: float


class DecisionOutcome(_Model):
    action: Action
    reason_codes: list[str]
    selected_route: str | None
    selected_provider: str | None
    paypal_called: bool
    security_outcome: str
    explanation: str


class ExecutionReference(_Model):
    adapter: Literal["paypal_sandbox"]
    mode: Literal["sandbox", "simulated_no_credentials"]
    reference: str
    status: str
    idempotency_key: str
    duplicate: bool = False


class ReceiptAgent(_Model):
    id: str | None
    model: str
    model_version: str
    prompt_version: str
    toolset_version: str
    config_hash: str


class ReceiptAuthority(_Model):
    delegation_id: str | None
    principal_id: str | None
    requested_level: int
    approved_level: int
    execute: bool
    max_amount: str | None
    step_up_granted: bool


class ReceiptInstructionIntegrity(_Model):
    external_content_untrusted: bool
    prompt_injection: bool
    beneficiary_mutation: bool
    amount_mutation: bool
    sources: list[dict[str, Any]]


class ReceiptPolicy(_Model):
    engine: str
    bundle_version: str
    decision_id: str
    effect: str


class ReceiptApproval(_Model):
    required: bool
    status: Literal["captured", "missing", "expired", "rejected", "not_required"]
    approval_id: str | None
    bound_mandate_hash: str | None


class ReceiptExecutionIntegrity(_Model):
    idempotency_key: str | None
    quote_rechecked: bool
    duplicate_check: str


class DecisionReceiptV3(_Model):
    """Observable evidence for one decision. No credentials, no chain-of-thought."""

    receipt_version: Literal["v3"] = RECEIPT_VERSION
    receipt_id: str
    world_version: Literal["world-0.2.0"] = WORLD_VERSION
    episode_id: str
    method_version: Literal["apb-0.3-draft"] = METHOD_VERSION
    evaluated_at: AwareDatetime
    mandate_hash: str
    agent: ReceiptAgent
    authority: ReceiptAuthority
    instruction_integrity: ReceiptInstructionIntegrity
    policy: ReceiptPolicy
    approval: ReceiptApproval
    execution_integrity: ReceiptExecutionIntegrity
    gates: list[GateResult]
    candidate_set: list[str]
    excluded_routes: list[ExcludedRoute]
    route_scores: list[RouteScore]
    selected_route: str | None
    selected_action: Action
    security_outcome: str
    reason_codes: list[str]
    paypal_called: bool
    trace_manifest_hash: str
    data_mode: DataMode
    execution_reference: ExecutionReference | None
    receipt_hash: str


class Commit(_Model):
    """What changes between decision and commit."""

    at: AwareDatetime
    quotes: list[Quote] | None = None
    drift_tolerance: Annotated[Decimal_, Field(ge=0)] = Decimal("0.005")


class ApprovedInventory(_Model):
    models: list[str]
    prompts: list[str]
    policy_bundles: list[str] = [POLICY_BUNDLE]


class AnswerKey(_Model):
    """Server-side only. Never sent to a browser before the episode is run."""

    action: Action
    reason_codes: list[str]
    paypal_called: bool
    security_outcome: str


class EpisodeDefinition(_Model):
    episode_id: str
    title: str
    family: str
    summary: str
    data_mode: Literal["synthetic"] = "synthetic"
    world_version: Literal["world-0.2.0"] = WORLD_VERSION
    variant_of: str | None = None
    relaxes: str | None = None
    evaluated_at: AwareDatetime
    identity: AgentIdentity
    principal: Principal
    delegation: Delegation | None
    mandate: PaymentInstruction
    sources: list[InstructionSource]
    beneficiary: BeneficiaryState | None
    approval: Approval | None = None
    risk_signals: list[RiskSignal] = []
    routes: list[CandidateRoute]
    quotes: list[Quote]
    commit: Commit
    inventory: ApprovedInventory
    replay_attempts: Annotated[int, Field(ge=1, le=3)] = 1
    answer_key: AnswerKey


class BenchmarkRun(_Model):
    method_version: str
    world_version: str
    policy_bundle: str
    data_mode: Literal["synthetic"]
    agent: str
    runs_per_episode: int
    episodes: list[dict[str, Any]]
    metrics: dict[str, Any]


class CoScalingAssessment(_Model):
    agent_id: str
    requested_level: int
    approved_level: int
    controls_present: list[str]
    controls_missing: list[str]
    decision: Literal["grant", "grant_with_step_up", "deny", "reduce"]
    review_by: AwareDatetime | None
