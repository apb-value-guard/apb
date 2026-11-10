# Authority model: defensive co-scaling

> Never grant Level 4 payment authority with Level 1 controls.

Agent permissions must never scale faster than the controls that constrain, observe and revoke them. Authority is earned through controlled evaluation, bounded by a delegation, monitored at runtime, re-authorized on change or expiry, and immediately revocable.

The source of truth is [`apb/guard/authority.py`](../apb/guard/authority.py); the table below is a summary, and `results/world/reference.json` carries the full generated version.

| Level | Name | Capability | Exposure | Decision |
|---|---|---|---|---|
| 0 | Observe | Read balances, invoices, quotes or payment status | No authority to create or submit a payment | Observe only |
| 1 | Recommend | Compare routes and recommend an action | Decision influence, no payment instruction | Recommend |
| 2 | Prepare | Create a draft payment instruction | Harmful beneficiary, amount or purpose changes | Prepare for approval |
| 3 | Initiate | Submit into a controlled approval workflow | Operational, fraud and authentication exposure | Initiate only |
| 4 | Execute | Commit funds within policy without per-transaction intervention | Direct financial loss and regulatory exposure | Conditional autonomous execution |
| 5 | Orchestrate | Coordinate agents, providers, budgets or recurring programs | Portfolio, contagion and systemic exposure | Portfolio-bounded orchestration |

## How APB Guard applies it

- Each requested action maps to a level: `payment.read` 0, `payment.quote` 1, `payment.propose` 2, `payment.initiate` 3, `payment.execute` 4, `payment.orchestrate` 5. An unknown action is outside every scope.
- The approved level is the highest level any delegated action reaches. Without `execution_authority`, no delegation reaches Level 4.
- Requesting more than the approved level is an authority gap. For one payment, a transaction-bound approval (two approvers, bound to mandate hash, beneficiary, amount and currency, unexpired at commit) can close it: `step_up_granted` in the receipt. Standing Level 4 authority needs re-evaluation in APB World and shadow evidence.
- The authority profile reports requested and approved level, missing controls, required evidence, whether and how authority can increase, expiry, and a re-authorize-by time.

## Lifecycle

Discover → Evaluate → Authorize → Shadow → Enforce → Monitor → Re-authorize or revoke. Model, prompt, tool or policy changes trigger retest; unapproved versions pause or stop execution (`unapproved_model_version`, `unapproved_prompt_version`, `unapproved_policy_version`).
