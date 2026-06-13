"""handoff.py — stable CAPS→Qwen handoff contract (pre-Qwen).

Produces one compact, downstream-friendly structure per document that the
future Qwen quality-diagnosis stage can consume directly.  It merges two
already-existing artifacts without re-deriving anything:

    CapsRunResult            (document-level + per-criterion CAPS verdicts)
    dict[str, EvidencePacket] (per-criterion shaped evidence)
        → build_caps_handoff → CapsHandoff → to_dict → JSON

Architecture position:
    CAPS (retrieval → checks → scoring)  → CapsRunResult ┐
    packet_builder.build_evidence_packets → packets      ┘→ handoff → [Qwen]

Responsibility boundary (intentional):
    - CAPS is the single source of truth for every verdict field
      (status, stoplight, count, notes, missing_signals). These are copied
      verbatim from CriterionResult — never recomputed here.
    - The evidence-packet layer is the single source of truth for the SHAPE
      of evidence. Its EvidenceItem already carries the exact downstream
      fields, so this module re-exposes them under `evidence_items` unchanged.

This is the PRE-Qwen handoff. It is NOT the final merged CAPS+Qwen contract.
It deliberately contains ONLY objective CAPS outputs plus shaped evidence:
    - no manual_review / manual_review_required / manual_review_flags / reason
    - no qwen_diagnostics / qwen_strengths / qwen_weaknesses
    - no hidden_score (an internal CAPS scoring artifact, not a handoff field)

No retrieval, scoring, prompt, or LLM logic lives here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from src.caps.caps import CapsPipelineArtifacts
from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import CapsRunResult, CriterionStatus, StoplightLabel
from src.feedback.evidence import EvidenceItem, EvidencePacket
from src.feedback.packet_builder import build_evidence_packets

# ---------------------------------------------------------------------------
# Per-criterion handoff entry
# ---------------------------------------------------------------------------


@dataclass
class CriterionHandoff:
    """One criterion's CAPS verdict plus its shaped evidence.

    All verdict fields (status, stoplight, count, notes, missing_signals) are
    copied verbatim from the authoritative CriterionResult. evidence_items are
    reused unchanged from the criterion's EvidencePacket — the existing
    EvidenceItem already exposes the downstream-friendly fields:
        block_id, page_no, block_type, heading_path, excerpt,
        selection_reason, signal_class.
    """

    status: CriterionStatus
    stoplight: StoplightLabel
    count: int | None
    notes: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
    evidence_items: list[EvidenceItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Document-level handoff
# ---------------------------------------------------------------------------


@dataclass
class CapsHandoff:
    """Stable pre-Qwen handoff for one document.

    criteria is keyed by criterion_key in CRITERIA_KEYS order, so the contract
    is stable and every known criterion always appears.
    """

    document_id: str
    source_name: str
    overall_stoplight: StoplightLabel
    blockers_triggered: list[str] = field(default_factory=list)
    criteria: dict[str, CriterionHandoff] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_caps_handoff(
    caps_result: CapsRunResult,
    packets: Mapping[str, EvidencePacket],
) -> CapsHandoff:
    """Merge a CapsRunResult and its evidence packets into one CapsHandoff.

    CAPS is the source of truth for every verdict field; this function copies
    those fields and attaches the matching packet's evidence_items. It never
    re-judges, re-scores, or invents any value.

    Args:
        caps_result: The final document-level CAPS output.
        packets: dict[criterion_key → EvidencePacket] from build_evidence_packets.
            A criterion absent from this mapping simply gets no evidence_items.

    Returns:
        CapsHandoff with one entry per criterion in CRITERIA_KEYS order.
    """
    criteria: dict[str, CriterionHandoff] = {}

    for key in CRITERIA_KEYS:
        cr = caps_result.scorecard.results[key]
        pkt = packets.get(key)
        criteria[key] = CriterionHandoff(
            status=cr.status,
            stoplight=cr.stoplight,
            count=cr.count,
            notes=list(cr.notes),
            missing_signals=list(cr.missing_signals),
            evidence_items=list(pkt.evidence_items) if pkt else [],
        )

    return CapsHandoff(
        document_id=caps_result.doc_id,
        source_name=caps_result.source_name,
        overall_stoplight=caps_result.overall_stoplight,
        blockers_triggered=list(caps_result.blockers_triggered),
        criteria=criteria,
    )


def build_handoff_from_artifacts(artifacts: CapsPipelineArtifacts) -> CapsHandoff:
    """Convenience: build evidence packets and the handoff in one call.

    Use this when you already have a CapsPipelineArtifacts (from
    run_caps_with_artifacts) and want the handoff without manually calling
    build_evidence_packets first.
    """
    packets = build_evidence_packets(artifacts)
    return build_caps_handoff(artifacts.result, packets)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _evidence_item_to_dict(item: EvidenceItem) -> dict:
    """Serialise one EvidenceItem to its stable downstream dict shape.

    The first seven fields are the original stable contract. The remaining
    fields are the rule-based enrichment exposed for the downstream Qwen stage;
    they are always emitted (empty when not populated) so the shape stays stable
    across runs and documents.
    """
    return {
        "block_id": item.block_id,
        "page_no": item.page_no,
        "block_type": item.block_type,
        "heading_path": list(item.heading_path),
        "excerpt": item.excerpt,
        "selection_reason": item.selection_reason,
        "signal_class": item.signal_class,
        # --- rule-based enrichment (always present; empty when not derivable) ---
        "focused_excerpt": item.focused_excerpt,
        "matched_signals": list(item.matched_signals),
        "criterion_subtype": item.criterion_subtype,
        "classification_source": item.classification_source,
        "evidence_strength": item.evidence_strength,
        "matched_row_count": item.matched_row_count,
        "matched_row_ids": list(item.matched_row_ids),
        "local_section_label": item.local_section_label,
        "context_warning": item.context_warning,
    }


def to_dict(handoff: CapsHandoff) -> dict:
    """Serialise a CapsHandoff to a plain JSON-ready dict.

    Key order is fixed here so the emitted contract is stable across runs.
    """
    return {
        "document_id": handoff.document_id,
        "source_name": handoff.source_name,
        "overall_stoplight": handoff.overall_stoplight,
        "blockers_triggered": list(handoff.blockers_triggered),
        "criteria": {
            key: {
                "status": c.status,
                "stoplight": c.stoplight,
                "count": c.count,
                "notes": list(c.notes),
                "missing_signals": list(c.missing_signals),
                "evidence_items": [
                    _evidence_item_to_dict(it) for it in c.evidence_items
                ],
            }
            for key, c in handoff.criteria.items()
        },
    }
