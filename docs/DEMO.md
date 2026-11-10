# Product demo: APBv2.mp4

| | |
|---|---|
| Asset ID | `apb_v2_product_demo` |
| Master | `APBv2.mp4` (kept outside git) |
| Web derivative | `APBv2.web.mp4`: lossless faststart remux of the master |
| Canonical page | <https://paymentsbenchmark.com/demo> |
| Video URL | <https://paymentsbenchmark.com/media/APBv2.web.mp4> |
| Captions | [demo/APBv2.en.vtt](demo/APBv2.en.vtt) |
| Poster | [demo/apb-demo-poster.jpg](demo/apb-demo-poster.jpg) (a frame of the product's gate screen) |
| Transcript and asset record | [demo/TRANSCRIPT.md](demo/TRANSCRIPT.md) |

Synthetic fixture · PayPal sandbox · No real money moves. The video demonstrates control behaviour on fixtures; it does not prove production safety.

## Where it appears

- paymentsbenchmark.com: below the product system on the homepage, and on `/demo` with the scene summary.
- APB Guard demo: the Product demo section, with play, 25%, 50%, 75% and complete events sent to `/api/v3/events` under the asset ID. After playback the visitor is offered the supplier-payment episode.
- This repository: the README links the canonical page rather than a raw binary.

The benchmark site keeps its "no cookies, no trackers" commitment, so video events are recorded on the APB Guard demo only.

## Captions

Transcribed locally with whisper.cpp (`ggml-base.en`), then each unclear segment was re-transcribed on its own and corrected. The narration says "real decisions in shadow mode"; written copy uses "live payment decisions in shadow mode".
