# Decision Receipt v3

Every completed decision produces one receipt. Schema: [`schemas/decision-receipt-v3.schema.json`](../schemas/decision-receipt-v3.schema.json), generated from `DecisionReceiptV3` in [`apb/guard/schemas.py`](../apb/guard/schemas.py). Examples: [`results/world/sample-receipts.json`](../results/world/sample-receipts.json).

| Field | Meaning |
|---|---|
| `receipt_version`, `world_version`, `method_version` | `v3`, `world-0.2.0`, `apb-0.3-draft` |
| `receipt_id` | `apb-rec-` + the first 12 hex digits of `receipt_hash` |
| `episode_id`, `evaluated_at`, `mandate_hash` | What was decided, when, and the hash of the authenticated mandate |
| `agent` | Agent ID, model, model/prompt/toolset versions, `config_hash` |
| `authority` | Delegation, principal, requested and approved level, execute flag, autonomous limit, `step_up_granted` |
| `instruction_integrity` | Whether external content was untrusted, injected, or mutated the beneficiary or amount; source hashes and priorities |
| `policy` | Engine, bundle version, decision ID and effect (`ALLOW`, `DENY`, `STEP_UP`, `HANDOFF`) |
| `approval` | Required, status, approval ID and bound mandate hash |
| `execution_integrity` | Idempotency key, quote recheck, duplicate check |
| `gates` | Every gate in order: status, effect, reason codes, evidence reference, policy version, explanation |
| `candidate_set`, `excluded_routes`, `route_scores` | The declared routes, exclusions with reasons, DVS for eligible routes |
| `selected_route`, `selected_action` | The route (for PAY or a route HANDOFF) and the action |
| `security_outcome`, `reason_codes` | Outcome class and the codes that drove the action |
| `paypal_called`, `execution_reference` | Whether the PayPal sandbox adapter was invoked; adapter, mode (`sandbox` or `simulated_no_credentials`), reference, status, idempotency key, duplicate flag |
| `trace_manifest_hash` | Hash of the episode, source hashes and gate results |
| `data_mode` | `synthetic` for every public episode |
| `receipt_hash` | SHA-256 over every other field, canonical JSON |

## Integrity

`verify_receipt()` recomputes `receipt_hash`; changing any field makes it fail. The same episode always produces the same receipt. Signed envelopes, trusted timestamps and append-only storage are on the roadmap ([CONTROL_EVIDENCE.md](CONTROL_EVIDENCE.md)).

## What a receipt never holds

Tokens or token references, client secrets, full account numbers (accounts appear masked to the last four digits), authentication factors, raw prompts, hidden attack text, or model chain-of-thought.
