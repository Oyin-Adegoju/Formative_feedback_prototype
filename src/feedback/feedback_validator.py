"""feedback_validator.py — deterministic validation of LLM feedback output.

Parses the raw JSON string returned by the feedback writer (Stage B) and
enforces all guardrails before the result reaches the caller.

No LLM calls. No prompt logic. No fallback generation.
Raises FeedbackValidationError on any guardrail violation so the caller
(feedback_builder.py) fails hard.

Source of truth is the MERGED feedback input (src/feedback/merge_builder.py),
NOT a raw CapsRunResult. The validator never touches CAPS runtime objects.

Guardrails enforced here:
  - Output must be valid JSON.
  - All required fields must be present and non-empty where expected.
  - stoplight must equal merged["final_stoplight"] (LLM may not change it).
  - No numeric score pattern may appear in any LLM-written text field.
  - Every evidence_ref block ID must exist in the merged evidence_items.
  - Every criterium key in feedback[] must be a known CAPS criterion key.
  - feedback[] must contain exactly one entry per criterion (no missing, no dupes).
  - docent_toelichting must contain the exact final stoplight word.
  - disclaimer, document_id, and stoplight are always overwritten with
    deterministic values — never trusted from LLM output.

Architecture position:
    merged_feedback_input.json → feedback_builder → [llm_client] → feedback_validator → FeedbackResult
                                                                    ^^^^^^^^^^^^^^^^^^
                                                                    this file
"""

from __future__ import annotations

import json
import re
from typing import Final

from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.merge_builder import collect_known_block_ids
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
    "stoplight",
    "student_samenvatting",
    "docent_toelichting",
    "feed_up",
    "feedback",
    "feed_forward",
    "taalgebruik",
})
"""Fields that must be present in the LLM output dict.

stoplight is required here so that a missing field raises "Missing required
field: stoplight" rather than a confusing "Stoplight mismatch: None".
document_id and disclaimer are not required from the LLM — they are always
injected deterministically by this validator.
"""

_STATUS_LABELS: Final[frozenset[str]] = frozenset({
    "missing", "partial", "sufficient", "strong",
})
"""Internal CAPS CriterionStatus values that must not appear in LLM-written output.

Applied only to LLM text fields (student_samenvatting, docent_toelichting,
feed_up, feedback[].observatie, feed_forward, taalgebruik).
Not applied to evidence_ref — block IDs legitimately contain alphanumerics.
"""

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class FeedbackValidationError(Exception):
    """Raised when LLM output violates one or more guardrails.

    Caught by feedback_builder.py, which re-raises as a hard failure.

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


def _llm_text_fields(data: dict) -> str:
    """Concatenate all LLM-written text fields into one string for leak scanning.

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(raw_json: str, merged: dict) -> FeedbackResult:
    """Validate raw LLM output and return a clean FeedbackResult.

    Applies all guardrails in order. Raises FeedbackValidationError on the
    first violation found.

    document_id, stoplight, and disclaimer are always set from the merged input
    and DISCLAIMER — the LLM's own values for these fields are ignored.

    Args:
        raw_json: The raw string returned by the LLM (expected to be JSON).
        merged: The merged feedback input (build_merged_feedback_input output).
            Source of truth for final_stoplight, evidence block IDs, and
            document_id.

    Returns:
        A fully validated FeedbackResult ready for the caller.

    Raises:
        FeedbackValidationError: on any guardrail violation.
    """
    final_stoplight = merged["final_stoplight"]
    document_id = merged["document_id"]

    # --- 1. JSON parse ---
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise FeedbackValidationError(
            f"JSON parse error: {exc}", raw=raw_json
        ) from exc

    if not isinstance(data, dict):
        raise FeedbackValidationError(
            "LLM output is not a JSON object.", raw=raw_json
        )

    # --- 2. Required fields ---
    missing = _REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise FeedbackValidationError(
            f"Missing required fields: {sorted(missing)}", raw=raw_json
        )

    # --- 3. Non-empty student_samenvatting ---
    if not (data.get("student_samenvatting") or "").strip():
        raise FeedbackValidationError(
            "student_samenvatting is empty.", raw=raw_json
        )

    # --- 3b. docent_toelichting must contain the exact final stoplight word ---
    docent = (data.get("docent_toelichting") or "").strip()
    if not re.search(rf"\b{re.escape(final_stoplight)}\b", docent, re.IGNORECASE):
        raise FeedbackValidationError(
            f"docent_toelichting does not mention the final stoplight: {final_stoplight}",
            raw=raw_json,
        )

    # --- 4. Stoplight must match the merged final stoplight ---
    llm_stoplight = data.get("stoplight")
    if llm_stoplight != final_stoplight:
        raise FeedbackValidationError(
            f"Stoplight mismatch: LLM returned '{llm_stoplight}', "
            f"merged input determined '{final_stoplight}'.",
            raw=raw_json,
        )

    # --- 5. No numeric score leak ---
    combined_text = _llm_text_fields(data)
    for pattern in _SCORE_PATTERNS:
        if pattern.search(combined_text):
            raise FeedbackValidationError(
                f"Score leak detected in LLM output (pattern: '{pattern.pattern}').",
                raw=raw_json,
            )

    # --- 5b. No internal CAPS status label leak ---
    for label in _STATUS_LABELS:
        if re.search(rf"\b{label}\b", combined_text, re.IGNORECASE):
            raise FeedbackValidationError(
                f"Internal status label leaked into feedback output: {label}",
                raw=raw_json,
            )

    # --- 6. Disclaimer: if present it must be unmodified ---
    if "disclaimer" in data and data["disclaimer"] != DISCLAIMER:
        raise FeedbackValidationError(
            "Disclaimer was modified by the LLM.", raw=raw_json
        )

    # --- 7. feedback must be a list ---
    if not isinstance(data.get("feedback"), list):
        raise FeedbackValidationError(
            "'feedback' must be a list.", raw=raw_json
        )

    # --- 8. feedback entries: known criterion keys and known evidence refs ---
    known_block_ids = collect_known_block_ids(merged)
    known_keys = frozenset(CRITERIA_KEYS)

    for entry in data["feedback"]:
        crit_key = entry.get("criterium")
        if crit_key is None:
            raise FeedbackValidationError(
                "A feedback entry is missing the 'criterium' field.", raw=raw_json
            )
        if crit_key not in known_keys:
            raise FeedbackValidationError(
                f"Unknown criterion key in feedback: '{crit_key}'.", raw=raw_json
            )
        for block_id in entry.get("evidence_ref") or []:
            if block_id not in known_block_ids:
                raise FeedbackValidationError(
                    f"Unknown evidence_ref block ID: '{block_id}'.", raw=raw_json
                )

    # --- 8b. feedback must contain exactly one entry per CAPS criterion ---
    returned_keys = [
        entry.get("criterium")
        for entry in data["feedback"]
        if isinstance(entry, dict)
    ]
    expected_keys = set(CRITERIA_KEYS)

    missing_criteria = sorted(expected_keys - set(returned_keys))
    if missing_criteria:
        raise FeedbackValidationError(
            f"Missing feedback criteria: {missing_criteria}", raw=raw_json
        )

    seen: set[str] = set()
    for key in returned_keys:
        if key in seen:
            raise FeedbackValidationError(
                f"Duplicate feedback criterion: '{key}'.", raw=raw_json
            )
        if key is not None:
            seen.add(key)

    # --- 9. feed_forward must be a list ---
    if not isinstance(data.get("feed_forward"), list):
        raise FeedbackValidationError(
            "'feed_forward' must be a list.", raw=raw_json
        )

    # --- All checks passed: build FeedbackResult with deterministic overrides ---
    return FeedbackResult(
        document_id=document_id,
        stoplight=final_stoplight,
        student_samenvatting=data["student_samenvatting"],
        docent_toelichting=data["docent_toelichting"],
        feed_up=data["feed_up"],
        feedback=[
            CriterionFeedback(
                criterium=entry["criterium"],
                observatie=entry.get("observatie") or "",
                evidence_ref=list(entry.get("evidence_ref") or []),
            )
            for entry in data["feedback"]
        ],
        feed_forward=list(data["feed_forward"]),
        taalgebruik=data["taalgebruik"],
        disclaimer=DISCLAIMER,
    )
