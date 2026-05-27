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

# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


class TableMetaDict(TypedDict, total=False):
    """Structured table metadata produced by the parser for every table block.

    All fields are optional (total=False) so that a partially-populated dict
    from a future anonymizer still satisfies this type.  In practice the parser
    always populates all five fields.

    cells: rows Ã columns; individual cells may be None for empty/merged cells.
    index_cells: parser-detected pure-number cells as [row_idx, col_idx] pairs.
    """

    row_count: int
    column_count: int
    header_row: str | None
    cells: list[list[str | None]]
    index_cells: list[list[int]]


class BlockDict(TypedDict):
    """Minimal block shape CAPS reads from the input report.

    Both parser-direct JSON blocks and future anonymized blocks satisfy this
    contract â the anonymizer preserves structure, only text content changes.
    table_meta and token_estimate are optional: present in parser-direct output,
    may be absent or stripped in anonymized output.
    """

    block_id: str
    page_no: int
    block_type: str          # str rather than Literal â forward-compat with new types
    text: str
    heading_path: list[str]
    is_front_matter: bool
    is_appendix: bool
    table_meta: NotRequired[TableMetaDict | None]
    token_estimate: NotRequired[int]


class ParseReportDict(TypedDict):
    """Report envelope CAPS receives.

    Required fields match both parser.report.generate_report output and the
    future anonymized variant.  The optional fields are present in raw parser
    reports but may be stripped by the anonymizer â CAPS must not depend on them
    for judgement, only for metadata/logging.
    """

    doc_id: str
    source_name: str
    page_count: int
    block_count: int
    blocks: list[BlockDict]
    source_path: NotRequired[str]
    counts_by_type: NotRequired[dict[str, int]]
    text_quality: NotRequired[float]
    warnings: NotRequired[list[str]]
    
