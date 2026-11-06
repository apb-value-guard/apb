# APB Value Guard, built on PayPal

Hackathon entry. Deterministic code decides eligibility and scores. AI only
interprets requests and explains results, and it never changes a score, a route
or a number. There are no PayPal calls yet.

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

## AI is optional

**You can rerun everything without an API key.** With no `ANTHROPIC_API_KEY`,
`--case a` and `--case b` use the fixture mandates and template explanations,
and the tests use a mocked client. Only `--text` needs a key, because it has to
read free text. Without one it says so and exits rather than guessing.

To use AI, copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.
`ANTHROPIC_MODEL` is optional.

- **Parsing** (`apb/parser.py`): the model extracts fields from the text. Code
  then converts units, enforces scope (USD or GBP to Kenya, by M-PESA or bank)
  and confirms the amount, wallet and deadline appear in the text. Anything
  missing or unclear becomes a question, and no mandate is produced.
- **Explaining** (`apb/explain.py`): the model sees only the receipt JSON. Its
  text is discarded in favour of the template if it contains a number that isn't
  in the receipt, omits the selected route, or doesn't say synthetic values are
  synthetic. `explanation_source` records which one was used.

## Run it

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m apb.demo --case a
.\.venv\Scripts\python.exe -m apb.demo --case b
.\.venv\Scripts\python.exe -m apb.demo --text "Send `$200 to M-PESA 254700000001 within 2 hours"
```

## License

MIT. See `LICENSE`.
