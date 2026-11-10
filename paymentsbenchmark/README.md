# paymentsbenchmark.com

Static site for the Agent Payments Benchmark (APB). Every page is plain pre-rendered HTML, so it works without JavaScript and is readable by search and AI crawlers. JavaScript only adds motion, the replay feed, the toggles and copy buttons.

## Deploy

Upload the contents of `dist/` to any static host. Pages are `method.html`, `results.html` and so on, served at clean URLs (`/method`, `/results`):

- **Cloudflare Pages / Netlify / GitHub Pages:** clean URLs work out of the box. Set `404.html` as the not-found page if the host asks.
- **Vercel:** `dist/vercel.json` turns on `cleanUrls`.

Point paymentsbenchmark.com at the host. No cookies, no trackers, fonts are self-hosted.

## Edit and rebuild

All numbers live once, in the data block at the top of `build.py` (tests, agents, observed runs). Page text is in `src/pages/*.html`; styles and motion in `src/assets/`.

```
python3 build.py
```

This writes `dist/` (deployable, clean URLs) and `preview/` (relative `.html` links for local preview). `benchmark.json` and `results.md` are generated from the same data, so they always match the pages.

## Before going live

- `evaluations@paymentsbenchmark.com` is a placeholder contact address. Create it or change `CONTACT` in `build.py`.
- The demo video card on the home page is a placeholder until the video is published. The live demo card links to https://apb-value-guard.onrender.com.
- Point the two extra domains at the main site with a permanent (301) redirect, keeping the path: `paymentbenchmark.com` → `https://paymentsbenchmark.com` and `4pb.ai` → `https://paymentsbenchmark.com`. Most registrars and Cloudflare offer this as "URL forwarding" or a "redirect rule".

Fonts: Space Grotesk and JetBrains Mono, SIL Open Font License (licences in `dist/fonts/`).
