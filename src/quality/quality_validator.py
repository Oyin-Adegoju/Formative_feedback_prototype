"""quality_validator.py — deterministic validation of Qwen quality output.

Parses the raw JSON returned by the quality LLM call and enforces all
guardrails before the diagnosis reaches the merge step.

No LLM calls. No prompt logic. No fallback generation.
Raises QualityValidationError on any guardrail violation so the caller
(quality_builder.py) fails hard — there is no fallback quality output.

Guardrails enforced here:
  - Output must be a valid JSON object.
  - criteria must contain exactly the five CAPS criteria, no more, no fewer.
  - Each criterion must carry diagnostics, strengths, weaknesses,
    manual_review, manual_review_reason, reason.
  - diagnostics must contain exactly the expected dimensions for that
    criterion, each value in {laag, middel, hoog}.
  - manual_review must be a real boolean.
  - strengths / weaknesses / manual_review_reason must be lists of strings.
  - No numeric score pattern and no internal CAPS status label may appear in
    any Qwen-written text field.
  - document_id is always overwritten deterministically from the handoff.

Architecture position:
    caps_handoff.json → quality_builder → [llm_client] → quality_validator → QualityDiagnostics
                                                          ^^^^^^^^^^^^^^^^^^
                                                          this file
"""

from __future__ import annotations

import json
import re
from typing import Final

from src.caps.criterion_specs import CRITERIA_KEYS
from src.quality.output_schema import (
    QUALITY_DIMENSIONS,
    QUALITY_LEVELS,
    CriterionQuality,
    QualityDiagnostics,
)

# ---------------------------------------------------------------------------
# Leak-detection patterns (shared shape with the feedback validator)
# ---------------------------------------------------------------------------

_SCORE_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"\b\d+\s*/\s*1[0-5]\b"),
    re.compile(r"\b(hidden[_\s])?score\s*[:=]\s*\d+", re.IGNORECASE),
    re.compile(r"\bcijfer\s*[:=]\s*\d+", re.IGNORECASE),
    re.compile(r"\b\d+\s*punt(?:en)?\b", re.IGNORECASE),
]
"""Numeric-score leaks. The quality stage must never emit a score or hidden_score."""

_STATUS_LABELS: Final[frozenset[str]] = frozenset({
    "missing", "partial", "sufficient", "strong",
})
"""Internal CAPS status labels that must not be repeated verbatim in Qwen text."""

_REQUIRED_CRITERION_FIELDS: Final[frozenset[str]] = frozenset({
    "diagnostics", "strengths", "weaknesses",
    "manual_review", "manual_review_reason", "reason",
})


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class QualityValidationError(Exception):
    """Raised when quality output violates one or more guardrails.

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


def _criterion_text(crit: dict) -> str:
    """Concatenate all Qwen-written text in one criterion for leak scanning."""
    parts: list[str] = [
        crit.get("reason") or "",
        " ".join(str(x) for x in (crit.get("strengths") or [])),
        " ".join(str(x) for x in (crit.get("weaknesses") or [])),
        " ".join(str(x) for x in (crit.get("manual_review_reason") or [])),
    ]
    return " ".join(parts)


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _validate_criterion(key: str, crit: object, raw: str) -> CriterionQuality:
    if not isinstance(crit, dict):
        raise QualityValidationError(
            f"Criterion '{key}' is not a JSON object.", raw=raw
        )

    missing = _REQUIRED_CRITERION_FIELDS - set(crit.keys())
    if missing:
        raise QualityValidationError(
            f"Criterion '{key}' missing fields: {sorted(missing)}", raw=raw
        )

    # --- diagnostics: exact dimension set, each value a valid level ---
    diagnostics = crit.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise QualityValidationError(
            f"Criterion '{key}' diagnostics is not an object.", raw=raw
        )
    expected_dims = set(QUALITY_DIMENSIONS[key])
    actual_dims = set(diagnostics.keys())
    if actual_dims != expected_dims:
        raise QualityValidationError(
            f"Criterion '{key}' diagnostics dimensions mismatch: "
            f"expected {sorted(expected_dims)}, got {sorted(actual_dims)}",
            raw=raw,
        )
    for dim, level in diagnostics.items():
        if level not in QUALITY_LEVELS:
            raise QualityValidationError(
                f"Criterion '{key}' dimension '{dim}' has invalid value "
                f"'{level}' (allowed: {sorted(QUALITY_LEVELS)}).",
                raw=raw,
            )

    # --- manual_review must be a real bool (not a truthy string) ---
    if not isinstance(crit.get("manual_review"), bool):
        raise QualityValidationError(
            f"Criterion '{key}' manual_review must be a boolean.", raw=raw
        )

    # --- list fields ---
    for list_field in ("strengths", "weaknesses", "manual_review_reason"):
        if not _is_str_list(crit.get(list_field)):
            raise QualityValidationError(
                f"Criterion '{key}' {list_field} must be a list of strings.",
                raw=raw,
            )

    if not isinstance(crit.get("reason"), str):
        raise QualityValidationError(
            f"Criterion '{key}' reason must be a string.", raw=raw
        )

    # --- no score / status-label leaks in any text field ---
    combined = _criterion_text(crit)
    for pattern in _SCORE_PATTERNS:
        if pattern.search(combined):
            raise QualityValidationError(
                f"Score leak in criterion '{key}' (pattern: '{pattern.pattern}').",
                raw=raw,
            )
    for label in _STATUS_LABELS:
        if re.search(rf"\b{label}\b", combined, re.IGNORECASE):
            raise QualityValidationError(
                f"Internal CAPS status label '{label}' leaked into criterion '{key}'.",
                raw=raw,
            )

    return CriterionQuality(
        diagnostics=dict(diagnostics),
        strengths=list(crit["strengths"]),
        weaknesses=list(crit["weaknesses"]),
        manual_review=bool(crit["manual_review"]),
        manual_review_reason=list(crit["manual_review_reason"]),
        reason=crit["reason"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(raw_json: str, document_id: str) -> QualityDiagnostics:
    """Validate raw quality LLM output and return a clean QualityDiagnostics.

    Applies all guardrails in order, raising QualityValidationError on the first
    violation. document_id is always set from the supplied handoff value — the
    LLM's own document_id is ignored.

    Args:
        raw_json: The raw string returned by the LLM (expected JSON).
        document_id: The authoritative document id from the CAPS handoff.

    Returns:
        A fully validated QualityDiagnostics with one entry per CAPS criterion
        in CRITERIA_KEYS order.

    Raises:
        QualityValidationError: on any guardrail violation.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise QualityValidationError(
            f"JSON parse error: {exc}", raw=raw_json
        ) from exc

    if not isinstance(data, dict):
        raise QualityValidationError(
            "Quality output is not a JSON object.", raw=raw_json
        )

    criteria = data.get("criteria")
    if not isinstance(criteria, dict):
        raise QualityValidationError(
            "Quality output is missing a 'criteria' object.", raw=raw_json
        )

    expected = set(CRITERIA_KEYS)
    actual = set(criteria.keys())
    if actual != expected:
        raise QualityValidationError(
            f"Quality criteria keys mismatch: expected {sorted(expected)}, "
            f"got {sorted(actual)}",
            raw=raw_json,
        )

    validated: dict[str, CriterionQuality] = {}
    for key in CRITERIA_KEYS:  # stable order
        validated[key] = _validate_criterion(key, criteria[key], raw_json)

    return QualityDiagnostics(document_id=document_id, criteria=validated)
