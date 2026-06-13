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
    """

    block_id: str
    page_no: int
    block_type: str
    heading_path: list[str]
    excerpt: str
    selection_reason: str
    signal_class: SignalClass


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
