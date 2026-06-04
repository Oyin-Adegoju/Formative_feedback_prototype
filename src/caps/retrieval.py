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
