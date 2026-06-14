"""handoff.py — stable CAPS→Qwen handoff contract (pre-Qwen).

Produces one compact, downstream-friendly structure per document that the
Qwen quality-diagnosis stage consumes directly.  It merges two already-existing
artifacts without re-deriving anything:

    CapsRunResult            (document-level CAPS run — only extraction read here)
    dict[str, EvidencePacket] (per-criterion shaped evidence)
        → build_caps_handoff → CapsHandoff → to_dict → JSON

Architecture position:
    CAPS (retrieval → checks → scoring)  → CapsRunResult ┐
    packet_builder.build_evidence_packets → packets      ┘→ handoff → [Qwen]

Responsibility boundary (intentional — this is the honesty boundary):
    - This is an EXTRACTION handoff. CAPS, at this stage, only matches signals,
      counts, and detects sections — it does NOT understand content. So the
      handoff deliberately carries ONLY objective/extractive fields:
        count, notes, missing_signals, evidence_items.
    - It does NOT expose the CAPS scoring verdicts. The following are computed
      internally by scoring.py for CAPS' own use but are NOT part of this
      pre-Qwen contract, because they would assert content-quality judgements
      the extraction layer cannot honestly make yet:
        per-criterion status      (missing/partial/sufficient/strong)
        per-criterion stoplight    (red/yellow/green)
        document overall_stoplight (red/green)
        document blockers_triggered (the document-level mirror of status)
      Quality judgement is the Qwen stage's job; the document-level stoplight is
      assigned later, in the merge layer, AFTER Qwen output exists.
    - The evidence-packet layer is the single source of truth for the SHAPE
      of evidence. Its EvidenceItem already carries the exact downstream fields,
      so this module re-exposes them under `evidence_items` unchanged.

This is the PRE-Qwen handoff. It is NOT the final merged CAPS+Qwen contract.
It deliberately contains ONLY objective CAPS extraction plus shaped evidence:
    - no status / stoplight / overall_stoplight / blockers_triggered
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
from src.caps.models import CapsRunResult
from src.feedback.evidence import EvidenceItem, EvidencePacket
from src.feedback.packet_builder import build_evidence_packets

# ---------------------------------------------------------------------------
# Per-criterion handoff entry
# ---------------------------------------------------------------------------


@dataclass
class CriterionHandoff:
    """One criterion's objective CAPS extraction plus its shaped evidence.

    All fields are extraction/coverage observations copied verbatim from the
    authoritative CriterionResult — NOT quality verdicts:
        count           — found count for countable criteria (or None)
        notes           — extraction/coverage observations (signals found/absent,
                          counts vs thresholds, section/ambiguity warnings)
        missing_signals — expected signals that were not detected (coverage gaps)
    evidence_items are reused unchanged from the criterion's EvidencePacket — the
    existing EvidenceItem already exposes the downstream-friendly fields
    (block_id, page_no, block_type, heading_path, excerpt, selection_reason,
    signal_class, plus rule-based enrichment).

    The CAPS scoring verdicts (status, stoplight) are intentionally NOT carried
    here — see the module docstring.
    """

    count: int | None
    notes: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
    evidence_items: list[EvidenceItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Document-level handoff
# ---------------------------------------------------------------------------


@dataclass
class CapsHandoff:
    """Stable pre-Qwen extraction handoff for one document.

    criteria is keyed by criterion_key in CRITERIA_KEYS order, so the contract
    is stable and every known criterion always appears.

    Carries no document-level verdict (no overall_stoplight, no
    blockers_triggered): document-level signals are decided downstream, after
    Qwen quality output exists.
    """

    document_id: str
    source_name: str
    criteria: dict[str, CriterionHandoff] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def build_caps_handoff(
    caps_result: CapsRunResult,
    packets: Mapping[str, EvidencePacket],
) -> CapsHandoff:
    """Merge a CapsRunResult and its evidence packets into one CapsHandoff.

    Copies ONLY the objective extraction fields (count, notes, missing_signals)
    from each CriterionResult and attaches the matching packet's evidence_items.
    It never copies, re-judges, re-scores, or invents any verdict. The CAPS
    status/stoplight and the document overall_stoplight/blockers_triggered are
    deliberately left out of the pre-Qwen contract.

    Args:
        caps_result: The final document-level CAPS output (verdicts ignored here).
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
            count=cr.count,
            notes=list(cr.notes),
            missing_signals=list(cr.missing_signals),
            evidence_items=list(pkt.evidence_items) if pkt else [],
        )

    return CapsHandoff(
        document_id=caps_result.doc_id,
        source_name=caps_result.source_name,
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
    Carries extraction only — no status, stoplight, overall_stoplight, or
    blockers_triggered.
    """
    return {
        "document_id": handoff.document_id,
        "source_name": handoff.source_name,
        "criteria": {
            key: {
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
