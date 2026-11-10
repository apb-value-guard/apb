"""Authority Levels 0-5: defensive co-scaling.

Never grant Level 4 payment authority with Level 1 controls. Each level names
the capability, the exposure it creates, the minimum control profile, the
evidence a receipt must carry, and the authority decision. The website, the
docs and the schema all read this table.
"""

from typing import NamedTuple

OPERATING_RULE = "Never grant Level 4 payment authority with Level 1 controls."


class AuthorityLevel(NamedTuple):
    level: int
    name: str
    capability: str
    exposure: str
    controls: tuple[str, ...]
    evidence: tuple[str, ...]
    decision: str
    actions: tuple[str, ...]


LEVELS: tuple[AuthorityLevel, ...] = (
    AuthorityLevel(
        0, "Observe", "Read balances, invoices, quotes or payment status.",
        "No authority to create or submit a payment.",
        ("Workload identity", "Tenant isolation", "Data minimization", "Read-only scopes", "Logging"),
        ("Identity context", "Access log"),
        "Observe only.", ("payment.read",),
    ),
    AuthorityLevel(
        1, "Recommend", "Compare routes and recommend an action.",
        "Decision influence, but no payment instruction.",
        ("Mandate discovery", "Source provenance", "Route eligibility", "Grounded explanation"),
        ("Evaluation receipt", "Released facts", "Excluded routes"),
        "Recommend.", ("payment.read", "payment.quote"),
    ),
    AuthorityLevel(
        2, "Prepare", "Create a draft payment instruction.",
        "An instruction can carry a harmful beneficiary, amount or purpose change.",
        ("Delegated scope", "Beneficiary integrity", "Spend policy", "Injection defense", "No execute scope"),
        ("Draft receipt", "Source hashes", "Master-data comparison"),
        "Prepare for approval.", ("payment.read", "payment.quote", "payment.propose"),
    ),
    AuthorityLevel(
        3, "Initiate", "Submit a payment into a controlled approval workflow.",
        "Operational, fraud and authentication exposure.",
        ("Short-lived initiation scope", "Step-up approval", "SCA integration", "Idempotency", "Quote recheck"),
        ("Complete receipt", "Approval request", "Idempotency key"),
        "Initiate only.", ("payment.read", "payment.quote", "payment.propose", "payment.initiate"),
    ),
    AuthorityLevel(
        4, "Execute", "Commit funds within policy without per-transaction human intervention.",
        "Direct financial loss and regulatory exposure.",
        ("Transaction-bound authority", "Continuous authorization", "Velocity limits", "Kill switch",
         "Reconciliation", "Shadow evidence", "Circuit breakers"),
        ("Approval binding", "Commit-time recheck", "Execution reference", "Reconciliation status"),
        "Conditional autonomous execution.",
        ("payment.read", "payment.quote", "payment.propose", "payment.initiate", "payment.execute"),
    ),
    AuthorityLevel(
        5, "Orchestrate", "Coordinate several agents, providers, budgets or recurring programs.",
        "Portfolio, contagion and systemic exposure.",
        ("Hierarchical budgets", "Multi-agent isolation", "Policy precedence", "Aggregate monitoring",
         "Separation of duties", "Automated suspension", "Continuous red-team evaluation"),
        ("Portfolio evidence", "Aggregate monitoring record", "Budget hierarchy"),
        "Portfolio-bounded orchestration.",
        ("payment.read", "payment.quote", "payment.propose", "payment.initiate", "payment.execute",
         "payment.orchestrate"),
    ),
)

ACTION_LEVEL = {
    "payment.read": 0,
    "payment.quote": 1,
    "payment.propose": 2,
    "payment.initiate": 3,
    "payment.execute": 4,
    "payment.orchestrate": 5,
}


def level_for_action(action: str) -> int:
    """The level an action needs. An unknown action needs more than any level."""
    return ACTION_LEVEL.get(action, 99)


def approved_level(allowed_actions: list[str], execution_authority: bool) -> int:
    """The highest level any delegated action reaches. Without execution
    authority, no delegation reaches Level 4 or above."""
    levels = [ACTION_LEVEL[action] for action in allowed_actions if action in ACTION_LEVEL]
    level = max(levels, default=-1)
    return level if execution_authority else min(level, 3)


def missing_controls(current: int, requested: int) -> list[str]:
    """Controls added by every level above `current` up to `requested`."""
    found: list[str] = []
    for item in LEVELS:
        if current < item.level <= requested:
            found += [control for control in item.controls if control not in found]
    return found


def as_table() -> list[dict[str, object]]:
    return [
        {**item._asdict(), "controls": list(item.controls), "evidence": list(item.evidence),
         "actions": list(item.actions)}
        for item in LEVELS
    ]
