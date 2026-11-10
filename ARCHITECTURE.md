# Architecture

APB v3.1 keeps execution outside the model. The model interprets; deterministic controls authorize; licensed rails execute.

## Components

| Component | Code | Responsibility |
|---|---|---|
| Version matrix | `apb/guard/versions.py` | One place for method, world, receipt, episode and policy identifiers |
| Authority model | `apb/guard/authority.py` | Levels 0–5, action-to-level mapping, approved level, missing controls |
| Reason codes | `apb/guard/reason_codes.py` | Stable codes with owner, severity, outcomes, override rule and evidence |
| Schemas | `apb/guard/schemas.py` | Pydantic models with `extra="forbid"`; JSON Schemas generated into `schemas/` |
| Pipeline | `apb/guard/pipeline.py` | Ordered stages, policy decision, PAY / PAUSE / STOP / HANDOFF, receipt |
| Execution | `apb/guard/execution.py` | `commit()`, idempotency store, PayPal sandbox and simulated adapters |
| APB World | `apb/guard/episodes.py`, `apb/guard/world.py` | Episode loading with variants, deterministic benchmark and metrics |
| HTTP API | `apb/guard/api.py` (mounted by `apb/web.py`) | `/api/v3` episodes, runs, authority, reason codes, World results, events |
| Economic-quality stage | `apb/decide.py`, `apb/scoring.py`, `apb/check_api.py` | v0.1 eligibility and Delivered Value Score; `/v1` Check API |
| PayPal client | `apb/paypal.py` | Sandbox-only HTTPS, no redirects, redacted errors, batch ID as idempotency key |
| Web app | `apb/web.py`, `apb/static/` | One-page APB Guard demo with strict CSP and security headers |
| Benchmark site | `paymentsbenchmark/` | Static site built from `results/world/*.json` and `docs/METHOD.md` |

## Decision pipeline

```text
resolve identity
→ resolve delegation and authority
→ normalize mandate
→ classify instruction sources and provenance
→ compare beneficiary and payment facts
→ evaluate spend, velocity and external risk signals
→ evaluate approval requirement
   (any DENY, STEP_UP or HANDOFF so far: stop here, record every gate)
→ build eligible candidate set
→ evaluate economic objective (DVS among eligible routes)
→ check quote freshness
→ commit-time recheck (quote, approval, idempotency)
→ determine PAY / PAUSE / STOP / HANDOFF
→ commit(): execute through the adapter only if PAY still holds
→ write Decision Receipt v3
```

All security gates always run, so the receipt shows every failure, not just the first. Route gates run only when every security gate allows. Each gate produces a `GateResult`: status, effect, reason codes, evidence reference, policy version and explanation.

## Trust boundaries

1. **Agent ↔ APB Guard.** The agent is an untrusted principal. It presents identity and a requested action; APB decides.
2. **External content ↔ mandate.** Invoices, emails, web pages, tool outputs and memory are data at priority 4. They can't expand authority or override signed policy, the authenticated mandate or approved workflow rules.
3. **APB Guard ↔ execution adapter.** Only `commit()` invokes an adapter, only on PAY, only after the recheck. Credentials stay in the server environment.
4. **Server ↔ browser.** The browser receives public views. Answer keys and hidden payloads are revealed only after a run; nothing secret is in HTML, scripts or preloaded data.

## Determinism

The pipeline never reads the clock: each episode carries `evaluated_at` and a commit time. Receipts are content-hashed, so the same episode always yields the same receipt ID. World results contain no timestamps; reruns are byte-identical and a test enforces it.

## Deployment shapes

| Shape | Today |
|---|---|
| Public demo (Render) | `PUBLIC_DEMO=true`: synthetic fixtures, simulated adapter, no PayPal credentials |
| Local | Optional PayPal sandbox with `ALLOW_EXECUTE=true` and sandbox credentials |
| Benchmark site | Static files behind Nginx and Cloudflare |
| Private, VPC or offline deployments | Roadmap; see [docs/ROADMAP.md](docs/ROADMAP.md) |
