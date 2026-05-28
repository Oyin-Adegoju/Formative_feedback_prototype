"""criterion_specs.py â rubric contract for CAPS: INFIRFS requirements assignment.

Defines the 5 assessment criteria as pure metadata: keys, labels, descriptions,
retrieval hints, count thresholds, and guidance notes.

No model calls. No PDF parsing. No scoring logic. No counting. Metadata only.

Architecture position:
  parser output â [anonymizer] â CAPS retrieval â CAPS checks â CAPS scoring
  This file is consumed by retrieval, checks, and scoring â not by the parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Criterion spec dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionSpec:
    """Metadata for one rubric criterion.

    Consumed by retrieval, checks, and scoring layers.
    Contains no logic â only what to look for and what thresholds apply.

    Fields without defaults are always required.
    Fields with defaults are optional and criterion-specific.
    """

    key: str
    """Stable machine key â matches CriterionResult.criterion_key in models.py."""

    label: str
    """Human-readable display name for output and feedback reports."""

    description: str
    """Wat dit criterium beoordeelt, in heldere taal voor docent en student."""

    is_blocker: bool
    """True â een ontbrekend/gedeeltelijk verdict activeert een rood stoplicht op documentniveau."""

    relevant_block_types: frozenset[str]
    """Parser block_type values that may carry evidence for this criterion.

    Uses str rather than BlockType Literal to stay decoupled from parser internals.
    Anonymized output preserves block_type, so this stays valid after anonymization.
    """

    heading_hints: tuple[str, ...]
    """Lowercase substrings to match against each entry in block.heading_path.

    Broad by design â retrieval narrows by scored relevance, not hard matching.
    """

    text_hints: tuple[str, ...]
    """Lowercase substrings to match against block.text.

    These are content signals, not definitive indicators. Retrieval uses them
    to score blocks; check functions do the actual judgement.
    """

    minimum_count: int | None = None
    """Minimaal aantal items voor een 'voldoende' verdict. None = aanwezigheidscriterium."""

    strong_from: int | None = None
    """Minimaal aantal items voor een 'goed' verdict. None = niet van toepassing."""

    notes: str = ""
    """Toelichting voor check- en retrieval-lagen: subtiliteiten, co-voorkomensregels,
    veelvoorkomende patronen en aandachtspunten buiten sleutelwoordmatching."""

    manual_review_trigger: str = ""
    """Condities die dit criterium markeren voor handmatige verificatie.
    Lege string betekent geen automatische trigger buiten de normale logica."""


