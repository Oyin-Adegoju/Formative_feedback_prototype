"""CAPS layer data contract.

Defines every typed structure that flows into or out of the CAPS judgement
engine, in four clear layers:

  1. Input contract  â what CAPS accepts (BlockDict, ParseReportDict)
  2. Evidence        â lightweight block pointers (EvidenceRef)
  3. Per-criterion   â one verdict per rubric item (CriterionResult)
  4. Document output â scorecard + full run result (CapsScorecard, CapsRunResult)

No scoring, retrieval, prompt, or anonymization logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, NotRequired, TypedDict

# ---------------------------------------------------------------------------
# Shared type aliases
# ---------------------------------------------------------------------------

CriterionStatus = Literal["missing", "partial", "sufficient", "strong"]
"""Verdict for one criterion."""

StoplightLabel = Literal["red", "yellow", "green"]
"""Document-level or per-criterion traffic-light label."""

InputSource = Literal["parser_direct", "anonymized"]
"""Tracks whether CAPS received raw parser output or anonymizer output."""
