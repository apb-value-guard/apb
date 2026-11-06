# APB Value Guard, built on PayPal

Hackathon entry. This step is the deterministic decision engine only: it makes
no AI calls and no PayPal calls.

> **Every value in `fixtures/` is synthetic.** The providers, fees, FX rates,
> recipient amounts, delivery times and wallet are made up for demonstration.
> They are not real quotes and say nothing about any real provider's pricing.

Recipient amounts are rounded down to whole KES.

## How a decision is made

1. **Eligibility gate** (`apb/eligibility.py`): each route either passes or is
   excluded with a reason code.
2. **Scoring** (`apb/scoring.py`): Delivered Value Score (DVS) is the route's
   recipient amount divided by the best eligible recipient amount, times 100.
   Excluded routes are never scored.
3. **Decision** (`apb/decide.py`): selects the eligible route with the highest
   recipient amount and returns a `DecisionReceipt`.

## Run it

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m apb.demo --case a
.\.venv\Scripts\python.exe -m apb.demo --case b
```

## License

MIT. See `LICENSE`.
