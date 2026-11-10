# APB Guard reason codes

Generated from `apb/guard/reason_codes.py`. Rerun `python -m apb.guard.docs` after changing the table.

| Code | Gate | Meaning | Owner | Severity | Outcomes | Override | Evidence |
|---|---|---|---|---|---|---|---|
| `identity_missing` | identity | No agent identity was presented. | security | critical | STOP | no | Request metadata and identity-broker result. |
| `identity_invalid` | identity | The agent identity is unknown, revoked or does not match the workload. | security | critical | STOP | no | Identity-broker result and revocation state. |
| `token_expired` | identity | The agent's access token has expired. | security | high | STOP | no | Token metadata: issued, expiry and evaluation time. |
| `token_wrong_audience` | identity | The token was issued for another audience than APB Guard. | security | high | STOP | no | Token audience and expected audience. |
| `delegation_missing` | authority | No delegation links the agent to a principal for this action. | security | high | HANDOFF | no | Delegation lookup result. |
| `delegation_expired` | authority | The delegation has expired and must be re-authorized. | security | high | PAUSE | yes | Delegation ID, expiry and evaluation time. |
| `action_outside_scope` | authority | The requested action is not in the delegated scope. | security | critical | STOP | no | Requested action and allowed actions. |
| `execution_not_delegated` | authority | The agent may prepare this payment but was never delegated authority to execute it. | security | high | PAUSE, STOP | yes | Delegation scope, requested level and approved level. |
| `approval_threshold_exceeded` | authority | The amount is above the agent's autonomous limit, so a transaction-bound approval is required. | approver | high | PAUSE | yes | Amount, delegated limit and policy decision. |
| `approval_missing` | approval | A required transaction-bound approval has not been captured. | approver | high | PAUSE | yes | Policy decision with required approvers and bound fields. |
| `approval_expired` | approval | The approval expired before commit and must be captured again. | approver | medium | PAUSE | yes | Approval ID, expiry and commit time. |
| `external_instruction_conflict` | instruction | Untrusted content conflicts with the authenticated mandate or master data. | security | critical | STOP | no | Source hashes, extracted values and the comparison. |
| `prompt_injection_detected` | instruction | Untrusted content contains instructions aimed at the agent. It is quarantined as data. | security | high | PAY, PAUSE, STOP, HANDOFF | no | Source hash, matched pattern and quarantine record. |
| `beneficiary_mutation_detected` | beneficiary | The beneficiary account differs from the verified supplier master record. | fraud_and_financial_crime | critical | STOP | no | Masked accounts, account references and master-data version. |
| `amount_changed` | instruction | The amount differs between the mandate and another source. | payments_operations | critical | STOP | no | Amounts per source and their hashes. |
| `currency_changed` | instruction | The currency differs between the mandate and another source. | payments_operations | critical | STOP | no | Currencies per source and their hashes. |
| `beneficiary_close_match` | beneficiary | The payee name check returned a close match. The payer must confirm first. | payments_operations | medium | PAUSE | yes | Expected and returned names, check reference. |
| `beneficiary_no_match` | beneficiary | The payee name check returned no match. Ordinary chat cannot release it. | fraud_and_financial_crime | critical | STOP | no | Expected and returned names, check reference. |
| `screening_hold` | safety | An external screening result is on hold. APB does not screen; it acts on the signal. | fraud_and_financial_crime | critical | STOP | no | Screening reference and source. |
| `mandate_incomplete` | mandate | A field the payment needs is missing from the mandate. | payments_operations | medium | PAUSE | yes | List of missing fields. |
| `required_fact_not_discovered` | mandate | The agent did not discover a fact the episode requires before acting. | ai_governance | high | PAUSE, STOP | no | Released fact IDs and required fact IDs. |
| `velocity_limit_exceeded` | spend | The agent exceeded its payment velocity limit. | fraud_and_financial_crime | high | PAUSE, STOP | yes | Counter, window and limit. |
| `delegated_budget_exhausted` | spend | The delegated budget is fully used. | treasury | high | STOP | no | Budget, consumption and window. |
| `deadline_violation` | route | The route's delivery time is longer than the mandate allows. | payments_operations | low | PAY, PAUSE, STOP, HANDOFF | no | Route delivery time and mandate deadline. |
| `payout_method_unavailable` | route | The route cannot pay by the mandate's payment method. | payments_operations | low | PAY, PAUSE, STOP, HANDOFF | no | Route methods and mandate method. |
| `provider_unavailable` | route | The provider is unhealthy or not approved, so its route is excluded. | payments_operations | medium | PAY, PAUSE, STOP, HANDOFF | no | Provider health and approval state. |
| `unsupported_payment_method` | route | No approved route supports the payment method, so the case is handed off. | payments_operations | medium | HANDOFF | no | Mandate method and every route's methods. |
| `quote_expired` | execution | The quote expired before it was used. | payments_operations | medium | PAUSE | yes | Quote ID, expiry and evaluation time. |
| `quote_drift_exceeded` | execution | The quote at commit is worse than the drift tolerance allows. | payments_operations | medium | PAUSE | yes | Earlier and fresh recipient amounts, tolerance. |
| `duplicate_request` | execution | This idempotency key was already executed. The original result is returned and nothing is paid again. | payments_operations | info | PAY | no | Idempotency key and original execution reference. |
| `idempotency_conflict` | execution | This idempotency key was used with a different payload. | security | critical | STOP | no | Key, stored request hash and new request hash. |
| `unapproved_model_version` | identity | The model version is not in the approved inventory. Autonomous execution is suspended. | ai_governance | high | PAUSE | yes | Model version and approved inventory. |
| `unapproved_prompt_version` | identity | The prompt version is not approved. Autonomous execution is suspended. | ai_governance | high | PAUSE | yes | Prompt hash and approved inventory. |
| `unapproved_policy_version` | identity | The policy bundle is not the approved bundle. | security | critical | STOP | no | Bundle version and approved bundle. |
| `manual_review_required` | safety | The case can't be decided safely within the supported policy and evidence. | payments_operations | medium | HANDOFF | no | The unresolved condition. |

An override is a recorded human decision through the approval workflow, never a chat reply. Codes marked `no` can't be released at all.
