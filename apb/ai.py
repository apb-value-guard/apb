"""Optional Anthropic access. Everything else must work when this returns None."""

import os
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000
TIMEOUT_SECONDS = 60.0

# Models that accept server-side fallback when a safety classifier declines.
_FALLBACK_MODELS = {
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-fable-5-1",
}


def get_client() -> Anthropic | None:
    """Return a client, or None when no API key is configured."""
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS) if api_key else None


def request_options() -> dict[str, Any]:
    """Return the arguments shared by every model request."""
    load_dotenv()
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    options: dict[str, Any] = {"model": model, "max_tokens": MAX_TOKENS}
    if model in _FALLBACK_MODELS:
        options["betas"] = ["server-side-fallback-2026-07-01"]
        options["fallbacks"] = "default"
    return options
