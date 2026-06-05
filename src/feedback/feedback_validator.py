"""feedback_validator.py — deterministic validation of LLM feedback output.

Parses the raw JSON string returned by the LLM and enforces all guardrails
before the result reaches the caller.

No LLM calls. No prompt logic. No fallback generation.
Raises FeedbackValidationError on any guardrail violation so the caller
(feedback_builder.py) can decide whether to retry or return fallback output.

Guardrails enforced here:
  - Output must be valid JSON.
  - All required fields must be present and non-empty where expected.
  - stoplight must equal CapsRunResult.overall_stoplight (LLM may not change it).
  - No numeric score pattern may appear in any LLM-written text field.
  - Every evidence_ref block ID must exist in the CAPS scorecard evidence.
  - Every criterium key in feedback[] must be a known CAPS criterion key.
  - disclaimer, document_id, and stoplight are always overwritten with
    deterministic values — never trusted from LLM output.

Architecture position:
    CapsRunResult → feedback_builder → [llm_client] → feedback_validator → FeedbackResult
                                                       ^^^^^^^^^^^^^^^^^^
                                                       this file
"""

from __future__ import annotations

import json
import re
from typing import Final

from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import CapsRunResult
from src.feedback.output_schema import DISCLAIMER, CriterionFeedback, FeedbackResult
# ---------------------------------------------------------------------------
# Score-leak detection patterns
# ---------------------------------------------------------------------------

_SCORE_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"\b\d+\s*/\s*1[0-5]\b"),                          # e.g. 8/10, 12/15
    re.compile(r"\b(hidden[_\s])?score\s*[:=]\s*\d+", re.IGNORECASE),  # score: 7
    re.compile(r"\bcijfer\s*[:=]\s*\d+", re.IGNORECASE),          # cijfer: 8
    re.compile(r"\b\d+\s*punt(?:en)?\b", re.IGNORECASE),          # 12 punten
]
"""Regex patterns that indicate a numeric score has leaked into LLM output.

Only applied to LLM-written text fields (student_samenvatting, docent_toelichting,
feed_up, feedback[].observatie, feed_forward, taalgebruik).
Not applied to evidence_ref (block IDs legitimately contain numbers).
"""
# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS: Final[frozenset[str]] = frozenset({
    "student_samenvatting",
    "docent_toelichting",
    "feed_up",
    "feedback",
    "feed_forward",
    "taalgebruik",
})
"""Fields that must be present in the LLM output dict.

document_id, stoplight, and disclaimer are not required from the LLM —
they are always injected deterministically by this validator.
"""
# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class FeedbackValidationError(Exception):
    """Raised when LLM output violates one or more guardrails.

    Caught by feedback_builder.py to trigger fallback output.

    reason: human-readable description of the violated guardrail.
    raw:    the raw LLM string that caused the failure (for logging).
    """

    def __init__(self, reason: str, raw: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw = raw

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _collect_known_block_ids(caps_result: CapsRunResult) -> frozenset[str]:
    """Collect all EvidenceRef block IDs from the CAPS scorecard."""
    ids: set[str] = set()
    for cr in caps_result.scorecard.results.values():
        for ref in cr.evidence:
            ids.add(ref.block_id)
    return frozenset(ids)


def _llm_text_fields(data: dict) -> str:
    """Concatenate all LLM-written text fields into one string for score-leak scanning.

    Excludes evidence_ref values — block IDs legitimately contain digits.
    """
    parts: list[str] = [
        data.get("student_samenvatting") or "",
        data.get("docent_toelichting") or "",
        data.get("feed_up") or "",
        data.get("taalgebruik") or "",
        " ".join(data.get("feed_forward") or []),
        " ".join(
            entry.get("observatie") or ""
            for entry in (data.get("feedback") or [])
            if isinstance(entry, dict)
        ),
    ]
    return " ".join(parts)


