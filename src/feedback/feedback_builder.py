"""feedback_builder.py — entry point for the feedback writer (Stage B).

Assembles the feedback-writer prompt from the MERGED feedback input, calls the
local LLM, validates the response, and returns a FeedbackResult.

Strict by design: any failure (missing template, LLM error, validation error)
raises FeedbackGenerationError. There is NO fallback feedback — a failed call
must surface, not be papered over with deterministic filler.

No rubric logic, no scoring, no block scanning, NO CapsRunResult. This stage
consumes the merged CAPS + Qwen contract (src/feedback/merge_builder.py) only.

Architecture position:
    merged_feedback_input.json → feedback_builder → [llm_client] → feedback_validator → FeedbackResult
                                 ^^^^^^^^^^^^^^^^
                                 this file
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Final

from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.feedback_validator import FeedbackValidationError, validate
from src.feedback.output_schema import FeedbackResult
from src.llm import llm_client
from src.llm.llm_client import LlmCallError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_VERSION: Final[str] = "feedback_writer_v4"
_PROMPT_PATH: Final[Path] = (
    Path(__file__).parent.parent.parent / "prompts" / "feedback_writer_v4.txt"
)

# The merged payload plus five criteria of feedback needs more headroom than the
# llm_client default.
_DEFAULT_MAX_TOKENS: Final[int] = 2048


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class FeedbackGenerationError(Exception):
    """Raised when the feedback stage cannot produce validated feedback.

    Wraps template/LLM/validation failures so callers fail hard with one type.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _assemble_prompt(merged: dict, template: str) -> str:
    """Fill the v4 feedback-writer prompt from the merged feedback input.

    Placeholders in feedback_writer_v4.txt:
        <<STOPLIGHT>>                   — merged["final_stoplight"]
        <<CRITERION_KEYS>>              — comma-joined CAPS criterion keys
        <<MERGED_FEEDBACK_INPUT_JSON>>  — the full merged input as pretty JSON
    """
    merged_json = json.dumps(merged, indent=2, ensure_ascii=False)
    return (
        template
        .replace("<<STOPLIGHT>>", merged["final_stoplight"])
        .replace("<<CRITERION_KEYS>>", ", ".join(CRITERIA_KEYS))
        .replace("<<MERGED_FEEDBACK_INPUT_JSON>>", merged_json)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_feedback(
    merged: dict,
    *,
    timeout: int = 800,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> FeedbackResult:
    """Generate formative feedback from the merged CAPS + Qwen contract.

    Pipeline:
        1. Load the v4 prompt template.
        2. Assemble the prompt from the merged input.
        3. Call the local LLM via llm_client.complete().
        4. Validate the raw response via feedback_validator.validate().
        5. Return the validated FeedbackResult.

    Strict: raises FeedbackGenerationError on any failure. Never returns a
    fallback.

    Args:
        merged: The merged feedback input (build_merged_feedback_input output).
        timeout: LLM request timeout in seconds.
        max_tokens: Maximum response tokens.

    Returns:
        A validated FeedbackResult.

    Raises:
        FeedbackGenerationError: on template, LLM, or validation failure.
    """
    doc_id = merged.get("document_id", "")
    stoplight = merged.get("final_stoplight", "")
    model_id = os.environ.get("LLM_MODEL", "Qwen2.5-14B-Instruct")

    try:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(
            "doc_id=%s prompt_version=%s | template_missing: %s",
            doc_id, _PROMPT_VERSION, exc,
        )
        raise FeedbackGenerationError(f"prompt template not found: {exc}") from exc

    prompt = _assemble_prompt(merged, template)

    try:
        raw_json = llm_client.complete(prompt, model=None, max_tokens=max_tokens, timeout=timeout)
    except LlmCallError as exc:
        logger.error(
            "doc_id=%s prompt_version=%s model=%s stoplight=%s | llm_call_failed: %s",
            doc_id, _PROMPT_VERSION, model_id, stoplight, exc.reason,
        )
        raise FeedbackGenerationError(f"LLM call failed: {exc.reason}") from exc

    try:
        result = validate(raw_json, merged)
    except FeedbackValidationError as exc:
        logger.warning(
            "doc_id=%s prompt_version=%s model=%s stoplight=%s "
            "| validation_failed: %s | raw_preview=%.200s",
            doc_id, _PROMPT_VERSION, model_id, stoplight, exc.reason, exc.raw,
        )
        raise FeedbackGenerationError(f"validation failed: {exc.reason}") from exc

    logger.info(
        "doc_id=%s prompt_version=%s model=%s stoplight=%s | validation_ok",
        doc_id, _PROMPT_VERSION, model_id, stoplight,
    )
    return result
