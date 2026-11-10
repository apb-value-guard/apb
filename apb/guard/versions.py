"""The canonical version matrix. Everything that prints a version reads it here.

The product narrative (APB v3.1) is not an API or method version, so it is
kept apart from the identifiers that appear in receipts and results.
"""

PRODUCT_NARRATIVE = "APB v3.1"
METHOD_VERSION = "apb-0.3-draft"
WORLD_VERSION = "world-0.2.0"
RECEIPT_VERSION = "v3"
DEFAULT_EPISODE = "E-AUTH-004"
POLICY_BUNDLE = "payments-2026.11.1"
DEMO_ASSET_ID = "apb_v2_product_demo"
DEMO_VIDEO = "APBv2.mp4"

# The v0.1 corridor benchmark keeps its own method label. It is now the
# economic-quality stage that runs only after every security gate passes.
CORRIDOR_METHOD_VERSION = "apb-0.1"

VERSION_MATRIX = {
    "product_narrative": PRODUCT_NARRATIVE,
    "method": METHOD_VERSION,
    "world": WORLD_VERSION,
    "decision_receipt": RECEIPT_VERSION,
    "default_episode": DEFAULT_EPISODE,
    "policy_bundle": POLICY_BUNDLE,
    "demo_asset_id": DEMO_ASSET_ID,
    "demo_video": DEMO_VIDEO,
}
