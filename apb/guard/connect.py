"""Connect and bind: the stage that runs before the payment gates.

connect: is this connector what it claims to be, and does it ask only for what
its purpose and the requested authority level need?
bind: create the delegation record (user, agent, vendor, level, limit, allowed
actions, allowed payees, expiry, hash) that the payment gates check later.

Platform-neutral: the inputs are domains, a directory status and scopes, not
any one vendor's API. Deterministic code only.
"""

from typing import NamedTuple

from apb.guard.authority import ACTION_LEVEL, approved_level, level_for_action
from apb.guard.schemas import BindingGrant, BindingRecord, ConnectorRequest, Delegation
from apb.guard.versions import POLICY_BUNDLE

# The highest authority level each stated purpose can justify.
PURPOSE_MAX_LEVEL = {
    "account_information": 0,
    "budgeting": 1,
    "invoice_preparation": 2,
    "payment_initiation": 3,
    "supplier_payments": 4,
    "treasury_orchestration": 5,
}
# A manually added connector lowers trust: never auto-accepted above Level 2.
MANUAL_MAX_AUTO_LEVEL = 2


def domain_matches(domain: str, expected: str) -> bool:
    """Exactly `expected`, or a subdomain of it. "pay-acme.io" is not "acme.io"."""
    domain, expected = domain.lower().rstrip("."), expected.lower().rstrip(".")
    return domain == expected or domain.endswith("." + expected)


def scope_limit(connection: ConnectorRequest) -> int:
    """The highest level any requested scope may need: the lower of what the
    stated purpose justifies and the level requested for this connector."""
    return min(PURPOSE_MAX_LEVEL.get(connection.stated_purpose, -1), connection.requested_level)


class ConnectCheck(NamedTuple):
    """One connect check. `effect` is ALLOW, WARN, STEP_UP or DENY."""

    check: str
    label: str
    effect: str
    code: str | None
    detail: str


def connect_checks(connection: ConnectorRequest) -> list[ConnectCheck]:
    """The four connect checks, each with its own result, in a fixed order."""
    checks = []
    if connection.directory_status == "manual":
        if connection.requested_level > MANUAL_MAX_AUTO_LEVEL:
            checks.append(ConnectCheck(
                "directory", "Directory status", "STEP_UP", "connector_unreviewed",
                f"Added manually and asks for Level {connection.requested_level}; manual connectors are never "
                f"accepted automatically above Level {MANUAL_MAX_AUTO_LEVEL}."))
        else:
            checks.append(ConnectCheck(
                "directory", "Directory status", "WARN", None,
                f"Added manually: accepted at Level {connection.requested_level}, at most Level "
                f"{MANUAL_MAX_AUTO_LEVEL} without review."))
    elif connection.directory_status == "listed":
        checks.append(ConnectCheck("directory", "Directory status", "WARN", None,
                                   "Listed in the directory but not reviewed."))
    else:
        checks.append(ConnectCheck("directory", "Directory status", "ALLOW", None, "Reviewed directory entry."))

    if domain_matches(connection.server_domain, connection.declared_domain):
        checks.append(ConnectCheck("domain", "Server domain", "ALLOW", None,
                                   f"{connection.server_domain} is on the declared domain {connection.declared_domain}."))
    else:
        checks.append(ConnectCheck(
            "domain", "Server domain", "DENY", "connector_domain_mismatch",
            f"The server {connection.server_domain} is not on {connection.vendor}'s declared domain "
            f"{connection.declared_domain}."))

    login_ok = domain_matches(connection.oauth_login_domain, connection.bank_domain)
    redirect_ok = domain_matches(connection.oauth_redirect_domain, connection.agent_platform_domain)
    if login_ok and redirect_ok:
        checks.append(ConnectCheck(
            "oauth", "OAuth login and redirect", "ALLOW", None,
            f"Login on {connection.oauth_login_domain} (the bank), redirect to {connection.oauth_redirect_domain} "
            f"(the declared platform)."))
    else:
        where = []
        if not login_ok:
            where.append(f"the login runs on {connection.oauth_login_domain}, not the bank's {connection.bank_domain}")
        if not redirect_ok:
            where.append(f"it redirects to {connection.oauth_redirect_domain}, not the declared platform "
                         f"{connection.agent_platform_domain}")
        checks.append(ConnectCheck("oauth", "OAuth login and redirect", "DENY", "oauth_redirect_mismatch",
                                   "OAuth: " + " and ".join(where) + "."))

    limit = scope_limit(connection)
    over = [s for s in connection.requested_scopes if level_for_action(s) > limit]
    if over:
        checks.append(ConnectCheck(
            "scopes", "Scopes against purpose and level", "STEP_UP", "scope_exceeds_purpose",
            f"Purpose '{connection.stated_purpose}' at Level {connection.requested_level} allows scopes up to "
            f"Level {max(limit, 0)}; requested {', '.join(over)}."))
    else:
        checks.append(ConnectCheck(
            "scopes", "Scopes against purpose and level", "ALLOW", None,
            f"Every requested scope fits purpose '{connection.stated_purpose}' at Level {connection.requested_level}."))
    return checks


def check_connect(gate, connection: ConnectorRequest) -> None:
    """Record the connect checks on `gate` (a pipeline Gate): failures first by
    strength, so the strongest reason leads."""
    checks = connect_checks(connection)
    rank = {"DENY": 0, "STEP_UP": 1, "WARN": 2, "ALLOW": 3}
    for item in sorted(checks, key=lambda c: rank[c.effect]):
        if item.effect == "DENY":
            gate.deny(item.code, item.detail)
        elif item.effect == "STEP_UP":
            gate.step_up(item.code, item.detail)
        elif item.effect == "WARN":
            gate.warn(None, item.detail)
    if gate.status == "pass":
        gate.explanation = (f"{connection.name} is reviewed, served from {connection.declared_domain}, logs in on the "
                            f"bank's domain, redirects to the declared platform and asks only for in-purpose scopes.")


def binding_level(connection: ConnectorRequest) -> int:
    return approved_level(connection.requested_scopes, connection.requested_level >= 4)


def create_binding(connection: ConnectorRequest, grant: BindingGrant) -> BindingRecord:
    """The delegation record, content-addressed: its id and hash come from its fields."""
    from apb.guard.pipeline import canonical_hash

    level = binding_level(connection)
    actions = sorted((s for s in connection.requested_scopes if ACTION_LEVEL.get(s, 99) <= level),
                     key=lambda s: (ACTION_LEVEL[s], s))
    fields = {
        "user_id": grant.user_id, "agent_id": connection.agent_id, "vendor": connection.vendor,
        "connector_id": connection.connector_id, "authority_level": level,
        "spending_limit": grant.spending_limit, "allowed_actions": actions,
        "allowed_payees": sorted(grant.allowed_payees), "created_at": grant.granted_at,
        "expires_at": grant.expires_at,
    }
    digest = canonical_hash({k: (v.model_dump(mode="json") if hasattr(v, "model_dump") else v)
                             for k, v in fields.items()})
    return BindingRecord(binding_id="bnd-" + digest[7:19], hash=digest, **fields)


def delegation_from_binding(binding: BindingRecord) -> Delegation:
    """The binding, in the shape the payment gates already check.

    Countries are not restricted here: the payee allow-list is narrower, and the
    beneficiary gate checks it against the binding itself.
    """
    return Delegation(
        delegation_id=binding.binding_id, principal_id=binding.user_id, agent_id=binding.agent_id,
        allowed_actions=binding.allowed_actions, execution_authority=binding.authority_level >= 4,
        max_amount=binding.spending_limit, allowed_beneficiaries=binding.allowed_payees,
        allowed_countries=[], expires_at=binding.expires_at, policy_bundle=POLICY_BUNDLE,
    )
