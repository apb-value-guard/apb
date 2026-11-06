"""Optional AI access. Everything else must work when no backend is available.

AI_BACKEND selects the backend:
- claude_cli (default): the local `claude` command, using its own login.
- api: the Anthropic API, only if ANTHROPIC_API_KEY is set.
- none: no AI.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal, TypeVar

import anthropic
from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

logger = logging.getLogger(__name__)

Backend = Literal["claude_cli", "api", "none"]
OutputT = TypeVar("OutputT", bound=BaseModel)

DEFAULT_BACKEND = "claude_cli"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000
TIMEOUT_SECONDS = 60.0
CLI_TIMEOUT_SECONDS = 120.0

# Models that accept server-side fallback when a safety classifier declines.
_FALLBACK_MODELS = {
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-fable-5-1",
}

# Removed so the CLI uses its own login: it would otherwise bill these keys.
_CLI_BILLING_VARIABLES = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}

# Removed so a run from inside a Claude Code session behaves like any other.
_CLI_SESSION_VARIABLES = {
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SESSION_ID",
}

_SAFE_MODEL_NAME = re.compile(r"[\w.\-\[\]]+")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class AIError(Exception):
    """The backend was tried and gave no usable result."""


def _model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL


def selected_backend() -> Backend:
    """Return the backend to use, from AI_BACKEND in the environment or .env."""
    load_dotenv()
    choice = os.environ.get("AI_BACKEND", "").strip().lower() or DEFAULT_BACKEND
    if choice == "api" and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        logger.warning("AI_BACKEND is api but ANTHROPIC_API_KEY is not set.")
        return "none"
    if choice not in {"claude_cli", "api", "none"}:
        logger.warning("Unknown AI_BACKEND %r, so AI is off.", choice)
        return "none"
    return choice


def get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS)


def request_options() -> dict[str, Any]:
    """Return the arguments shared by every API request."""
    load_dotenv()
    model = _model()
    options: dict[str, Any] = {"model": model, "max_tokens": MAX_TOKENS}
    if model in _FALLBACK_MODELS:
        options["betas"] = ["server-side-fallback-2026-07-01"]
        options["fallbacks"] = "default"
    return options


def _cli_environment() -> dict[str, str]:
    removed = _CLI_BILLING_VARIABLES | _CLI_SESSION_VARIABLES
    return {key: value for key, value in os.environ.items() if key not in removed}


def _run_claude(system: str, user: str) -> str:
    """Run one tool-free `claude -p` turn and return its text.

    On Windows `claude` is a .cmd launcher, so its arguments pass through
    cmd.exe, where characters like & would act as shell syntax. User text
    therefore goes through stdin and the system prompt through a file.
    """
    executable = shutil.which("claude")
    if executable is None:
        raise AIError("the claude command was not found")
    model = _model()
    if not _SAFE_MODEL_NAME.fullmatch(model):
        raise AIError("ANTHROPIC_MODEL is not a valid model name")

    # An empty working directory keeps the project out of the model's context.
    with tempfile.TemporaryDirectory() as workspace:
        system_file = Path(workspace) / "system.txt"
        system_file.write_text(system, encoding="utf-8")
        try:
            completed = subprocess.run(
                [
                    executable,
                    "-p",
                    "--output-format",
                    "json",
                    "--model",
                    model,
                    "--system-prompt-file",
                    str(system_file),
                    "--tools",
                    "",
                    "--safe-mode",
                    "--strict-mcp-config",
                    "--disable-slash-commands",
                    "--no-session-persistence",
                ],
                input=user,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                cwd=workspace,
                env=_cli_environment(),
                timeout=CLI_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise AIError(f"the claude command failed: {type(error).__name__}") from error

    if completed.returncode != 0:
        raise AIError(f"the claude command exited with {completed.returncode}")
    try:
        reply = json.loads(completed.stdout)
    except ValueError as error:
        raise AIError("the claude command did not return JSON") from error
    if not isinstance(reply, dict) or reply.get("is_error") is not False:
        raise AIError("the claude command reported an error")
    if reply.get("stop_reason") != "end_turn":
        raise AIError(f"unusable result (stop: {reply.get('stop_reason')})")
    text = reply.get("result")
    if not isinstance(text, str) or not text.strip():
        raise AIError("the claude command returned no text")
    return text.strip()


def complete_text(
    system: str, user: str, client: Anthropic | None = None
) -> str | None:
    """Return the model's text, or None if AI is off. Raises AIError on failure."""
    backend = "api" if client else selected_backend()
    if backend == "none":
        return None
    if backend == "claude_cli":
        return _run_claude(system, user)

    try:
        response = (client or get_client()).beta.messages.create(
            **request_options(),
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.AnthropicError as error:
        raise AIError(f"the API call failed: {type(error).__name__}") from error
    if response.stop_reason != "end_turn":
        raise AIError(f"unusable result (stop: {response.stop_reason})")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise AIError("the API returned no text")
    return text


def complete_structured(
    system: str,
    user: str,
    output_type: type[OutputT],
    client: Anthropic | None = None,
) -> OutputT | None:
    """Return a validated object, or None if AI is off. Raises AIError on failure."""
    backend = "api" if client else selected_backend()
    if backend == "none":
        return None

    if backend == "claude_cli":
        schema = json.dumps(output_type.model_json_schema(), sort_keys=True)
        instruction = (
            f"{system}\n\nReply with only a JSON object that matches this JSON "
            f"Schema, with no other text:\n{schema}"
        )
        text = _run_claude(instruction, user)
        fenced = _CODE_FENCE.match(text)
        try:
            return output_type.model_validate_json(
                fenced.group(1) if fenced else text
            )
        except ValueError as error:
            raise AIError("the reply did not match the schema") from error

    try:
        response = (client or get_client()).beta.messages.parse(
            **request_options(),
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_type,
        )
    except (anthropic.AnthropicError, ValueError) as error:
        raise AIError(f"the API call failed: {type(error).__name__}") from error
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        raise AIError(f"unusable result (stop: {response.stop_reason})")
    return response.parsed_output
