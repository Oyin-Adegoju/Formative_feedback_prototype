"""output_schema.py — typed output contract for the quality-diagnosis stage.

Defines the exact JSON structure that quality_builder.py produces and
quality_validator.py enforces. It mirrors the output block of
prompts/qwen_quality/quality_diagnostics_all_criteria_v2.txt — the single
source of truth for the per-criterion diagnostic dimensions.

No logic, no LLM calls. Only types and the dimension/level constants.

Architecture position:
    caps_handoff.json → quality_builder → [llm_client] → quality_validator → QualityDiagnostics
                                                                              ^^^^^^^^^^^^^^^^^^
                                                                              defined here
"""

from __future__ import annotations

from typing import Final, TypedDict

from src.caps.criterion_specs import CRITERIA_KEYS

# ---------------------------------------------------------------------------
# Allowed diagnostic values
# ---------------------------------------------------------------------------

QUALITY_LEVELS: Final[frozenset[str]] = frozenset({"laag", "middel", "hoog"})
"""The only values a diagnostics dimension may take. No numbers, no CAPS labels."""

JUDGEMENT_VALUES: Final[frozenset[str]] = frozenset({"strong", "mixed", "weak"})
"""The only values criterion_judgement may take — Qwen's single per-criterion
content-quality verdict. NOT a stoplight: Python maps it to a stoplight
deterministically in the merge layer (strong→green, mixed→yellow, weak→red),
applies guards, and only then assigns colours. Qwen never emits stoplights."""

# ---------------------------------------------------------------------------
# Required diagnostic dimensions per criterion
# ---------------------------------------------------------------------------

QUALITY_DIMENSIONS: Final[dict[str, tuple[str, ...]]] = {
    "beperking": (
        "concreteness",
        "research_support",
        "case_relevance",
        "impact_on_requirements",
    ),
    "stakeholders": (
        "stakeholder_relevance",
        "interest_quality",
        "influence_quality",
        "context_link",
    ),
    "requirements": (
        "concreteness",
        "testability",
        "relevance",
        "explanation_quality",
        "priority_logic",
        "classification_quality",
    ),
    "taalkeuze": (
        "explicit_choice",
        "target_group_fit",
        "consequence_quality",
        "design_decision_clarity",
    ),
    "security": (
        "measure_quality",
        "risk_link",
        "case_relevance",
        "technical_correctness",
        "explanation_quality",
    ),
}
"""Per-criterion diagnostic dimensions, copied verbatim from the v2 prompt's
output schema. Keys must equal CRITERIA_KEYS; quality_validator enforces both
the set of dimensions per criterion and that every value is in QUALITY_LEVELS."""

# A defensive assertion: the schema must cover exactly the CAPS criteria.
assert set(QUALITY_DIMENSIONS.keys()) == set(CRITERIA_KEYS), (
    "QUALITY_DIMENSIONS keys must match CAPS CRITERIA_KEYS"
)

# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


class CriterionQuality(TypedDict):
    """Quality diagnosis for one rubric criterion.

    criterion_judgement is Qwen's single roll-up content-quality verdict
    (strong/mixed/weak; see JUDGEMENT_VALUES) — the explicit field the merge
    layer maps to a per-criterion stoplight. diagnostics holds the per-dimension
    laag/middel/hoog verdicts. strengths, weaknesses, and manual_review_reason
    are short Dutch, non-student-facing bullet lists. manual_review is the
    per-criterion docent-focus flag.
    """

    criterion_judgement: str
    diagnostics: dict[str, str]
    strengths: list[str]
    weaknesses: list[str]
    manual_review: bool
    manual_review_reason: list[str]
    reason: str


class QualityDiagnostics(TypedDict):
    """Complete output of one quality-diagnosis run.

    document_id is set deterministically by the validator from the handoff —
    never trusted from the LLM. criteria is keyed by criterion_key in
    CRITERIA_KEYS order so the contract is stable.
    """

    document_id: str
    criteria: dict[str, CriterionQuality]
