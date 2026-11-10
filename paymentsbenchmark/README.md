# paymentsbenchmark.com

Static source for <https://paymentsbenchmark.com>: the APB v3.1 category, method, benchmark, evidence and enterprise site. Every page is pre-rendered HTML that works without JavaScript; scripts only add motion, the form helper and copy buttons. No cookies, no trackers.

## Data

The site never restates numbers by hand. `build.py` and `v3.py` read, from the main repository:

- `results/world/reference.json`: version matrix, Authority Levels, reason codes, episodes (`python -m apb.guard.docs`)
- `results/world/world-0.2.0.json`: APB World results (`python -m apb.guard.world`)
- `results/world/sample-receipts.json`: real Decision Receipts
- `docs/METHOD.md` and `docs/METHOD_V0_1.md`: published as `/method.md` and `/method-v0.1.md`

The v0.1 corridor tables on `/results` come from the data block in `build.py` (method apb-0.1).

## Build

```
python build.py
```

Writes `dist/` (deployable, clean URLs) and `preview/` (relative links). Both are git-ignored. The product video is not in git: place `APBv2.web.mp4` in `media/` first, or set `APB_SKIP_VIDEO=1` to build without it (CI does this).

## Deploy

`deploy/deploy.ps1` uploads `dist/` to the host, swaps releases atomically and rolls back on failure. See [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md).

## Contact form

There is no backend. The form opens the visitor's mail app addressed to `CONTACT` in `build.py`. That address needs a working mailbox before the form is promoted.

Fonts: Space Grotesk and JetBrains Mono, SIL Open Font License (licences in `src/fonts/`).
