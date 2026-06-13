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
"""Traffic-light label. The same type is used at two levels with two contracts:

Per-criterion (CriterionResult.stoplight), derived from status by the scoring
layer's mapping:
    strong              -> green
    sufficient          -> yellow
    missing | partial   -> red

Document-level (CapsRunResult.overall_stoplight), CAPS-only contract:
    red    -> one or more blocker criteria are missing or partial
    green  -> no blockers triggered
    yellow -> NOT produced by CAPS alone. All current criteria are blockers,
              so document-level is only ever red or green. Document-level
              yellow is reserved for the downstream Qwen content-quality stage.
"""

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
    page_count: NotRequired[int]
    block_count: int
    blocks: list[BlockDict]
    source_path: NotRequired[str]
    counts_by_type: NotRequired[dict[str, int]]
    text_quality: NotRequired[float]
    warnings: NotRequired[list[str]]
    
    # ---------------------------------------------------------------------------
# Evidence references
# ---------------------------------------------------------------------------


@dataclass
class EvidenceRef:
    """Lightweight pointer to one block that supports a criterion verdict.

    Deliberately holds no full Block object â only a stable reference.
    This keeps CAPS output identical whether the input was parser-direct
    or anonymized: the anonymizer preserves block_id, page_no, and block_type.

    text_snippet: first ~120 chars of block.text at evaluation time.
    Leave empty when no preview is needed or when operating on anonymized input.
    """

    block_id: str
    page_no: int
    block_type: str
    text_snippet: str = ""


# ---------------------------------------------------------------------------
# Per-criterion result
# ---------------------------------------------------------------------------


@dataclass
class CriterionResult:
    """CAPS verdict for one rubric criterion.

    Fields without defaults must always be provided; the rest are optional
    because not every criterion has a meaningful count.

    status is the authoritative verdict. stoplight is presentation-oriented
    and derived from status (strong->green, sufficient->yellow,
    missing|partial->red). Note: a per-criterion stoplight may be yellow even
    though the document-level CapsRunResult.overall_stoplight never is — see
    StoplightLabel for the two-level contract.

    evidence is the CAPS contract's evidence field, named consistently as
    "evidence" across models, checks, scoring, and caps. The downstream
    feedback layer later enriches these EvidenceRef pointers into
    EvidencePacket.evidence_items (a separate, richer type in a separate
    layer); the two names are intentionally not unified here.

    missing_signals: short English identifiers for signals that are absent and
        affect this criterion's verdict (e.g. "research_evidence", "stakeholder_count").
        Populated by checks.py; consumed by downstream Qwen diagnosis.
    """

    criterion_key: str            # matches CriterionSpec.key
    status: CriterionStatus       # authoritative verdict
    stoplight: StoplightLabel     # presentation label derived from status by the scoring layer
    is_blocker: bool              # mirrors CriterionSpec.is_blocker
    evidence: list[EvidenceRef] = field(default_factory=list)
    count: int | None = None      # found count for countable criteria (e.g. stakeholders)
    notes: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)

# ---------------------------------------------------------------------------
# Document-level scorecard
# ---------------------------------------------------------------------------


@dataclass
class CapsScorecard:
    """All criterion results for one document.

    results is keyed by criterion_key (e.g. "stakeholders", "requirements").
    hidden_score is populated by the scoring layer; None until scoring runs.
    """

    doc_id: str
    results: dict[str, CriterionResult] = field(default_factory=dict)
    hidden_score: int | None = None


# ---------------------------------------------------------------------------
# Run metadata
# ---------------------------------------------------------------------------


@dataclass
class CapsRunMeta:
    """Non-verdict metadata recorded for every CAPS run."""

    input_source: InputSource       # parser_direct | anonymized
    page_count: int
    block_count: int
    criteria_evaluated: list[str] = field(default_factory=list)
    caps_version: str = "0.1.0"


# ---------------------------------------------------------------------------
# Full CAPS run result
# ---------------------------------------------------------------------------


@dataclass
class CapsRunResult:
    """Top-level output of one CAPS evaluation.

    overall_stoplight: CAPS-only document verdict — "red" when any blocker is
        triggered, otherwise "green". CAPS alone never emits "yellow" at this
        level (all current criteria are blockers); document-level yellow is
        reserved for the downstream Qwen stage. See StoplightLabel.
    blockers_triggered: criterion_keys where is_blocker=True and status is
        "missing" or "partial". Populated by the scoring layer, not here.

    manual_review_required and manual_review_flags have been removed from CAPS
    ownership. Content-quality diagnosis and manual review flagging are now
    Qwen's responsibility in the downstream stage.
    """

    doc_id: str
    source_name: str
    scorecard: CapsScorecard
    overall_stoplight: StoplightLabel
    run_meta: CapsRunMeta
    blockers_triggered: list[str] = field(default_factory=list)

