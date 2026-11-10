# Security policy

## Supported versions

| Component | Supported |
|---|---|
| `main` branch (APB Guard v3, method apb-0.3-draft) | Yes |
| Public demo at apb-value-guard.onrender.com | Yes |
| paymentsbenchmark.com | Yes |
| Older tags and the v0.1 route-quality code | Best effort |

## Reporting a vulnerability

**Do not open a public issue for a vulnerability, and never post credentials, tokens or payment data anywhere.**

Report privately through GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**.

> Maintainer action before publication: enable *Private vulnerability reporting* in the repository settings (Settings → Code security). Until it is enabled, the Security tab has no report button; this placeholder must be resolved before inviting outside reports.

Include what you found, how to reproduce it, the impact you see, and any proof of concept limited to the synthetic fixtures and the PayPal sandbox.

## Scope

In scope: the code in this repository, the public demo, the `/api/v3` and `/v1` APIs, and paymentsbenchmark.com.

Out of scope: denial-of-service or volumetric testing, social engineering, physical attacks, third-party services (PayPal, Render, Cloudflare, GitHub), and findings that need real customer data. Test only against synthetic fixtures and sandbox accounts you control.

## Coordinated disclosure

We aim to acknowledge a report within 5 working days and to agree a fix and disclosure date with you. Please give us reasonable time to fix an issue before publishing. We will credit reporters who want credit in the changelog. There is no paid bug bounty.

## Safe harbour

Good-faith research that follows this policy, stays within scope, avoids privacy violations and service disruption, and stops at the minimum needed to show the issue will not be pursued by the maintainers.
