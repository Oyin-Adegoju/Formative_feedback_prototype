"""merge_builder.py — merges the CAPS handoff and the Qwen quality diagnosis.

Produces ONE stable contract — the merged feedback input — that is the SOLE
input to the feedback writer (Stage B). The feedback writer must never re-read
raw CapsRunResult or packet objects; it depends only on this merged JSON.

    caps_handoff.json  (CAPS extraction + shaped evidence)
    quality_diagnostics.json (Qwen per-criterion quality + criterion_judgement)
        → build_merged_feedback_input → MergedFeedbackInput → merged_feedback_input.json

Responsibility boundary:
    - CAPS is the source of truth for the OBJECTIVE EXTRACTION only:
      count / notes / missing_signals / evidence. Copied verbatim from the
      handoff. The handoff carries no CAPS status/stoplight verdicts.
    - Qwen is the source of truth for the CONTENT-QUALITY JUDGEMENT:
      criterion_judgement (strong/mixed/weak), diagnostics, strengths,
      weaknesses, manual_review. Copied verbatim from the quality diagnosis.
    - THIS module owns the DETERMINISTIC AGGREGATION: it maps Qwen's
      per-criterion judgement to a per-criterion stoplight (with a manual_review
      guard and an objective count-floor guard), then aggregates those into the
      document-level final stoplight. Qwen never emits a stoplight; CAPS never
      decides the document verdict before the quality stage.

The aggregation is implemented as small pure helper functions so each rule is
independently testable.

No retrieval, scoring, prompt, or LLM logic lives here.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, TypedDict

from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS
from src.caps.models import StoplightLabel

# ---------------------------------------------------------------------------
# Aggregation constants
# ---------------------------------------------------------------------------

_JUDGEMENT_TO_STOPLIGHT: Final[dict[str, StoplightLabel]] = {
    "strong": "green",
    "mixed": "yellow",
    "weak": "red",
}
"""Base mapping from Qwen's content-quality judgement to a per-criterion stoplight."""

_COUNT_FLOOR_KEYS: Final[frozenset[str]] = frozenset({"stakeholders", "requirements"})
"""Criteria whose objective CAPS count, when below the rubric minimum_count,
forces the per-criterion stoplight to red regardless of Qwen's judgement.

Deliberately limited to the two countable BLOCKER criteria with hard minimums.
Not applied to security (also countable) or to beperking / taalkeuze."""


# ---------------------------------------------------------------------------
# Merged contract
# ---------------------------------------------------------------------------


class MergedCriterion(TypedDict):
    """One criterion in the merged feedback input.

    caps_* fields are objective extraction. qwen_* fields are quality judgement.
    criterion_judgement is Qwen's verdict (strong/mixed/weak); criterion_stoplight
    is the deterministic per-criterion stoplight Python derived from it.
    evidence_items are passed through unchanged from the handoff.
    """

    count: int | None
    caps_notes: list[str]
    missing_signals: list[str]
    evidence_items: list[dict]
    qwen_diagnostics: dict[str, str]
    qwen_strengths: list[str]
    qwen_weaknesses: list[str]
    criterion_judgement: str
    criterion_stoplight: StoplightLabel
    manual_review: bool
    manual_review_reason: list[str]


class MergedFeedbackInput(TypedDict):
    """Stable merged CAPS + Qwen contract — sole input to the feedback writer."""

    document_id: str
    source_name: str
    final_stoplight: StoplightLabel
    criteria_requiring_extra_review: list[str]
    criteria: dict[str, MergedCriterion]


# ---------------------------------------------------------------------------
# Per-criterion stoplight derivation (pure helpers)
# ---------------------------------------------------------------------------


def map_judgement_to_stoplight(judgement: str) -> StoplightLabel:
    """Map a Qwen content-quality judgement to its base stoplight.

    strong → green, mixed → yellow, weak → red. An unknown value defaults to
    yellow (the cautious middle) — the validator already rejects unknown values
    upstream, so this is a defensive fallback only.
    """
    return _JUDGEMENT_TO_STOPLIGHT.get(judgement, "yellow")


def apply_manual_review_guard(
    stoplight: StoplightLabel, manual_review: bool
) -> StoplightLabel:
    """Downgrade a green criterion to yellow when Qwen flagged manual_review.

    manual_review only ever downgrades green → yellow. It never upgrades, and it
    never turns yellow into red on its own.
    """
    if stoplight == "green" and manual_review:
        return "yellow"
    return stoplight


def apply_count_floor(
    key: str,
    stoplight: StoplightLabel,
    count: int | None,
    minimum: int | None,
) -> StoplightLabel:
    """Force red when an objectively under-count countable criterion is floored.

    Only stakeholders and requirements are floored (see _COUNT_FLOOR_KEYS). When
    the CAPS count is known and below the rubric minimum_count, the criterion is
    red regardless of Qwen's judgement. Otherwise the stoplight is unchanged.
    """
    if (
        key in _COUNT_FLOOR_KEYS
        and minimum is not None
        and count is not None
        and count < minimum
    ):
        return "red"
    return stoplight


def derive_criterion_stoplight(
    key: str,
    judgement: str,
    manual_review: bool,
    count: int | None,
    minimum: int | None,
) -> StoplightLabel:
    """Deterministic per-criterion stoplight: base map → review guard → count floor.

    The count floor is applied last so it wins over both the base mapping and the
    manual_review guard.
    """
    stoplight = map_judgement_to_stoplight(judgement)
    stoplight = apply_manual_review_guard(stoplight, manual_review)
    stoplight = apply_count_floor(key, stoplight, count, minimum)
    return stoplight


# ---------------------------------------------------------------------------
# Overall-stoplight derivation (merge-layer policy, post-Qwen)
# ---------------------------------------------------------------------------


def derive_overall_stoplight(
    criterion_stoplights: Iterable[StoplightLabel],
) -> StoplightLabel:
    """Aggregate per-criterion stoplights into the document-level final stoplight.

        red    — any per-criterion stoplight is red.
        yellow — none red, but at least one is yellow.
        green  — all green.

    Assigned HERE, after Qwen quality output exists — never by CAPS before the
    quality stage.
    """
    seen = set(criterion_stoplights)
    if "red" in seen:
        return "red"
    if "yellow" in seen:
        return "yellow"
    return "green"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_merged_feedback_input(
    handoff: dict,
    quality: dict,
) -> MergedFeedbackInput:
    """Merge a CAPS handoff dict and a quality diagnosis dict into one contract.

    Both inputs are the already-serialised stage artifacts:
        handoff — src/feedback/handoff.to_dict output (caps_handoff.json)
        quality — src/quality QualityDiagnostics (quality_diagnostics.json)

    Copies CAPS extraction fields and Qwen fields verbatim, and derives the
    per-criterion criterion_stoplight (via derive_criterion_stoplight) and the
    document-level final_stoplight (via derive_overall_stoplight). Every criterion
    in CRITERIA_KEYS always appears so the contract shape is stable.

    Raises:
        KeyError: if a CAPS criterion is missing from either input — the two
            stages disagree on the criterion set, which must fail loudly rather
            than silently drop a criterion.
    """
    handoff_criteria = handoff["criteria"]
    quality_criteria = quality["criteria"]

    criteria: dict[str, MergedCriterion] = {}
    review: list[str] = []
    stoplights: list[StoplightLabel] = []

    for key in CRITERIA_KEYS:
        caps_c = handoff_criteria[key]
        qwen_c = quality_criteria[key]

        manual_review = bool(qwen_c.get("manual_review", False))
        if manual_review:
            review.append(key)

        count = caps_c.get("count")
        minimum = CRITERIA_BY_KEY[key].minimum_count
        judgement = qwen_c.get("criterion_judgement", "mixed")
        criterion_stoplight = derive_criterion_stoplight(
            key, judgement, manual_review, count, minimum
        )
        stoplights.append(criterion_stoplight)

        criteria[key] = MergedCriterion(
            count=count,
            caps_notes=list(caps_c.get("notes", [])),
            missing_signals=list(caps_c.get("missing_signals", [])),
            evidence_items=list(caps_c.get("evidence_items", [])),
            qwen_diagnostics=dict(qwen_c.get("diagnostics", {})),
            qwen_strengths=list(qwen_c.get("strengths", [])),
            qwen_weaknesses=list(qwen_c.get("weaknesses", [])),
            criterion_judgement=judgement,
            criterion_stoplight=criterion_stoplight,
            manual_review=manual_review,
            manual_review_reason=list(qwen_c.get("manual_review_reason", [])),
        )

    return MergedFeedbackInput(
        document_id=handoff["document_id"],
        source_name=handoff.get("source_name", ""),
        final_stoplight=derive_overall_stoplight(stoplights),
        criteria_requiring_extra_review=review,
        criteria=criteria,
    )


def collect_known_block_ids(merged: dict) -> frozenset[str]:
    """All evidence block IDs referenced in the merged input, for validation."""
    ids: set[str] = set()
    for crit in merged.get("criteria", {}).values():
        for item in crit.get("evidence_items", []):
            bid = item.get("block_id")
            if bid:
                ids.add(bid)
    return frozenset(ids)
