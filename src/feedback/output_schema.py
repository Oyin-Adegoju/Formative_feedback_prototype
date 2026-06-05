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

