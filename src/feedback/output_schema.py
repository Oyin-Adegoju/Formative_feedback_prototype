"""output_schema.py — typed output contract for the feedback generation layer.

Defines the exact JSON structure that feedback_builder.py produces and
feedback_validator.py enforces.

No logic, no LLM calls, no imports from other feedback modules.
Only types, constants, and the single source of truth for the output shape.

Architecture position:
    CapsRunResult → feedback_builder → [llm_client] → feedback_validator → FeedbackResult
                                                                            ^^^^^^^^^^^^^^
                                                                            defined here
"""

from __future__ import annotations

from typing import Final, TypedDict

from src.caps.models import StoplightLabel

# ---------------------------------------------------------------------------
# Disclaimer
# ---------------------------------------------------------------------------

DISCLAIMER: Final[str] = "Dit is formatieve feedback en geen officiële beoordeling."
"""Fixed disclaimer text injected by feedback_validator, never written by the LLM."""
# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


class CriterionFeedback(TypedDict):
    """Feedback for one rubric criterion inside FeedbackResult.feedback."""

    criterium: str
    """Criterion key, e.g. 'stakeholders'. Must match a known CAPS criterion key."""

    observatie: str
    """LLM-written observation for this criterion. Student-facing, no scores."""

    evidence_ref: list[str]
    """Block IDs from EvidenceRef that support this observation.

    Only block IDs that appear in the CAPS scorecard evidence are valid.
    feedback_validator enforces this.
    """
