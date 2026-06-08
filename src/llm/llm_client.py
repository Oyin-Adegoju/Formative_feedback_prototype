"""llm_client.py — thin wrapper around the local Qwen2.5-14B-Instruct inference server.

Single public function: complete(prompt, temperature) → str

Returns the raw response text. JSON parsing and validation are the caller's
responsibility (feedback_validator.py).

Raises LlmCallError on connection failures, timeouts, or unexpected responses.

Compatible with any OpenAI-compatible local inference server:
  Ollama     (default)  http://localhost:11434/v1
  LM Studio             http://localhost:1234/v1
  vLLM                  http://localhost:8000/v1

Override the base URL via the LLM_BASE_URL environment variable.
Override the model name via the LLM_MODEL environment variable.

No external dependencies — uses only Python stdlib (urllib, json, os).

Architecture position:
    feedback_builder → llm_client → inference server
                       ^^^^^^^^^^
                       this file
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Final

# ---------------------------------------------------------------------------
# Defaults — match guardrails.yaml where applicable
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL: Final[str] = "http://localhost:11434/v1"
"""Ollama default. Override with LLM_BASE_URL environment variable."""

_DEFAULT_MODEL: Final[str] = "qwen2.5:14b"
"""Must match the model name as the inference server knows it.
Override with LLM_MODEL environment variable."""

_DEFAULT_MAX_TOKENS: Final[int] = 1024
"""Matches model.max_tokens in guardrails.yaml."""

_DEFAULT_TEMPERATURE: Final[float] = 0.15
"""Low temperature for consistent, structured output.
Callers may override per-call; feedback_builder uses this default."""

_TIMEOUT_SECONDS: Final[int] = 800
"""Request timeout. 14B models on modest hardware can be slow."""


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class LlmCallError(Exception):
    """Raised when the inference server call fails for any reason.

    Caught by feedback_builder.py to trigger fallback feedback output.

    reason:      human-readable description of what went wrong.
    status_code: HTTP status code if the server responded; None otherwise.
    """

    def __init__(self, reason: str, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def complete(
    prompt: str,
    temperature: float = _DEFAULT_TEMPERATURE,
    model: str | None = None,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    timeout: int = _TIMEOUT_SECONDS,
) -> str:
    """Send a prompt to the local inference server and return the raw response text.

    Uses the OpenAI-compatible POST /v1/chat/completions endpoint.
    The full prompt is sent as a single user message — no system/user split,
    because the prompt template already contains all role and guardrail context.

    Args:
        prompt:      The fully assembled prompt from feedback_builder.py.
        temperature: Sampling temperature. Keep low (0.1–0.2) for structured output.
        model:       Model name override. Defaults to Qwen2.5-14B-Instruct.
        max_tokens:  Maximum tokens in the response.
        timeout:     Request timeout in seconds. Default matches _TIMEOUT_SECONDS (800 s).
                     Pass a higher value for very slow cold-starts on modest hardware.

    Returns:
        Raw string content of the model's first response choice.

    Raises:
        LlmCallError: on HTTP error, connection failure, timeout, or unexpected
            response shape from the inference server.
    """
    base_url = os.environ.get("LLM_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    model_id = model or os.environ.get("LLM_MODEL", _DEFAULT_MODEL)
    endpoint = f"{base_url}/chat/completions"

    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    req = urllib.request.Request(
        url=endpoint,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LlmCallError(
            f"HTTP {exc.code} from inference server at {endpoint}: {exc.reason}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise LlmCallError(
            f"Could not reach inference server at {base_url}: {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise LlmCallError(
            f"Inference server timed out after {_TIMEOUT_SECONDS}s ({endpoint})."
        ) from exc
    except json.JSONDecodeError as exc:
        raise LlmCallError(
            f"Inference server returned non-JSON response: {exc}"
        ) from exc

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LlmCallError(
            f"Unexpected response shape from inference server: {exc}\nBody: {body}"
        ) from exc


