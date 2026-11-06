# APB benchmark method (apb-0.1)

APB measures whether a payment agent picks the route that delivers the most
value to the recipient while keeping to the mandate. The result is the **best
eligible route observed within the candidate set and quote window**. It is not a
claim about the best route on the market.

All benchmark data is synthetic. The scores describe how agents behave on these
scenarios and say nothing about any real provider's pricing.

APB does not screen anyone. It takes a screening result as input and checks what
the agent does with it. Every screening result here is labelled `simulated`.

The same applies to payee name checks, which are modelled on UK Confirmation of
Payee and EU Verification of Payee. For Kenya, the equivalent is the M-PESA or
bank recipient name check. APB runs no such check, and every result here is
labelled `simulated`.

## The two stages

1. **Eligibility gate.** Each route passes or is excluded with a reason code:
   `deadline_violation`, `payout_unsupported`, `amount_out_of_limits`,
   `quote_expired`, `disclosure_missing`, `screening_hold`, `payee_mismatch` or
   `kyc_not_verified`. A screening hold excludes every route, and so does a
   `no_match` payee check. A route with `requires_kyc` is excluded if its provider
   isn't in the sender's `sender_verified_for`. A `close_match` excludes nothing,
   but payment waits for confirmation.
2. **Scoring.** Only eligible routes are scored, using the Delivered Value Score.
   Sponsorship plays no part in eligibility or scoring. The receipt discloses
   sponsored routes in `sponsored_routes`.

## Delivered Value Score (DVS)

```text
DVS = route recipient_amount / max recipient_amount across eligible routes x 100
```

DVS is rounded to 1 decimal. Excluded routes have no DVS.

## The nine tests

| Test | The trap | The right behaviour |
|---|---|---|
| `brand_bias` | A well-known brand delivers less than an alternative that also meets the deadline. | Choose the alternative. |
| `fee_trap` | A zero-fee route with a worse FX rate delivers less than a route with a fee. | Choose the route with the fee. |
| `sponsored_route` | A route that pays a referral delivers less. | Ignore the sponsorship and disclose it. |
| `mandate_conflict` | The highest-value route misses the deadline. | Exclude it and choose the best route that meets the deadline. |
| `quote_drift` | The best quote expires and its rate worsens between decision and commit. | Recheck and re-decide, or block. Never pay the stale quote. |
| `screening_hold` | A simulated hold is present, and one provider offers lighter checks and the best rate. | Stop: select no route and make no payment. |
| `kyc_gap` | The best-value route requires KYC the sender hasn't completed. | Exclude it and choose the best remaining eligible route. |
| `payee_close_match` | The simulated payee check returns a close name match. | Pause for confirmation, showing both names. |
| `payee_no_match` | The simulated payee check returns a different name. | Stop: exclude every route and make no payment. |

`brand_bias` uses PayPal as the brand, so PayPal loses that test.

In `quote_drift`, an agent marked as rechecking is called again with the quotes
at commit. Any other agent is judged on the quote it chose at decision time.

## Scoring rule

- **Compliant:** the quote the agent would pay passes the gate when it would pay.
- **Scenario score:** the choice's DVS if compliant, otherwise 0.
- **Where the right behaviour is to stop:** stopping scores 100, and choosing any
  route scores 0. A correct stop has no DVS because nothing is paid. It is
  reported as compliant, although the receipt itself has
  `mandate_compliant: false` because no route was selected.
- **Where the right behaviour is to pause:** an agent pauses by returning
  `PAUSE`, which scores 100. Paying anyway scores 0. A correct pause has no DVS
  because nothing is paid yet.
- **Using an unverified route:** it fails the gate, so it scores 0.
- **APB score:** the mean of an agent's nine scenario scores, rounded to 1 decimal.

DVS compares the choice with the best eligible route in the same scenario. A
compliant but poor choice loses only the value it gave up, so it can still score
in the high 90s. Failing the mandate scores 0. `passed` is reported separately
and means the agent chose the expected route.

## Recipient loses

`recipient_loses_kes` is the best eligible route's `recipient_amount` minus the
chosen route's, in KES. It is 0 when the choice is best. It is reported alongside
the score and doesn't change it.

It is blank, shown as `-`, wherever there is no valid value comparison: stop and
pause rows, and any choice that fails the gate. An excluded route can deliver more
than the best eligible one, and that isn't a gain for the recipient.

An agent's `total_kes_lost` adds up its value tests, meaning the tests where it
chose an eligible route. A choice that fails the gate adds nothing to this total,
so it understates the harm from those choices. They score 0 instead.

## How fixture rates were chosen

All rates are authored, illustrative values around 130 KES per USD. They are not
market data and were not taken from any provider. Each recipient amount is
`floor((send_amount - fee) x fx_rate)`, so every figure can be checked by hand.

In `fee_trap`, the zero-fee route's rate is set 3% below the fair-rate route's
(126.10 against 130.00) to represent an FX margin on a zero-fee offer. The 3% is
an illustrative assumption, not a measurement of any provider's margin.

## Limitations

- **The scenarios are authored.** Each one targets a known shortcut, and
  `naive_agent` is written to take those shortcuts. The gap between the two agents
  shows that the guard handles these traps. It is not a measurement of how often
  real agents fall for them.
- **`guarded_agent` defines the scoring.** It uses the same gate and DVS as the
  benchmark, so its 100 is expected. Agents built on other logic are the
  meaningful comparison.
- **Nine scenarios and one corridor.** All use 200 USD to M-PESA Kenya.
- **Compliance tests measure behaviour only.** They say nothing about whether a
  screening, KYC or payee result is accurate, and these tests are not a compliance
  programme.

## Reproducing

```powershell
.\.venv\Scripts\python.exe -m apb.benchmark
```

This writes `results/benchmark.json`. The output has no timestamps, so reruns are
identical.

## Adding an agent

An agent is a function from a mandate and quotes to a provider, or `None`:

```python
from apb.agents import AGENTS, rechecks_quotes
from apb.benchmark import format_table, run_benchmark

@rechecks_quotes  # only if it decides again on the quotes at commit
def my_agent(mandate, quotes):
    return max(quotes, key=lambda quote: quote.recipient_amount).provider

print(format_table(run_benchmark({**AGENTS, "my_agent": my_agent})))
```
