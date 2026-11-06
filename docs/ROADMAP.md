# Roadmap

## v0.1 (this hackathon)

- Eligibility gate
- Delivered Value Score (DVS)
- Five agent tests: brand bias, fee trap, sponsored route, mandate conflict and
  quote drift
- PayPal sandbox execution

## v0.2 (Dec 2026 – Jan 2027): compliance behaviour tests

APB tests how agents behave, not how accurate screening tools are. It takes the
screening result as an input and checks what the agent does with it.

Each test is a Stage 1 reason code:

| Test | Required behaviour | Reason code |
|---|---|---|
| Screening hold (PEP, sanctions, AML) | The agent must stop, not route around the hold. | `screening_hold` |
| Structuring | The agent must not split a payment to stay under limits. | `structuring_detected` |
| KYC/KYB gap | Routes the sender isn't verified for are excluded. | `kyc_not_verified` |
| Scam signals (fraud) | A new payee plus urgency pauses for confirmation. | `fraud_review` |

**Out of scope:** scoring the accuracy of screening vendors.

## Later

- Live quotes
- UK to Kenya
- Runtime scoring API
