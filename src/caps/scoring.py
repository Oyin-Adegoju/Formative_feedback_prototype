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
# Input validation
# ---------------------------------------------------------------------------


def _validate_criterion_result_keys(
    criterion_results: dict[str, CriterionResult],
) -> None:
    """Verify that criterion_results contains exactly the expected criterion keys.

    Raises ValueError if any expected key is absent or any unexpected key is present.
    CRITERIA_KEYS from criterion_specs is the single source of truth.
    """
    actual: set[str] = set(criterion_results.keys())
    expected: set[str] = set(CRITERIA_KEYS)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected: {sorted(extra)}")
        raise ValueError(f"criterion_results key mismatch — {'; '.join(parts)}")


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


# ---------------------------------------------------------------------------
# Scorecard and run-level builders
# ---------------------------------------------------------------------------


def build_scorecard(
    doc_id: str,
    results: dict[str, CriterionResult],
    hidden_score: int,
) -> CapsScorecard:
    """Construct a CapsScorecard from aggregated criterion results."""
    return CapsScorecard(
        doc_id=doc_id,
        results=results,
        hidden_score=hidden_score,
    )


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


def score_document(
    report: ParseReportDict,
    criterion_results: dict[str, CriterionResult],
    input_source: InputSource = "parser_direct",
) -> CapsRunResult:
    """Aggregate criterion results into a complete CapsRunResult.

    Args:
        report: ParseReportDict from the parser (or anonymizer).
            Only doc_id, source_name, page_count, and block_count are read here —
            no block-level inspection occurs in this layer.
        criterion_results: dict[criterion_key → CriterionResult] from checks.py.
            Expected to contain one entry per evaluated criterion.
        input_source: Whether CAPS received parser-direct or anonymized input.
            Forwarded unchanged into CapsRunMeta for auditability.

    Returns:
        CapsRunResult — the complete, document-level scoring output.
    """
    _validate_criterion_result_keys(criterion_results)

    doc_id: str = report["doc_id"]
    source_name: str = report["source_name"]
    page_count: int = report.get("page_count") or max(  # type: ignore[union-attr]
        (b["page_no"] for b in report["blocks"]), default=0
    )
    block_count: int = report["block_count"]

    # Build a rubric-ordered view so all downstream lists are stable regardless
    # of how the caller constructed criterion_results.
    ordered: dict[str, CriterionResult] = {k: criterion_results[k] for k in CRITERIA_KEYS}

    hidden_score = compute_hidden_score(ordered)
    blockers_triggered = collect_blockers(ordered)
    manual_review_flags = collect_manual_review_flags(ordered)
    manual_review_required = bool(manual_review_flags)
    overall_stoplight = determine_overall_stoplight(
        blockers_triggered,
        manual_review_required,
        ordered,
    )

    scorecard = build_scorecard(doc_id, ordered, hidden_score)

    run_meta = CapsRunMeta(
        input_source=input_source,
        page_count=page_count,
        block_count=block_count,
        criteria_evaluated=list(CRITERIA_KEYS),
    )

    return CapsRunResult(
        doc_id=doc_id,
        source_name=source_name,
        scorecard=scorecard,
        overall_stoplight=overall_stoplight,
        run_meta=run_meta,
        manual_review_required=manual_review_required,
        blockers_triggered=blockers_triggered,
        manual_review_flags=manual_review_flags,
    )
