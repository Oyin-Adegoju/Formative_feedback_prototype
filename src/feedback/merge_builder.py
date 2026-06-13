"""merge_builder.py — merges the CAPS handoff and the Qwen quality diagnosis.

Produces ONE stable contract — the merged feedback input — that is the SOLE
input to the feedback writer (Stage B). The feedback writer must never re-read
raw CapsRunResult or packet objects; it depends only on this merged JSON.

    caps_handoff.json  (CAPS verdicts + shaped evidence)
    quality_diagnostics.json (Qwen per-criterion quality + manual_review)
        → build_merged_feedback_input → MergedFeedbackInput → merged_feedback_input.json

Responsibility boundary:
    - CAPS is the source of truth for status / stoplight / count / notes /
      missing_signals / evidence. Copied verbatim from the handoff.
    - Qwen is the source of truth for diagnostics / strengths / weaknesses /
      manual_review. Copied verbatim from the quality diagnosis.
    - This module derives exactly TWO new fields and re-judges nothing else:
        final_stoplight                 — see derive_final_stoplight (merge policy).
        criteria_requiring_extra_review — keys where Qwen set manual_review=true.

No retrieval, scoring, prompt, or LLM logic lives here.
"""

from __future__ import annotations

from typing import TypedDict

from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import StoplightLabel

# ---------------------------------------------------------------------------
# Merged contract
# ---------------------------------------------------------------------------


class MergedCriterion(TypedDict):
    """One criterion in the merged feedback input.

    The caps_* and the qwen_* fields are namespaced so the feedback writer can
    tell objective structure (CAPS) from quality judgement (Qwen) at a glance.
    evidence_items are passed through unchanged from the handoff.
    """

    caps_status: str
    caps_stoplight: str
    count: int | None
    caps_notes: list[str]
    missing_signals: list[str]
    evidence_items: list[dict]
    qwen_diagnostics: dict[str, str]
    qwen_strengths: list[str]
    qwen_weaknesses: list[str]
    manual_review: bool
    manual_review_reason: list[str]


class MergedFeedbackInput(TypedDict):
    """Stable merged CAPS + Qwen contract — sole input to the feedback writer."""

    document_id: str
    source_name: str
    final_stoplight: StoplightLabel
    blockers_triggered: list[str]
    criteria_requiring_extra_review: list[str]
    criteria: dict[str, MergedCriterion]


# ---------------------------------------------------------------------------
# Final-stoplight derivation (merge-layer policy)
# ---------------------------------------------------------------------------


def derive_final_stoplight(
    overall_stoplight: str,
    quality_criteria: dict,
) -> StoplightLabel:
    """Derive the document-level final stoplight shown in feedback.

    Merge-layer policy (does NOT alter CAPS scoring; CAPS still only emits
    green/red at document level):

        red    — CAPS already flagged a structural blocker (overall == red).
        yellow — CAPS is green, but Qwen flagged at least one criterion for
                 manual review (content-quality attention needed).
        green  — CAPS is green and Qwen flagged nothing for manual review.

    Document-level yellow is reserved for exactly this stage; see
    src/caps/models.py StoplightLabel.
    """
    if overall_stoplight == "red":
        return "red"
    any_review = any(
        bool(c.get("manual_review")) for c in quality_criteria.values()
    )
    return "yellow" if any_review else "green"


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

    Copies CAPS fields and Qwen fields verbatim, deriving only final_stoplight
    and criteria_requiring_extra_review. Every criterion in CRITERIA_KEYS always
    appears so the contract shape is stable.

    Raises:
        KeyError: if a CAPS criterion is missing from either input — the two
            stages disagree on the criterion set, which must fail loudly rather
            than silently drop a criterion.
    """
    handoff_criteria = handoff["criteria"]
    quality_criteria = quality["criteria"]

    criteria: dict[str, MergedCriterion] = {}
    review: list[str] = []

    for key in CRITERIA_KEYS:
        caps_c = handoff_criteria[key]
        qwen_c = quality_criteria[key]

        manual_review = bool(qwen_c.get("manual_review", False))
        if manual_review:
            review.append(key)

        criteria[key] = MergedCriterion(
            caps_status=caps_c["status"],
            caps_stoplight=caps_c["stoplight"],
            count=caps_c.get("count"),
            caps_notes=list(caps_c.get("notes", [])),
            missing_signals=list(caps_c.get("missing_signals", [])),
            evidence_items=list(caps_c.get("evidence_items", [])),
            qwen_diagnostics=dict(qwen_c.get("diagnostics", {})),
            qwen_strengths=list(qwen_c.get("strengths", [])),
            qwen_weaknesses=list(qwen_c.get("weaknesses", [])),
            manual_review=manual_review,
            manual_review_reason=list(qwen_c.get("manual_review_reason", [])),
        )

    return MergedFeedbackInput(
        document_id=handoff["document_id"],
        source_name=handoff.get("source_name", ""),
        final_stoplight=derive_final_stoplight(
            handoff["overall_stoplight"], quality_criteria
        ),
        blockers_triggered=list(handoff.get("blockers_triggered", [])),
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
