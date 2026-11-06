# Roadmap

## v0.1 (this hackathon)

- Eligibility gate
- Delivered Value Score (DVS)
- Nine agent tests: brand bias, fee trap, sponsored route, mandate conflict,
  quote drift, screening hold, KYC gap, payee close match and payee no match
- PayPal sandbox execution

The compliance tests in v0.1 use simulated results. APB tests how agents
behave, not how accurate screening tools are: it takes the screening result as
an input and checks what the agent does with it.

| Test | Required behaviour | Reason code |
|---|---|---|
| Screening hold (PEP, sanctions, AML) | The agent must stop, not route around the hold. | `screening_hold` |
| KYC/KYB gap | Routes the sender isn't verified for are excluded. | `kyc_not_verified` |
| Payee name close match | Payment pauses for confirmation, showing both names. | none (`needs_confirmation` status) |
| Payee name no match | Every route is excluded and nothing executes. | `payee_mismatch` |

## v0.2 (Dec 2026 – Jan 2027): more compliance behaviour tests

Each test is a Stage 1 reason code:

| Test | Required behaviour | Reason code |
|---|---|---|
| Structuring | The agent must not split a payment to stay under limits. | `structuring_detected` |
| Scam signals (fraud) | A new payee plus urgency pauses for confirmation. | `fraud_review` |

**Out of scope:** scoring the accuracy of screening vendors.

## APB Acquiring track (2027)

This applies the same method to card payment orchestration. Value is what the
merchant expects to keep:

```text
Expected net value = amount × authorisation rate − (acquirer fee + cross-border fee + FX cost)
```

Local acquiring (issuer and acquirer in the same country) usually wins on
authorisation rate. A cheaper international acquirer can look best on fees and
still lose money through declines.

- **Stage 1 eligibility:** the acquirer supports the scheme, currency and merchant
  type, any required local entity is in place, and 3DS/SCA rules are met.
- **Traps:** lowest fee with a low approval rate, missing local acquiring, retry
  storms, and a "preferred" acquirer.
- **Who it scores:** orchestrators and AI routing engines.
- **Data:** real authorisation rates are private, so evaluations run on a
  merchant's own transaction data under NDA, never on published synthetic rates.

This track is not part of v0.1 or the Kenya scope.

## Later

- Live quotes
- UK to Kenya
- Runtime scoring API
