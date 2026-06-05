"""scoring.py — CAPS document-level scoring and scorecard construction.

Consumes per-criterion CriterionResult objects (produced by checks.py) and
aggregates them into a single document-level CapsRunResult.

No retrieval, no checks, no block scanning, no model calls.
Deterministic and stateless: same inputs always produce the same outputs.

Architecture position:
    parser output → [anonymizer] → CAPS retrieval → CAPS checks → CAPS scoring
"""

from __future__ import annotations

from typing import Final

from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import (
    CapsRunMeta,
    CapsRunResult,
    CapsScorecard,
    CriterionResult,
    CriterionStatus,
    InputSource,
    ParseReportDict,
    StoplightLabel,
)

# ---------------------------------------------------------------------------
# Status → numeric mapping
# ---------------------------------------------------------------------------

_STATUS_SCORE: Final[dict[CriterionStatus, int]] = {
    "missing": 0,
    "partial": 1,
    "sufficient": 2,
    "strong": 3,
}
"""Fixed numeric weight for each criterion status.

Per-criterion range: 0–3.
hidden_score range (5 criteria): 0–15.
Belongs here, not in criterion_specs.py — scoring concern, not rubric metadata.
"""

_SUFFICIENT_STATUSES: Final[frozenset[str]] = frozenset({"sufficient", "strong"})
"""Statuses that satisfy 'at least sufficient' for the green stoplight condition."""

_BLOCKER_TRIGGERING_STATUSES: Final[frozenset[str]] = frozenset({"missing", "partial"})
"""Statuses that activate a blocker when the criterion has is_blocker=True."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def status_to_score(status: CriterionStatus) -> int:
    """Return the numeric score for a criterion status (0–3)."""
    return _STATUS_SCORE[status]


def compute_hidden_score(results: dict[str, CriterionResult]) -> int:
    """Sum numeric status scores across all criterion results.

    With 5 criteria the range is 0 (all missing) to 15 (all strong).
    """
    return sum(status_to_score(r.status) for r in results.values())


def collect_blockers(results: dict[str, CriterionResult]) -> list[str]:
    """Return criterion keys where is_blocker=True and status is missing or partial."""
    return [
        key
        for key, r in results.items()
        if r.is_blocker and r.status in _BLOCKER_TRIGGERING_STATUSES
    ]


def collect_manual_review_flags(results: dict[str, CriterionResult]) -> list[str]:
    """Return criterion keys where manual_review=True."""
    return [key for key, r in results.items() if r.manual_review]


def determine_overall_stoplight(
    blockers_triggered: list[str],
    manual_review_required: bool,
    results: dict[str, CriterionResult],
) -> StoplightLabel:
    """Apply the document-level stoplight policy.

    red:    any blocker triggered, or manual_review_required is True
    green:  every criterion is at least 'sufficient', no blockers, no manual review
    yellow: everything else (non-blocker edge states)
    """
    if blockers_triggered or manual_review_required:
        return "red"

    if all(r.status in _SUFFICIENT_STATUSES for r in results.values()):
        return "green"

    return "yellow"
