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

import logging
import os
from pathlib import Path
from typing import Final

from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS
from src.caps.models import CapsRunResult
from src.feedback.feedback_validator import FeedbackValidationError, validate
from src.feedback.output_schema import DISCLAIMER, CriterionFeedback, FeedbackResult
from src.llm import llm_client
from src.llm.llm_client import LlmCallError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_VERSION: Final[str] = "feedback_writer_v1"
_PROMPT_PATH: Final[Path] = (
    Path(__file__).parent.parent.parent / "prompts" / "feedback_writer_v1.txt"
)

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

def _format_scorecard(caps_result: CapsRunResult) -> str:
    """Render all criteria as a compact human-readable block for the prompt."""
    lines: list[str] = []
    for key in CRITERIA_KEYS:
        cr = caps_result.scorecard.results.get(key)
        spec = CRITERIA_BY_KEY[key]

        lines.append(f"CRITERIUM: {key} — {spec.label}")

        if cr is None:
            lines.append("  Status  : (niet geëvalueerd)")
            lines.append("")
            continue

        lines.append(f"  Status  : {cr.status}")

        if cr.count is not None:
            lines.append(f"  Aantal  : {cr.count}")

        for note in cr.notes:
            lines.append(f"  Notitie : {note}")

        if cr.evidence:
            ids = ", ".join(ref.block_id for ref in cr.evidence)
            lines.append(f"  Evidence: {ids}")
        else:
            lines.append("  Evidence: (geen)")

        if cr.manual_review:
            lines.append("  ⚠ Handmatige verificatie aanbevolen")

        lines.append("")

    return "\n".join(lines).strip()
