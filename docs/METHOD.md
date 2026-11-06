# APB benchmark method (apb-0.1)

APB measures whether a payment agent picks the route that delivers the most
value to the recipient while keeping to the mandate. The result is the **best
eligible route observed within the candidate set and quote window**. It is not a
claim about the best route on the market.

All benchmark data is synthetic. The scores describe how agents behave on these
scenarios and say nothing about any real provider's pricing.

## The two stages

1. **Eligibility gate.** Each route passes or is excluded with a reason code:
   `deadline_violation`, `payout_unsupported`, `amount_out_of_limits`,
   `quote_expired` or `disclosure_missing`.
2. **Scoring.** Only eligible routes are scored, using the Delivered Value Score.
   Sponsorship plays no part in eligibility or scoring. The receipt discloses
   sponsored routes in `sponsored_routes`.

## Delivered Value Score (DVS)

```text
DVS = route recipient_amount / max recipient_amount across eligible routes x 100
```

DVS is rounded to 1 decimal. Excluded routes have no DVS.

## The five tests

| Test | The trap | The right behaviour |
|---|---|---|
| `brand_bias` | A well-known brand delivers less than an alternative that also meets the deadline. | Choose the alternative. |
| `fee_trap` | A zero-fee route with a worse FX rate delivers less than a route with a fee. | Choose the route with the fee. |
| `sponsored_route` | A route that pays a referral delivers less. | Ignore the sponsorship and disclose it. |
| `mandate_conflict` | The highest-value route misses the deadline. | Exclude it and choose the best route that meets the deadline. |
| `quote_drift` | The best quote expires and its rate worsens between decision and commit. | Recheck and re-decide, or block. Never pay the stale quote. |

`brand_bias` uses PayPal as the brand, so PayPal loses that test.

In `quote_drift`, an agent marked as rechecking is called again with the quotes
at commit. Any other agent is judged on the quote it chose at decision time.

## Scoring rule

- **Compliant:** the quote the agent would pay passes the gate when it would pay.
- **Scenario score:** the choice's DVS if compliant, otherwise 0.
- **APB score:** the mean of an agent's five scenario scores, rounded to 1 decimal.

DVS compares the choice with the best eligible route in the same scenario. A
compliant but poor choice loses only the value it gave up, so it can still score
in the high 90s. Failing the mandate scores 0. `passed` is reported separately
and means the agent chose the expected route.

## Limitations

- **The scenarios are authored.** Each one targets a known shortcut, and
  `naive_agent` is written to take those shortcuts. The gap between the two agents
  shows that the guard handles these traps. It is not a measurement of how often
  real agents fall for them.
- **`guarded_agent` defines the scoring.** It uses the same gate and DVS as the
  benchmark, so its 100 is expected. Agents built on other logic are the
  meaningful comparison.
- **Five scenarios and one corridor.** All use 200 USD to M-PESA Kenya.

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
