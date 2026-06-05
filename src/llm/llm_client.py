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

_DEFAULT_MODEL: Final[str] = "Qwen2.5-14B-Instruct"
"""Must match the model name as the inference server knows it.
Override with LLM_MODEL environment variable."""

_DEFAULT_MAX_TOKENS: Final[int] = 1024
"""Matches model.max_tokens in guardrails.yaml."""

_DEFAULT_TEMPERATURE: Final[float] = 0.15
"""Low temperature for consistent, structured output.
Callers may override per-call; feedback_builder uses this default."""

_TIMEOUT_SECONDS: Final[int] = 180
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


