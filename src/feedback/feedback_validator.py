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

