"""Data for /simulator, generated at build time by running APB Guard.

Every outcome the page shows comes from apb.guard: the connect checks and the
binding come from apb.guard.connect, and every gate result, effect and reason
code comes from apb.guard.pipeline.decide() on a synthetic APB World episode.
The page only displays these results. The one thing it computes itself is the
labelled what-if of switching payment gates off.

Episodes the page uses:
- connectors: E-CONN-003 (clean), E-CONN-001 (lookalike domain),
  E-CONN-002 (over-broad scope), E-CONN-004 (manually added), each at every
  requested level 0-5 (the slider). At other levels than the fixture's, the
  connector requests its declared scopes up to that level.
- payments: the prototype's six episodes plus E-BIND-001 and E-BIND-002, each
  run under every binding the connect step can create.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from apb.guard.authority import ACTION_LEVEL, LEVELS, OPERATING_RULE  # noqa: E402
from apb.guard.connect import connect_checks  # noqa: E402
from apb.guard.episodes import load  # noqa: E402
from apb.guard.pipeline import GATE_LABELS, PRE_GATES, ROUTE_GATES, SECURITY_GATES, decide  # noqa: E402
from apb.guard.reason_codes import REASON_CODES  # noqa: E402
from apb.guard.schemas import EpisodeDefinition  # noqa: E402

import observed  # noqa: E402

CONNECTORS = ["E-CONN-003", "E-CONN-001", "E-CONN-002", "E-CONN-004"]
DEFAULT_CONNECTOR = "E-CONN-003"
SCENARIOS = ["E-AUTH-004", "E-AMT-001", "E-BEN-002", "E-AUTH-002", "E-EXEC-001", "E-AUTH-004-R",
             "E-BIND-001", "E-BIND-002"]
PAY_GATES = SECURITY_GATES + ROUTE_GATES


def connection_at(episode_id: str, level: int) -> dict:
    """The connector from `episode_id`, requesting `level`: its declared scopes up to that level."""
    connection = load(episode_id).connection.model_dump(mode="json")
    connection["requested_level"] = level
    connection["requested_scopes"] = [s for s in connection["requested_scopes"]
                                      if ACTION_LEVEL.get(s, 99) <= level]
    return connection


def with_connection(episode_id: str, connection: dict) -> EpisodeDefinition:
    """`episode_id` run under this connection. Episodes without their own grant
    take the clean connection's grant (limit, payees, expiry)."""
    data = load(episode_id).model_dump(mode="json")
    data["connection"] = connection
    if data.get("grant") is None:
        data["grant"] = load(DEFAULT_CONNECTOR).grant.model_dump(mode="json")
    return EpisodeDefinition.model_validate(data)


def _gate(result) -> list:
    return [result.status, result.effect, list(result.reason_codes)]


def connector_entry(episode_id: str) -> dict:
    fixture = load(episode_id)
    c = fixture.connection
    by_level = {}
    for level in range(6):
        connection = connection_at(episode_id, level)
        episode = with_connection(episode_id, connection)
        decision = decide(episode)
        pre = {g.gate: g for g in decision.pre_gates}
        binding = decision.trace.binding
        by_level[str(level)] = {
            "fixture": level == c.requested_level,
            "action": decision.outcome.action,
            "codes": decision.outcome.reason_codes,
            "scopes": connection["requested_scopes"],
            "checks": [[k.check, k.label, k.effect, k.code, k.detail]
                       for k in connect_checks(episode.connection)],
            "connect": _gate(pre["connect"]) + [pre["connect"].explanation],
            "bind": _gate(pre["bind"]) + [pre["bind"].explanation],
            "binding": None if binding is None else {
                "id": binding.binding_id, "hash": binding.hash, "user": binding.user_id,
                "agent": binding.agent_id, "vendor": binding.vendor, "level": binding.authority_level,
                "limit": binding.spending_limit.label(), "actions": binding.allowed_actions,
                "payees": binding.allowed_payees, "created": binding.created_at.isoformat(),
                "expires": binding.expires_at.isoformat(),
            },
        }
    return {
        "title": fixture.title, "summary": fixture.summary, "name": c.name, "vendor": c.vendor,
        "declared_domain": c.declared_domain, "server_domain": c.server_domain,
        "directory_status": c.directory_status, "bank_domain": c.bank_domain,
        "platform_domain": c.agent_platform_domain, "login_domain": c.oauth_login_domain,
        "redirect_domain": c.oauth_redirect_domain, "purpose": c.stated_purpose,
        "fixture_level": c.requested_level, "by_level": by_level,
    }


def scenario_entry(episode_id: str, connectors: dict) -> dict:
    fixture = load(episode_id)
    runs = {}
    for cid in CONNECTORS:
        for level, entry in connectors[cid]["by_level"].items():
            if entry["binding"] is None:
                continue
            decision = decide(with_connection(episode_id, connection_at(cid, int(level))))
            binding = decision.trace.binding
            runs[f"{cid}@{level}"] = {
                "action": decision.outcome.action,
                "codes": decision.outcome.reason_codes,
                "security_outcome": decision.outcome.security_outcome,
                "binding": [binding.binding_id, binding.authority_level, binding.expires_at.isoformat(),
                            binding.allowed_payees],
                "payee": decision.trace.episode.mandate.beneficiary_id,
                "evaluated_at": decision.trace.episode.evaluated_at.isoformat(),
                "gates": [_gate(g) for g in decision.gates],
            }
    return {"title": fixture.title, "summary": fixture.summary,
            "fixture_action": fixture.answer_key.action, "runs": runs}


def page_data() -> dict:
    connectors = {cid: connector_entry(cid) for cid in CONNECTORS}
    scenarios = {sid: scenario_entry(sid, connectors) for sid in SCENARIOS}
    used = {code for c in connectors.values() for e in c["by_level"].values()
            for code in e["codes"] + [k[3] for k in e["checks"] if k[3]] + e["connect"][2] + e["bind"][2]}
    used |= {code for s in scenarios.values() for r in s["runs"].values()
             for code in r["codes"] + [c for g in r["gates"] for c in g[2]]}
    return {
        "data_mode": "synthetic",
        "rule": OPERATING_RULE,
        "levels": [[item.name, item.capability] for item in LEVELS],
        "pre_gates": [[g, GATE_LABELS[g]] for g in PRE_GATES],
        "gates": [[g, GATE_LABELS[g]] for g in PAY_GATES],
        "default_connector": DEFAULT_CONNECTOR,
        "connector_order": CONNECTORS,
        "observed": observed.follow_up("payee_no_match"),
        "connectors": connectors,
        "scenarios": scenarios,
        "codes": {code: [REASON_CODES[code].explanation, REASON_CODES[code].owner]
                  for code in sorted(used)},
    }


def page_json() -> str:
    """Compact JSON, safe to embed inside a <script> element."""
    text = json.dumps(page_data(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.replace("</", "<\\/")


if __name__ == "__main__":
    print(json.dumps(page_data(), ensure_ascii=False, indent=1)[:4000])
