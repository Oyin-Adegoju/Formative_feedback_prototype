"""quality_builder.py — entry point for the quality-diagnosis stage (Stage A).

Assembles the quality prompt from a CAPS handoff dict, calls the local LLM,
validates the response, and returns a QualityDiagnostics.

Strict by design: any failure (missing template, LLM error, validation error)
raises QualityGenerationError. There is NO fallback quality output — the merge
step must never run on fabricated diagnostics.

No rubric logic, no scoring, no block scanning. Consumes the handoff dict only.

Architecture position:
    caps_handoff.json → quality_builder → [llm_client] → quality_validator → QualityDiagnostics
                        ^^^^^^^^^^^^^^^^
                        this file
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Final

from src.llm import llm_client
from src.llm.llm_client import LlmCallError
from src.quality.output_schema import QualityDiagnostics
from src.quality.quality_validator import QualityValidationError, validate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_VERSION: Final[str] = "quality_diagnostics_all_criteria_v2"
_PROMPT_PATH: Final[Path] = (
    Path(__file__).parent.parent.parent
    / "prompts" / "qwen_quality" / "quality_diagnostics_all_criteria_v2.txt"
)

# Quality output covers five criteria with several bullet lists each, so it
# needs more headroom than the llm_client default.
_DEFAULT_MAX_TOKENS: Final[int] = 2048


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class QualityGenerationError(Exception):
    """Raised when the quality stage cannot produce a validated diagnosis.

    Wraps template/LLM/validation failures so callers fail hard with one type.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _assemble_prompt(handoff: dict, template: str) -> str:
    """Fill the quality prompt template from a CAPS handoff dict.

    Placeholders in quality_diagnostics_all_criteria_v2.txt:
        <<DOC_ID>>            → handoff["document_id"]
        <<CAPS_HANDOFF_JSON>> → the full handoff serialised as pretty JSON
    """
    doc_id = handoff.get("document_id", "")
    handoff_json = json.dumps(handoff, indent=2, ensure_ascii=False)
    return (
        template
        .replace("<<DOC_ID>>", doc_id)
        .replace("<<CAPS_HANDOFF_JSON>>", handoff_json)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_quality_diagnostics(
    handoff: dict,
    *,
    timeout: int = 800,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
) -> QualityDiagnostics:
    """Generate per-criterion quality diagnostics from a CAPS handoff dict.

    Pipeline:
        1. Load the quality prompt template.
        2. Assemble the prompt (handoff JSON).
        3. Call the local LLM via llm_client.complete().
        4. Validate the raw response via quality_validator.validate().
        5. Return the validated QualityDiagnostics.

    Strict: raises QualityGenerationError on any failure. Never returns a
    fallback.

    Args:
        handoff: The CAPS handoff dict (handoff.to_dict output).
        timeout: LLM request timeout in seconds.
        max_tokens: Maximum response tokens.

    Returns:
        A validated QualityDiagnostics.

    Raises:
        QualityGenerationError: on template, LLM, or validation failure.
    """
    doc_id = handoff.get("document_id", "")
    model_id = os.environ.get("LLM_MODEL", "Qwen2.5-14B-Instruct")

    try:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(
            "doc_id=%s prompt_version=%s | template_missing: %s",
            doc_id, _PROMPT_VERSION, exc,
        )
        raise QualityGenerationError(f"prompt template not found: {exc}") from exc

    prompt = _assemble_prompt(handoff, template)

    try:
        raw_json = llm_client.complete(prompt, model=None, max_tokens=max_tokens, timeout=timeout)
    except LlmCallError as exc:
        logger.error(
            "doc_id=%s prompt_version=%s model=%s | llm_call_failed: %s",
            doc_id, _PROMPT_VERSION, model_id, exc.reason,
        )
        raise QualityGenerationError(f"LLM call failed: {exc.reason}") from exc

    try:
        result = validate(raw_json, document_id=doc_id)
    except QualityValidationError as exc:
        logger.warning(
            "doc_id=%s prompt_version=%s model=%s | validation_failed: %s | raw_preview=%.200s",
            doc_id, _PROMPT_VERSION, model_id, exc.reason, exc.raw,
        )
        raise QualityGenerationError(f"validation failed: {exc.reason}") from exc

    logger.info(
        "doc_id=%s prompt_version=%s model=%s | validation_ok",
        doc_id, _PROMPT_VERSION, model_id,
    )
    return result
