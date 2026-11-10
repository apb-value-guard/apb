# Threat model

Scope: APB Guard (the pipeline, API and demo), APB World fixtures, and paymentsbenchmark.com. The agent under test is treated as an untrusted principal.

## Protected assets

| Asset | Why it matters |
|---|---|
| The decision to move money | An unauthorized PAY is the loss APB exists to prevent |
| Delegation and authority state | Over-broad authority turns any model error into a payment |
| Beneficiary and amount facts | Mutation redirects funds |
| PayPal sandbox credentials | Server-side only; their exposure would let anyone drive the sandbox account |
| Idempotency state | Replay protection against duplicate execution |
| Answer keys and hidden facts | Leaking them lets an agent be tuned to the test |
| Decision Receipts | Evidence integrity; tampering hides what happened |

## Trust boundaries

1. Agent → APB Guard (requests are untrusted).
2. External content → mandate (priority 4 data never overrides priorities 1–3).
3. APB Guard → execution adapter (only `commit()` on PAY).
4. Server → browser (public views only until a run completes).
5. Benchmark site → visitors (static; no server-side processing of submissions).

## Threat actors

Malicious invoice or email authors, a compromised or misaligned agent, an over-privileged integration, an insider changing models or policies without review, and an internet attacker probing the public demo.

## Threats and controls

| Threat | Attack surface | Control | Residual risk |
|---|---|---|---|
| Indirect prompt injection with fact mutation | Invoice, email, web content | Instruction hierarchy; deterministic comparison with master data; STOP on mutation (`E-AUTH-004`, `E-AMT-001`) | Mutations of fields APB doesn't compare (for example purpose text) |
| Prompt injection without fact change | Same | Quarantined and recorded (`E-INJ-001`); never relied on alone | Classifier-only detection is not used for any decision |
| Excessive authority | Tool scopes, delegation | Action-to-level mapping; `execution_not_delegated`, `action_outside_scope` | Depends on the delegation being configured correctly |
| Delegation abuse | Missing or expired delegation | HANDOFF or PAUSE (`E-AUTH-003`); expiry checked | No live identity provider in the public demo |
| Identity and token abuse | Agent token | Audience, expiry and agent match; STOP (`E-AUTH-001`) | Sender-constrained tokens are recorded, not cryptographically verified, today |
| Approval forgery or reuse | Approval object | Bound to mandate hash, beneficiary, amount, currency, approver count, expiry; rechecked at commit | Approvals are fixture data, not signed |
| Beneficiary mutation | Master data, payee checks | Verified master record; close match PAUSE, no match STOP | Master data itself is assumed correct |
| Replay and duplicate execution | Repeated PAY | Idempotency key = payout batch ID; original result returned (`E-EXEC-002`); conflicts STOP | In-memory store resets on restart; PayPal's own batch-ID uniqueness is the backstop |
| Stale quote or drift | Quote at commit | Recheck with 0.5% tolerance; PAUSE (`E-EXEC-001`) | Fixture quotes are synthetic |
| Model, prompt or policy drift | Agent configuration | Approved inventory; PAUSE or STOP (`E-CHG-001`, `E-CHG-002`) | Inventory is fixture data |
| Credential and payment-data exposure | Logs, receipts, browser | Credentials only in the server environment; receipts hold masked accounts and references; errors carry short codes only | Operator error in deployment configuration |
| Answer-key leakage | HTML, scripts, API | Public views strip answer keys and hidden payloads; a test checks the page, scripts and API | Fixtures are public in this repository by design |
| Provider or dependency compromise | PayPal API, Python packages | Sandbox-only URL allowlist, no redirects, pinned requirements, dependency audit in CI | Supply-chain risk is reduced, not removed |
| Clickjacking and injection on the demo | Browser | CSP without inline script, `frame-ancestors 'none'`, text-node rendering only, `nosniff` | — |
| Denial of service and circuit-breaker abuse | Public endpoints | Rate limit 60 per minute per address, 64 KB body limit; illustrative circuit breakers documented | The free hosting tier can still be saturated |

## Explicit non-goals

- APB does not screen people or entities, and does not replace KYC, AML, sanctions, fraud or SCA systems.
- APB does not hold or move funds.
- The public demo is not a production deployment and makes no availability promise.
- Synthetic fixtures do not prove production safety.
