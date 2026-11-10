# Changelog

## APB v3.1 · method apb-0.3-draft · world-0.2.0 · Decision Receipt v3 (unreleased)

The product is repositioned from route-quality-first to **defensive co-scaling for agentic payments**: payment-agent assurance and control infrastructure. Route quality is kept as the downstream economic-quality stage.

### Added
- `apb/guard/`: version matrix, Authority Levels 0–5, a 35-code reason-code taxonomy, typed schemas, an explicit staged decision pipeline, execution adapters with idempotent `commit()`, and Decision Receipt v3 with a content hash.
- APB World `world-0.2.0`: 17 synthetic episodes (default `E-AUTH-004`) with server-side answer keys and relaxing variants; a deterministic benchmark with defined metrics, Wilson interval and pass^k (`python -m apb.guard.world`).
- `/api/v3` (episodes, runs, authority, reason codes, World results, illustrative monitoring, anonymous events) and a new interactive APB Guard demo with STOP, PAUSE, PAY, HANDOFF, neutrality, replay and version-drift scenarios.
- JSON Schemas in `schemas/`; generated `docs/REASON_CODES.md`; `results/world/reference.json` and `sample-receipts.json`.
- The APBv2 product demo (asset `apb_v2_product_demo`) with captions, poster and transcript, served from paymentsbenchmark.com.
- paymentsbenchmark.com source in `paymentsbenchmark/`, rebuilt around APB World, the authority model, security, evidence and enterprise pages.
- ARCHITECTURE, SECURITY, THREAT_MODEL, CONTRIBUTING, CODE_OF_CONDUCT, CI workflow and GitHub templates.
- Security headers (CSP without inline script, frame-ancestors, nosniff, referrer and permissions policies, HSTS on the public demo); rate limiting extended to `/api/v3` POSTs.

### Changed
- v1 `DecisionReceipt.decision` value `HAND_OFF` is now `HANDOFF`, matching the v3.1 terminology. Receipts also carry `policy_version`.
- The v0.1 README and method moved to `ECONOMIC_QUALITY_V0_1.md` and `docs/METHOD_V0_1.md`; `docs/METHOD.md` is now the apb-0.3-draft method.
- `paypal.create_payout` delegates to a reusable `submit_payout`; v0.1 behaviour is unchanged.
- The product name on primary surfaces is APB Guard; "APB Value Guard" remains only in historical v0.1 material.

### Removed from primary surfaces
- Remittance-only positioning, the `$200` Kenya / M-PESA example as the headline scenario, the old demo-video placeholder, and "best route" wording without the eligibility qualification.

### Known limitations
- The `llm_agent_guarded` observed run (real model through `/v1/check` and `/v1/recheck`) has a harness and tests but no completed run yet.
- `apb_guard`'s 100% World score is a consistency check against answer keys written for it, not independent validation.

## apb-0.1 (2026-11-06 – 2026-11-08)

Route-quality benchmark: eligibility gate, Delivered Value Score, nine tests in three synthetic corridors, PayPal sandbox execution, Check API and MCP tool, observed `llm_agent` runs. See [ECONOMIC_QUALITY_V0_1.md](ECONOMIC_QUALITY_V0_1.md).
