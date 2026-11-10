# Control evidence, not compliance theatre

APB supplies technical controls and evidence that can support an organization's assurance environment. **APB does not make a customer SOC 2, ISO, PCI DSS, DORA, PSD2, GDPR, NIS2 or EU AI Act compliant, and APB holds no certification.**

## Status of each control

| Control | Status | Evidence in this repository |
|---|---|---|
| Deterministic policy gates in a fixed order, with reason codes | Implemented and evidenced now | `apb/guard/pipeline.py`, `tests/test_guard.py` |
| Authority Levels 0–5, requested versus approved level, missing controls | Implemented and evidenced now | `apb/guard/authority.py` |
| Instruction hierarchy; mutation detection against master data | Implemented and evidenced now | `E-AUTH-004`, `E-AMT-001`, `E-INJ-001` |
| Transaction-bound approval binding | Implemented and evidenced now | `approval_valid()`; tamper tests |
| Commit-time recheck: quote, approval, idempotency | Implemented and evidenced now | `E-EXEC-001`, `E-EXEC-002` |
| Idempotent sandbox execution; replay returns the original reference | Implemented and evidenced now | `commit()`, `tests/test_guard_api.py` |
| Decision Receipt v3 with content hash and JSON Schema | Implemented and evidenced now | `schemas/`, `verify_receipt()` |
| Security headers, rate limits and body limits on the public demo | Implemented and evidenced now | `apb/web.py`, `tests/test_web.py` |
| Sender-constrained token metadata in the identity context | Integration-ready or supported by design | Schema fields only; no identity-provider integration |
| Policy engines such as Open Policy Agent or Cedar | Integration-ready or supported by design | One decision interface; no adapter built |
| SCA and step-up approval services | Integration-ready or supported by design | Approval results consumed; no service integrated |
| Signed receipt envelopes, trusted timestamps, append-only storage | Roadmap | — |
| Shadow-mode comparison against live payment decisions | Roadmap | — |
| Policy console, incident linkage, reconciliation workflow | Roadmap | — |
| External penetration test; SOC 2 Type I or ISO/IEC 27001 readiness | Roadmap | — |

## Framework relevance (mapping hypotheses)

**Security and AI governance:** SOC 2 Security and Processing Integrity; ISO/IEC 27001, 27017, 27018 and 27701; ISO/IEC 42001, 23894 and 42005; NIST AI RMF and NIST SP 800-207; OWASP agentic-security guidance.

**Payments and resilience:** OpenID FAPI 2.0; PCI DSS v4.0.1; DORA and NIS2; PSD2 and SCA; GDPR; the EU AI Act where applicable.

For each, APB's controls and receipts may serve as inputs to an organization's own control environment. The mapping is a hypothesis that needs institutional scoping and independent review. SOC 2 is an attestation, not a certification. Tokenization alone doesn't remove PCI scope. The regulated payment service provider stays responsible for SCA. EU AI Act classification depends on intended purpose and deployment.

Applicability depends on deployment, role, scope, jurisdiction, implementation, operating process, and independent review.

## Claims we don't make

| Claim | Status |
|---|---|
| APB prevents unauthorized payments | Design objective demonstrated on synthetic episodes only |
| APB is SOC 2, ISO or PCI compliant or certified | Not made |
| APB reduces fraud | Not made without outcome-linked pilot evidence |
| APB benchmark scores prove production safety | Not made |
| PayPal endorses or partners with APB | Not made; PayPal is a sandbox execution adapter |
| APB is an industry standard | Not made |
