# Roadmap

Gated by evidence, not dates. Status labels match [CONTROL_EVIDENCE.md](CONTROL_EVIDENCE.md).

## Now (APB v3.1 · method apb-0.3-draft · world-0.2.0)

Implemented on synthetic fixtures: Authority Levels 0–5, the staged APB Guard pipeline, 35 reason codes, Decision Receipt v3, 17 episodes with relaxing variants, the deterministic World benchmark, PayPal sandbox execution behind `commit()`, and the interactive demo.

## Next 18 months

| Period | Deliverables | Exit gate |
|---|---|---|
| 0–3 months | Grow World 0.2 to 20 reviewed episodes; families C (payment policy) and F (data and credentials); secure development baseline | Two independent reviewers of answer keys; structured buyer interviews |
| 4–6 months | Customer simulator, fact-release engine, agent adapters (including the `llm_agent_guarded` observed run), FAPI-compatible identity profile, private deployment package | One paid security and decision audit |
| 7–9 months | Shadow APB Guard, policy console, approval-service integration, telemetry and circuit breakers, evidence exports | Measured latency, false positives and unsafe-decision detections |
| 10–12 months | Private APB World, two payment adapters, incident and reconciliation workflow, penetration test | One recurring private deployment or runtime extension |
| 13–18 months | Production pilot, tenant controls, enterprise support, conformance profile, certification readiness | Repeatable, evidenced outcomes |

## Later

- **Acquiring track.** The same gates-then-value method for card payment orchestration, scored on expected net value: authorisation rate × (amount − acquirer fee − cross-border fee − FX cost) − per-attempt fees. Runs on a merchant's own data under NDA.
- v0.1 economic-quality additions: structuring (`structuring_detected`) and scam signals (`fraud_review`).

## Stop and continue criteria

- No broad certification before answer-key governance and appeals exist.
- No new provider adapters before a customer funds them.
- No production enforcement before shadow evidence and rollback procedures exist.
- No drift into generic AI security; outcomes stay payment-specific.
