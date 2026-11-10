# Contributing

Thanks for helping. APB is a draft open method; changes to scoring, answer keys or claims need the same evidence as code.

## Set up

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

## Before you open a pull request

- `python -m pytest` passes.
- If you changed `apb/guard/`, run `python -m apb.guard.docs` and `python -m apb.guard.world` and commit the regenerated files. Tests fail when generated files drift.
- If you changed the site, run `python paymentsbenchmark/build.py` (set `APB_SKIP_VIDEO=1` if you don't have the video file).
- Don't change a version label (method, world, receipt, policy bundle) without a changelog entry explaining why.
- No secrets, tokens, real account numbers or customer data in code, fixtures, tests, screenshots or issues.

## Proposing an episode

Open an issue with the episode proposal template. Every episode needs:

1. Opening request, visible facts, hidden facts and attack payload.
2. A deterministic answer key: action, reason codes, whether PayPal is called, security outcome.
3. A relaxing variant where removing one constraint changes the answer.
4. Data mode (`synthetic` for public episodes).
5. A named reviewer other than the author.

## Claims discipline

Label everything as implemented now, supported by design, or roadmap. Don't add certification, compliance, partnership, customer or traction claims. See [docs/CONTROL_EVIDENCE.md](docs/CONTROL_EVIDENCE.md).

## Security issues

Never in public issues. See [SECURITY.md](SECURITY.md).
