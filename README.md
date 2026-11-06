# APB Value Guard, built on PayPal

Hackathon entry. Deterministic code decides eligibility and scores. AI only
interprets requests and explains results, and it never changes a score, a route
or a number, sees a credential, or triggers a payment. Payment runs only in the
PayPal sandbox.

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

**You can rerun everything with no AI at all.** `--case a` and `--case b` then
use the fixture mandates and template explanations, and the tests are mocked.
Only `--text` needs AI, because it has to read free text. Without it, `--text`
says so and exits rather than guessing.

AI needs no setup if you are logged in to Claude Code. To change that, copy
`.env.example` to `.env` and set `AI_BACKEND`:

- `claude_cli` (default): runs the local `claude` command with your own login.
  If it is missing or fails, explanations use the template.
- `none`: turns AI off.
- `api`: uses the Anthropic API instead. This is optional and is the only
  backend that needs `ANTHROPIC_API_KEY`.

`ANTHROPIC_MODEL` is optional. `claude_cli` runs with tools disabled and never
passes an API key to the command, so it cannot bill one.

- **Parsing** (`apb/parser.py`): the model extracts fields from the text. Code
  then converts units, enforces scope (USD or GBP to Kenya, by M-PESA or bank)
  and confirms the amount, wallet and deadline appear in the text. Anything
  missing or unclear becomes a question, and no mandate is produced.
- **Explaining** (`apb/explain.py`): the model sees the receipt, its mandate
  (including the wallet) and a list of allowed numbers. Its text is discarded in
  favour of the template if it contains a number that isn't in the receipt or
  mandate, including any derived or spelled-out number, omits the selected
  route, or doesn't say synthetic values are synthetic. `explanation_source`
  records which one was used.

## Executing in the PayPal sandbox

Nothing is paid without `--execute`. Add `PAYPAL_CLIENT_ID`,
`PAYPAL_CLIENT_SECRET` and `PAYPAL_SANDBOX_RECEIVER` (a sandbox personal account
email) to `.env`, never to `.env.example`, then run:

```powershell
.\.venv\Scripts\python.exe -m apb.demo --case a --execute
```

> **The sandbox payout is a USD stand-in for M-PESA delivery.** No real money
> moves and nothing reaches M-PESA. The KES amounts stay synthetic, and the
> receipt's `execution.note` says so.

- **Only PayPal pays.** Any other selected route records `handoff_logged` with
  `executed: false` and makes no PayPal call.
- **The quote is rechecked at commit.** The eligibility gate runs again with the
  real time. An expired quote stops with `quote_expired` and nothing is paid.
- **Approval.** A fee above `approval_required_above_fee` needs `--approve`.
- **Payee name check (simulated).** A `close_match` waits at `needs_confirmation`
  and shows both names until `--approve` or the UI's Confirm button. A `no_match`
  excludes every route and never executes. The UI's button confirms the name only.
- **A rerun can't pay twice.** `sender_batch_id` is a hash of `mandate_hash` and
  the selected `quote_id`, and PayPal rejects a repeat within 30 days. A rerun
  therefore pays nothing, and a new payment needs a new mandate.
- **`--fresh` makes a new payout on purpose.** With `--execute`, it gives the
  mandate a new timestamped `mandate_id`, which produces a new `sender_batch_id`.
  Use it for demos. Without it, reruns stay idempotent.
- **Live runs re-stamp quotes.** With `--execute`, synthetic quotes get the
  current time when loaded, so the recheck is meaningful. Without it, and in
  tests, they keep their fixed fixture time.
- **The AI has no part in paying.** The explanation is written before execution,
  from decision fields only. The `claude` command runs without any `PAYPAL_`
  variables, and only `apb/paypal.py` reads them. All PayPal URLs are checked
  against the sandbox host, and redirects are not followed.

## Benchmark

`python -m apb.benchmark` scores agents on nine synthetic tests, with no AI, and
writes `results/benchmark.json`. Reruns are identical. An agent is a plain
function, so you can add your own. See [docs/METHOD.md](docs/METHOD.md) for the
method and its limitations.

## Web UI

`python -m apb.web` serves a one-page UI at <http://localhost:8000>, on your
machine only. The "Execute in sandbox" button is off by default. To turn it on
in PowerShell, with the virtual environment active:

```powershell
$env:ALLOW_EXECUTE="true"; python -m apb.web
```

The setting lasts for that PowerShell window, and `.env` works too. Each decision
then uses a fresh mandate, so executing it makes a new sandbox payout.
Credentials never reach the browser, and the wallet is masked to its last 4
digits.

## Run it

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m apb.demo --case a
.\.venv\Scripts\python.exe -m apb.demo --case b
.\.venv\Scripts\python.exe -m apb.demo --text "Send `$200 to M-PESA 254700000001 within 2 hours"
```

## Roadmap

v0.1 is this hackathon build, including simulated screening hold, KYC and payee
name tests.
v0.2 adds structuring and scam signal tests. APB checks what an agent does with a
screening result. It does not screen anyone. See
[docs/ROADMAP.md](docs/ROADMAP.md).

## License

MIT. See `LICENSE`.
