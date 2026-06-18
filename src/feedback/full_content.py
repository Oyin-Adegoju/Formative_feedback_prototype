"""full_content.py — FULL per-criterion document text extraction (no trimming).

The "full content" evidence strategy: instead of the strict, budget-trimmed
packet-builder evidence, give the downstream stage the UNTRIMMED text of every
block that belongs to a criterion. A larger model is then not starved of context
by the heading-only gate and judges relevance itself.

What "full content per criterion" means
---------------------------------------
For each criterion, in document order, the untrimmed text of:
  1. every block whose DEEPEST heading belongs to that criterion's section family
     (section_family.primary_families — the same admission authority CAPS uses), AND
  2. for taalkeuze and security ONLY: any block elsewhere carrying a relevant
     CHOICE / security keyword (these two criteria rarely have a dedicated heading
     in this corpus). Such blocks are tagged source="keyword" so the model judges
     relevance itself.

Structurally-empty blocks (front matter, parser noise/templates) are excluded.
No excerpt trimming, no budget cap, no tiling, no table-row cap.

This module is LLM-free and dependency-light. The CAPS handoff builder
(build_full_content_handoff) turns this extraction into a standard handoff whose
evidence excerpts carry the full per-criterion text; the quality (v3) and feedback
(v5) stages then consume that handoff like any other.
"""

from __future__ import annotations

from typing import Final

from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.packet_builder import (
    _SEC_CONCRETE,
    _SEC_GENERIC,
    _TAAL_CHOICE,
    _TAAL_CHOICE_STRICT,
    _has_token,
)
from src.feedback.section_family import primary_families

# Block types carrying no rubric evidence — mirrors retrieval._EXCLUDED_TYPES.
_EXCLUDED_TYPES: Final[frozenset[str]] = frozenset({"front_matter", "noise", "template"})

# Keyword-supplement term sets for the two heading-less criteria. taalkeuze admits
# on CHOICE words only — consequence words (bereik, internationaal...) are too
# generic and would drag whole unrelated tables in. A real consequence discussion
# almost always sits in the same block as the choice itself.
_KEYWORD_TERMS: Final[dict[str, frozenset[str]]] = {
    "taalkeuze": _TAAL_CHOICE_STRICT | _TAAL_CHOICE,
    "security": _SEC_CONCRETE | _SEC_GENERIC,
}


def _searchable(block: dict) -> str:
    """Block text plus table header/cells — for keyword scanning only."""
    parts: list[str] = [block["text"]]
    tm = block.get("table_meta")
    if tm:
        if tm.get("header_row"):
            parts.append(tm["header_row"])
        for row in tm.get("cells") or []:
            parts.extend(c for c in row if c)
    return " ".join(parts)


def _render_block_text(block: dict) -> str:
    """Full, untrimmed display text: table as header + rows, else normalized text."""
    tm = block.get("table_meta")
    if block["block_type"] == "table" and tm and tm.get("cells"):
        lines: list[str] = []
        if tm.get("header_row"):
            lines.append(tm["header_row"].strip())
        for row in tm["cells"]:
            cells = [c.strip() for c in row if c]
            if cells:
                lines.append(" | ".join(cells))
        return "\n".join(lines)
    return " ".join(block["text"].split())


def collect_for_criterion(blocks: list[dict], key: str) -> list[dict]:
    """Provenance-tagged blocks for one criterion, in document order.

    source = "section" (heading family) or "keyword" (out-of-section keyword hit,
    taalkeuze/security only). De-duplicated by block_id; section wins. Excludes
    front matter / noise / template blocks and structurally-empty blocks.
    """
    fam_ids = {
        b["block_id"] for b in blocks if key in primary_families(b["heading_path"])
    }
    terms = _KEYWORD_TERMS.get(key)
    out: list[dict] = []
    for b in blocks:
        if b.get("is_front_matter") or b["block_type"] in _EXCLUDED_TYPES:
            continue
        bid = b["block_id"]
        if bid in fam_ids:
            source = "section"
        elif terms and any(_has_token(_searchable(b), t) for t in terms):
            source = "keyword"
        else:
            continue
        rendered = _render_block_text(b)
        if not rendered.strip():
            continue
        out.append({
            "block_id": bid,
            "page_no": b["page_no"],
            "block_type": b["block_type"],
            "heading_path": list(b["heading_path"]),
            "source": source,
            "text": rendered,
        })
    return out


def extract_full_content(report: dict) -> dict[str, list[dict]]:
    """Per-criterion provenance-tagged content for a full anonymized report."""
    blocks = report.get("blocks") or []
    return {key: collect_for_criterion(blocks, key) for key in CRITERIA_KEYS}


# ---------------------------------------------------------------------------
# Full-content CAPS handoff
# ---------------------------------------------------------------------------

_KEYWORD_WARNING: Final[str] = (
    "trefwoord-treffer buiten een eigen sectie — beoordeel de relevantie zelf"
)


def _full_content_item(block: dict) -> dict:
    """Map one collected (full-text) block to a LEAN handoff evidence item.

    Only fields that carry information are emitted: block_id, page_no, block_type,
    heading_path and the FULL excerpt. The trimmed packet-builder's metadata
    (signal_class, evidence_strength, focused_excerpt, matched_signals, …) is
    deliberately omitted — for full content it would be constant/empty boilerplate
    that (a) bloats both LLM prompts past the context window and (b) misleads the
    quality stage (every block would falsely read as "positive"/"strong"). The
    model judges purely on the text, exactly like the proven v1 full-content run.
    Out-of-section keyword hits keep a marker + warning so relevance is judged.
    """
    item = {
        "block_id": block["block_id"],
        "page_no": block["page_no"],
        "block_type": block["block_type"],
        "heading_path": list(block["heading_path"]),
        "excerpt": block["text"],                       # FULL, untrimmed
    }
    if block["source"] == "keyword":
        item["classification_source"] = "keyword"
        item["context_warning"] = _KEYWORD_WARNING
    return item


def build_full_content_handoff(report: dict) -> dict:
    """Build a CAPS handoff where each criterion carries the FULL section text.

    Same handoff JSON shape as src/feedback/handoff.to_dict, so the downstream
    quality + merge + feedback stages consume it unchanged — but the evidence is
    untrimmed (every in-section block, full text + full tables, plus the
    taalkeuze/security keyword fallback) instead of the budget-trimmed packets.

    `count` stays None (not used downstream). `notes` describe coverage honestly.
    """
    per_criterion = extract_full_content(report)
    doc_id = report.get("doc_id") or report.get("document_id") or ""
    source_name = report.get("source_name", "")

    criteria: dict[str, dict] = {}
    for key in CRITERIA_KEYS:
        blocks = per_criterion[key]
        items = [_full_content_item(b) for b in blocks]
        if items:
            notes = [f"Volledige in-sectie inhoud meegegeven ({len(items)} blok(ken), geen afkapping)."]
        else:
            notes = ["Geen relevante tekst voor dit criterium in het document gevonden."]
        criteria[key] = {
            "count": None,
            "notes": notes,
            "missing_signals": [],
            "evidence_items": items,
        }

    return {"document_id": doc_id, "source_name": source_name, "criteria": criteria}
