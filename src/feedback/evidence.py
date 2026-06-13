"""evidence.py — typed schema for per-criterion evidence packets.

Evidence packets sit between the CAPS judgement layer and the future
feedback writer.  They provide curated, compact evidence that supports
(or explains the absence of) each CAPS verdict — without passing the
full anonymised document to the feedback step.

Architecture position:
    CAPS (retrieval → checks → scoring)
    → build_evidence_packets          ← packet_builder.py
    → [future feedback writer]

No logic here — only typed shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Signal classification
# ---------------------------------------------------------------------------

SignalClass = Literal["positive", "weak", "absent_marker"]
"""
positive      – block actively supports the CAPS verdict for this criterion.
                Contains at least one clear signal that CAPS used in its
                judgement (MoSCoW cell, limitation term, concrete mechanism, …).

weak          – block carries some criterion signal but is thin, generic,
                or ambiguous.  Present in the packet to give context, but
                should not be treated as strong supporting evidence.

absent_marker – a heading block that signals a section was expected but has
                no substantive content block below it.  Use when the student
                named the section (e.g. "Security") without populating it.
                Useful for formulating improvement suggestions.
"""


EvidenceStrength = Literal["strong", "moderate", "thin", "absent"]
"""Compact, rule-based strength of one evidence item (derived, not judged).

strong   – positive item that is dense: a table with several matched rows, or
           a block where multiple distinct signals fired.
moderate – positive item with a single clear signal.
thin     – a `weak` signal_class item (present for context only).
absent   – an `absent_marker` item (section named but not populated).

This is a presentation convenience for the downstream Qwen stage. It is derived
deterministically from signal_class + content density in packet_builder; it is
NOT a CAPS verdict and never overrides one.
"""


# ---------------------------------------------------------------------------
# Evidence item — one curated block
# ---------------------------------------------------------------------------


@dataclass
class EvidenceItem:
    """One curated block of evidence for a rubric criterion.

    Carries just enough to let a feedback writer refer to the source
    location and understand the content — without the full document.

    heading_path mirrors BlockDict.heading_path from the parser/anonymiser.
    excerpt is pre-shaped (table rows / trimmed paragraph), ready to include
    in a prompt without further processing.

    Enrichment fields (everything below signal_class) are OPTIONAL, additive,
    and rule-based. They expose, explicitly and per item, the local signals the
    selection layer already observed — so the downstream Qwen stage does not
    have to re-parse free text. Every enrichment field has a neutral empty
    default and is only populated when it can be filled honestly from real
    block content. None of them is a CAPS verdict.

    focused_excerpt   – short, targeted snippet derived from the SAME block as
                        `excerpt`, cleaned down to the rows/sentence that carry
                        the matched signal. Empty when no tighter snippet helps.
    matched_signals   – rule-based signal names that fired for this item, e.g.
                        ["moscow:must", "moscow:should", "req_id"] or
                        ["belang", "invloed"]. Never invented.
    criterion_subtype – local content sub-label, e.g. functional / non_functional
                        / use_case / constraint (requirements), limitation /
                        research (beperking), choice / consequence (taalkeuze),
                        concrete / generic (security), table / text (stakeholders).
    classification_source – where criterion_subtype came from: "heading_path",
                        "row_id_prefix", "block_text_section_switch", "block_type".
    evidence_strength – compact strength label (see EvidenceStrength).
    matched_row_count – relevant row count for table evidence (e.g. MoSCoW rows
                        or stakeholder rows). None for non-table or when unknown.
    matched_row_ids   – requirement IDs recognised in the block (FR01, NFR03,
                        UC02), normalised and de-duplicated. Empty when none.
    local_section_label – a section label detected INSIDE the block text that
                        differs from a stale heading_path (carryover guard).
    context_warning   – short, honest parser/selection caveat, e.g. when the
                        heading_path looks stale relative to the block content.
    """

    block_id: str
    page_no: int
    block_type: str
    heading_path: list[str]
    excerpt: str
    selection_reason: str
    signal_class: SignalClass
    # --- enrichment (optional, rule-based, may be empty) ---
    focused_excerpt: str = ""
    matched_signals: list[str] = field(default_factory=list)
    criterion_subtype: str = ""
    classification_source: str = ""
    evidence_strength: EvidenceStrength = "moderate"
    matched_row_count: int | None = None
    matched_row_ids: list[str] = field(default_factory=list)
    local_section_label: str = ""
    context_warning: str = ""


# ---------------------------------------------------------------------------
# Evidence packet — one criterion
# ---------------------------------------------------------------------------


@dataclass
class EvidencePacket:
    """Curated evidence for one rubric criterion.

    Produced by build_evidence_packets (packet_builder.py) from a
    CapsPipelineArtifacts object.  Consumed by the future feedback writer.

    CAPS is the source of truth for the verdict.  The packet supplements
    that verdict with the actual content that drove it (evidence_items) and
    a list of expected-but-absent signals (missing_signals) that help
    formulate improvement suggestions.

    notes is copied verbatim from CriterionResult.notes.

    Manual-review flagging is intentionally NOT carried on this packet. CAPS
    no longer owns manual review; deciding whether a docent must verify a
    criterion is deferred to the downstream Qwen stage. Until that stage
    exists, the packet simply omits any manual-review field.
    """

    criterion_key: str
    notes: list[str]
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
