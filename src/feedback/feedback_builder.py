"""feedback_builder.py — entry point for the feedback generation layer.

Assembles a prompt from a CapsRunResult, calls the local LLM, validates
the response, and returns a FeedbackResult.

Returns a safe deterministic fallback if the LLM call fails or the output
fails validation — the caller always receives a usable FeedbackResult.

No rubric logic, no scoring, no block scanning. Consumes CapsRunResult only.

Architecture position:
    CapsRunResult → feedback_builder → [llm_client] → feedback_validator → FeedbackResult
                    ^^^^^^^^^^^^^^^^
                    this file
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Final

from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS
from src.caps.models import CapsRunResult
from src.feedback.evidence import EvidencePacket
from src.feedback.feedback_validator import FeedbackValidationError, validate
from src.feedback.output_schema import DISCLAIMER, CriterionFeedback, FeedbackResult
from src.llm import llm_client
from src.llm.llm_client import LlmCallError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_VERSION: Final[str] = "feedback_writer_v3"
_PROMPT_PATH: Final[Path] = (
    Path(__file__).parent.parent.parent / "prompts" / "feedback_writer_v3.txt"
)

# ---------------------------------------------------------------------------
# Status display mapping
# ---------------------------------------------------------------------------

_STATUS_DISPLAY: Final[dict[str, str]] = {
    "missing":    "niet zichtbaar in de aangeleverde evidence",
    "partial":    "deels zichtbaar, maar nog onvoldoende concreet",
    "sufficient": "voldoende zichtbaar",
    "strong":     "sterk uitgewerkt",
}
"""Student-safe Dutch phrases for CriterionStatus values, used in prompt context.

Raw status labels (missing/partial/sufficient/strong) must never reach the LLM
so they cannot leak into student-facing output.
"""

# ---------------------------------------------------------------------------
# Fallback output
# ---------------------------------------------------------------------------


def _fallback(caps_result: CapsRunResult, reason: str) -> FeedbackResult:
    """Return a safe, deterministic FeedbackResult when generation fails.

    Never raises. Always returns a structurally valid FeedbackResult so the
    caller does not need to handle None or re-raise.
    """
    return FeedbackResult(
        document_id=caps_result.doc_id,
        stoplight=caps_result.overall_stoplight,
        student_samenvatting=(
            "De automatische feedbackgeneratie is tijdelijk niet beschikbaar. "
            "Een docent zal je document handmatig beoordelen."
        ),
        docent_toelichting=(
            f"Automatische feedback kon niet worden gegenereerd ({reason}). "
            "Handmatige beoordeling is vereist."
        ),
        feed_up="",
        feedback=[],
        feed_forward=[],
        taalgebruik="",
        disclaimer=DISCLAIMER,
    )
# ---------------------------------------------------------------------------
# Prompt assembly helpers
# ---------------------------------------------------------------------------


def _collect_block_ids(caps_result: CapsRunResult) -> list[str]:
    """Return all unique evidence block IDs from the scorecard, in stable order."""
    seen: set[str] = set()
    ids: list[str] = []
    for key in CRITERIA_KEYS:
        cr = caps_result.scorecard.results.get(key)
        if not cr:
            continue
        for ref in cr.evidence:
            if ref.block_id not in seen:
                seen.add(ref.block_id)
                ids.append(ref.block_id)
    return ids

def _format_caps_criteria_summary(caps_result: CapsRunResult) -> str:
    """Render all criteria for <<CAPS_CRITERIA_SUMMARY>> in the v3 prompt."""
    lines: list[str] = []
    for key in CRITERIA_KEYS:
        cr = caps_result.scorecard.results.get(key)
        spec = CRITERIA_BY_KEY[key]
        if cr is None:
            lines.append(f"{key} ({spec.label}): niet geëvalueerd")
            lines.append("")
            continue
        lines.append(f"{key} ({spec.label})")
        lines.append(f"  stoplight      : {cr.stoplight}")
        if cr.count is not None:
            lines.append(f"  count          : {cr.count}")
        missing = ", ".join(cr.missing_signals) if cr.missing_signals else "-"
        lines.append(f"  missing_signals: {missing}")
        for note in cr.notes:
            lines.append(f"  note: {note}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_evidence_pack(packets: dict[str, EvidencePacket]) -> str:
    """Serialise evidence packets to JSON for <<EVIDENCE_PACK>> in the v3 prompt."""
    data: dict = {}
    for key, pkt in packets.items():
        data[key] = {
            "missing_signals": pkt.missing_signals,
            "evidence_items": [
                {
                    "block_id": item.block_id,
                    "page_no": item.page_no,
                    "block_type": item.block_type,
                    "heading_path": item.heading_path,
                    "excerpt": item.excerpt,
                    "selection_reason": item.selection_reason,
                    "signal_class": item.signal_class,
                }
                for item in pkt.evidence_items
            ],
        }
    return json.dumps(data, indent=2, ensure_ascii=False)


def _assemble_prompt(
    caps_result: CapsRunResult,
    template: str,
    packets: dict[str, EvidencePacket] | None = None,
) -> str:
    """Fill all placeholders in the v3 prompt template."""
    block_ids = _collect_block_ids(caps_result)
    known_ids_str = ", ".join(block_ids) if block_ids else "(geen evidence beschikbaar)"
    evidence_pack_str = _format_evidence_pack(packets) if packets else "(geen evidence beschikbaar)"
    blockers_str = ", ".join(caps_result.blockers_triggered) if caps_result.blockers_triggered else "geen"

    # Manual review is no longer produced by CAPS — it is deferred to the future
    # Qwen stage. The v3 template still carries the placeholders, so we fill them
    # with an explicit "deferred" marker rather than fabricating a verdict.
    manual_review_deferred = "n.v.t. (volgt in Qwen-fase)"

    return (
        template
        .replace("<<DOC_ID>>", caps_result.doc_id)
        .replace("<<STOPLIGHT>>", caps_result.overall_stoplight)
        .replace("<<CRITERION_KEYS>>", ", ".join(CRITERIA_KEYS))
        .replace("<<KNOWN_BLOCK_IDS>>", known_ids_str)
        .replace("<<MANUAL_REVIEW_REQUIRED>>", manual_review_deferred)
        .replace("<<MANUAL_REVIEW_FLAGS>>", manual_review_deferred)
        .replace("<<BLOCKERS_TRIGGERED>>", blockers_str)
        .replace("<<CAPS_CRITERIA_SUMMARY>>", _format_caps_criteria_summary(caps_result))
        .replace("<<EVIDENCE_PACK>>", evidence_pack_str)
    )

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_feedback(
    caps_result: CapsRunResult,
    packets: dict[str, EvidencePacket] | None = None,
) -> FeedbackResult:
    """Generate formative feedback from a CapsRunResult.

    Pipeline:
        1. Load prompt template from prompts/feedback_writer_v1.txt.
        2. Assemble prompt with scorecard data.
        3. Call the local LLM via llm_client.complete().
        4. Validate the raw response via feedback_validator.validate().
        5. Return the validated FeedbackResult.

    On any failure (missing template, LLM error, validation error) the
    function logs the failure and returns a safe fallback FeedbackResult.
    It never raises.

    Args:
        caps_result: The complete output of one CAPS evaluation run.

    Returns:
        A validated FeedbackResult, or a deterministic fallback on failure.
    """
    doc_id = caps_result.doc_id
    stoplight = caps_result.overall_stoplight
    model_id = os.environ.get("LLM_MODEL", "Qwen2.5-14B-Instruct")

    # --- 1. Load template ---
    try:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(
            "doc_id=%s prompt_version=%s | template_missing: %s",
            doc_id, _PROMPT_VERSION, exc,
        )
        return _fallback(caps_result, f"prompt template not found: {exc}")

    # --- 2. Assemble prompt ---
    prompt = _assemble_prompt(caps_result, template, packets=packets)

    # --- 3. LLM call ---
    try:
        raw_json = llm_client.complete(prompt)
    except LlmCallError as exc:
        logger.error(
            "doc_id=%s prompt_version=%s model=%s stoplight=%s | llm_call_failed: %s",
            doc_id, _PROMPT_VERSION, model_id, stoplight, exc.reason,
        )
        return _fallback(caps_result, f"LLM call failed: {exc.reason}")

    # --- 4. Validate ---
    packet_ids: frozenset[str] = frozenset(
        item.block_id
        for pkt in (packets or {}).values()
        for item in pkt.evidence_items
    )
    try:
        result = validate(raw_json, caps_result, extra_known_ids=packet_ids)
    except FeedbackValidationError as exc:
        logger.warning(
            "doc_id=%s prompt_version=%s model=%s stoplight=%s "
            "| validation_failed: %s | raw_preview=%.200s",
            doc_id, _PROMPT_VERSION, model_id, stoplight,
            exc.reason, exc.raw,
        )
        return _fallback(caps_result, f"validation failed: {exc.reason}")

    # --- 5. Return validated result ---
    logger.info(
        "doc_id=%s prompt_version=%s model=%s stoplight=%s | validation_ok",
        doc_id, _PROMPT_VERSION, model_id, stoplight,
    )
    return result