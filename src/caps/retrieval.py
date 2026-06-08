"""retrieval.py — CAPS candidate-block retrieval for rubric criteria.

Retrieves and ranks candidate evidence blocks from a parsed document report
for each CriterionSpec. This is the only responsibility of this file.

No scoring verdicts, no criterion judgement, no counting,
no blocker logic, no stoplight, no manual review decisions.

Architecture position:
    parser output → [anonymizer] → CAPS retrieval → CAPS checks → CAPS scoring

Operates on BlockDict objects (models.py contract), not on parser-internal
Block dataclasses. This keeps retrieval compatible with both parser-direct
output and future anonymized output without any redesign.

Scoring model (heuristic, deterministic):
    - Block type must be in relevant_block_types for a type-match bonus.
      Type match alone is not enough: a block must match at least one
      heading hint or text hint to appear in results at all.
    - Heading hints are the strongest signal: they match against
      heading_path entries and, for table blocks, against header_row.
    - Text hints are a moderate signal: they match against the full
      searchable text (block.text + table cells + header_row for tables).
    - Appendix blocks are demoted by a multiplier — secondary evidence.
    - Caption blocks are mildly demoted — supporting evidence only.
    - front_matter, noise, and template blocks are always excluded.

Output: dict[criterion_key, list[RetrievalHit]] — consumed by checks.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from src.caps.criterion_specs import CriterionSpec, INFIRFS_REQUIREMENTS_CRITERIA
from src.caps.models import BlockDict, ParseReportDict

# ---------------------------------------------------------------------------
# Score weights — tune here, not in scoring functions
# ---------------------------------------------------------------------------

_SCORE_TYPE_MATCH: Final[float] = 1.0
"""Bonus when block_type is in criterion.relevant_block_types."""

_SCORE_HEADING_HINT: Final[float] = 3.0
"""Per heading hint matched in heading_path entries or table header_row.

Table header_row is treated at heading-hint level because it structurally
labels what a table is about, analogous to a section heading.
"""

_SCORE_TEXT_HINT: Final[float] = 1.5
"""Per text hint matched anywhere in the block's searchable text."""

_APPENDIX_FACTOR: Final[float] = 0.4
"""Score multiplier for is_appendix=True blocks.

Appendix blocks are secondary evidence — valid but weaker than body content.
Not excluded entirely so checks.py can still inspect them when body evidence
is thin.
"""

_CAPTION_FACTOR: Final[float] = 0.6
"""Score multiplier for caption blocks.

Captions are not primary retrieval targets but may contain criterion-relevant
references (e.g. 'Figuur 3: stakeholder matrix'). Downweighted so body
content consistently ranks above captions, but not excluded.
"""

_DEFAULT_MAX_CANDIDATES: Final[int] = 20
"""Default maximum RetrievalHit objects returned per criterion."""

_EXCLUDED_TYPES: Final[frozenset[str]] = frozenset({"front_matter", "noise", "template"})
"""Block types that carry no rubric evidence and are never retrieved."""


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@dataclass
class RetrievalHit:
    """One candidate evidence block for a rubric criterion.

    Consumed by checks.py. Carries the full BlockDict so check functions
    can do deeper inspection (e.g. cell-level counting for stakeholders)
    without re-fetching the block from the report.

    score: heuristic retrieval score; higher means more relevant.
        Type-match alone is not enough to appear here — at least one
        heading hint or text hint must have matched.
    matched_heading_hints: heading hints that fired (via heading_path or
        table header_row). Empty when only text hints matched.
    matched_text_hints: text hints that fired in the block's searchable
        text. Empty when only heading hints matched.
    reasons: human-readable trace strings for debugging and explainability.
        Format: "signal:detail", e.g. "heading_hints:['stakeholder']".
    """

    block: BlockDict
    score: float
    matched_heading_hints: list[str] = field(default_factory=list)
    matched_text_hints: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


# Convenience alias used as the primary output type checks.py imports.
CriterionCandidates = dict[str, list[RetrievalHit]]


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase and strip a string for hint matching."""
    return text.lower().strip()


def _heading_path_text(block: BlockDict) -> str:
    """Join all heading_path entries into one normalized string.

    Entries are pipe-separated so a hint cannot accidentally span two
    adjacent path entries (e.g. hint 'b c' won't match 'a b' + 'c d').
    """
    return " | ".join(_normalize(entry) for entry in block["heading_path"])


def _table_header_text(block: BlockDict) -> str:
    """Extract table_meta.header_row as a normalized string.

    Returns an empty string when:
    - the block is not a table
    - table_meta is absent or None
    - header_row is None (parser could not detect a header row)
    """
    if block["block_type"] != "table":
        return ""
    tm = block.get("table_meta")
    if not tm:
        return ""
    header = tm.get("header_row")
    return _normalize(header) if header else ""


def _searchable_text(block: BlockDict) -> str:
    """Build the full normalized searchable string for a block.

    For table blocks: concatenates block.text, header_row, and all
    non-empty cells. Cells overlap with the canonical block.text (the
    parser already encodes them there), but explicit re-inclusion makes
    intent clear and is robust to future canonical-text format changes.

    For all other blocks: block.text normalized.
    """
    parts: list[str] = [_normalize(block["text"])]
    if block["block_type"] == "table":
        tm = block.get("table_meta")
        if tm:
            if tm.get("header_row"):
                parts.append(_normalize(tm["header_row"]))
            for row in tm.get("cells") or []:
                for cell in row:
                    if cell:
                        parts.append(_normalize(cell))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Block scoring
# ---------------------------------------------------------------------------


def _score_block(
    block: BlockDict,
    criterion: CriterionSpec,
) -> RetrievalHit | None:
    """Score one block against one criterion.

    Returns None when the block is excluded entirely (excluded type or
    front matter) or when it has no hint signal (heading or text).

    A block must match at least one heading hint or text hint to appear
    in results. Type-match alone is not sufficient — this prevents flooding
    results with every paragraph in the document just because paragraphs
    are in relevant_block_types.

    Scoring formula (before secondary-evidence factors):
        score = type_match_bonus
              + n_heading_hints × SCORE_HEADING_HINT
              + n_text_hints    × SCORE_TEXT_HINT

    Secondary-evidence factors (applied after, independently):
        - is_appendix=True  → score × APPENDIX_FACTOR (0.4)
        - block_type=caption → score × CAPTION_FACTOR  (0.6)
    """
    # --- hard exclusions ---
    if block["block_type"] in _EXCLUDED_TYPES:
        return None
    if block["is_front_matter"]:
        return None

    # --- heading hint matching ---
    # heading_path entries are the primary source; table header_row is
    # treated as an equivalent structural label for table blocks.
    heading_path = _heading_path_text(block)
    table_header = _table_header_text(block)

    matched_heading_hints: list[str] = []
    for hint in criterion.heading_hints:
        if hint in heading_path:
            matched_heading_hints.append(hint)
        elif table_header and hint in table_header:
            matched_heading_hints.append(hint)

    # --- text hint matching ---
    searchable = _searchable_text(block)
    matched_text_hints: list[str] = []
    for hint in criterion.text_hints:
        if hint in searchable:
            matched_text_hints.append(hint)

    # Require at least one signal — pure type-match blocks add noise.
    if not matched_heading_hints and not matched_text_hints:
        return None

    # --- compute score ---
    score: float = 0.0
    reasons: list[str] = []

    if block["block_type"] in criterion.relevant_block_types:
        score += _SCORE_TYPE_MATCH
        reasons.append(f"type:{block['block_type']}")

    if matched_heading_hints:
        score += len(matched_heading_hints) * _SCORE_HEADING_HINT
        reasons.append(f"heading_hints:{matched_heading_hints}")

    if matched_text_hints:
        score += len(matched_text_hints) * _SCORE_TEXT_HINT
        reasons.append(f"text_hints:{matched_text_hints}")

    # --- secondary-evidence demotions (applied independently) ---
    if block["is_appendix"]:
        score *= _APPENDIX_FACTOR
        reasons.append("appendix:downweighted")

    if block["block_type"] == "caption":
        score *= _CAPTION_FACTOR
        reasons.append("caption:downweighted")

    return RetrievalHit(
        block=block,
        score=round(score, 4),
        matched_heading_hints=matched_heading_hints,
        matched_text_hints=matched_text_hints,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Public retrieval functions
# ---------------------------------------------------------------------------


def retrieve_for_criterion(
    blocks: list[BlockDict],
    criterion: CriterionSpec,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
) -> list[RetrievalHit]:
    """Retrieve and rank candidate blocks for one criterion.

    Scores every block against the criterion, discards non-hits,
    and returns at most max_candidates results sorted by:
        1. score descending (highest relevance first)
        2. block_id ascending (lexicographic, for stability across runs)

    Args:
        blocks: All blocks from a ParseReportDict.
        criterion: The CriterionSpec to retrieve for.
        max_candidates: Upper bound on returned results.

    Returns:
        Deterministically ordered list of RetrievalHit.
        Empty list when no blocks match the criterion.
    """
    hits: list[RetrievalHit] = []
    for block in blocks:
        hit = _score_block(block, criterion)
        if hit is not None:
            hits.append(hit)

    hits.sort(key=lambda h: (-h.score, h.block["block_id"]))
    return hits[:max_candidates]


def retrieve_all_criteria(
    report: ParseReportDict,
    criteria: tuple[CriterionSpec, ...] = INFIRFS_REQUIREMENTS_CRITERIA,
    max_candidates: int = _DEFAULT_MAX_CANDIDATES,
) -> CriterionCandidates:
    """Retrieve and rank candidate blocks for all criteria in one document.

    Iterates over all criteria and calls retrieve_for_criterion for each.
    Every criterion in the input tuple is guaranteed to appear as a key
    in the result (with an empty list when no blocks match).

    Args:
        report: A ParseReportDict — parser-direct JSON or anonymized variant.
            Both satisfy the BlockDict contract and work without changes.
        criteria: Tuple of CriterionSpec to evaluate.
            Defaults to INFIRFS_REQUIREMENTS_CRITERIA (all 5 criteria).
        max_candidates: Upper bound on candidates per criterion.

    Returns:
        dict[criterion_key → list[RetrievalHit]], one entry per criterion.
        Suitable for direct consumption by checks.py.
    """
    blocks: list[BlockDict] = report["blocks"]
    return {
        criterion.key: retrieve_for_criterion(blocks, criterion, max_candidates)
        for criterion in criteria
    }
