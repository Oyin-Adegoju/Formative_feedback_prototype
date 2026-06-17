"""Tests for the full-content extraction + handoff builder (src/feedback/full_content.py).

Full content = per criterion the UNTRIMMED text of every block whose deepest
heading belongs to that criterion's section family, plus a keyword fallback for
taalkeuze/security. No trimming, no budget, no table-row cap.
"""

from __future__ import annotations

from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.full_content import build_full_content_handoff, collect_for_criterion


def _block(block_id, block_type, text, heading_path, *, is_front_matter=False, table_meta=None):
    b = {
        "block_id": block_id, "page_no": 1, "block_type": block_type,
        "text": text, "heading_path": heading_path,
        "is_front_matter": is_front_matter, "is_appendix": False,
    }
    if table_meta is not None:
        b["table_meta"] = table_meta
    return b


_LONG_BEP = (
    "Wij richten ons op slechtziende gebruikers met een visuele beperking. "
    "Uit deskresearch blijkt dat deze doelgroep met beperking beperkte toegang heeft "
    "tot online winkels, en daarom besteden wij extra aandacht aan toegankelijkheid. "
) * 3

_REPORT = {
    "doc_id": "fc-001", "source_name": "fc.pdf", "page_count": 3,
    "blocks": [
        _block("b1", "paragraph", _LONG_BEP, ["Beperking"]),
        _block("b2", "paragraph", "Aanvullend literatuuronderzoek onderbouwt de afbakening.",
               ["Onderzoek naar web gebruikers"]),
        _block("b3", "paragraph",
               "Wachtwoorden worden gehasht met bcrypt en HTTPS beschermt verbindingen.",
               ["Security"]),
        _block("b4", "paragraph",
               "De webshop wordt in het Nederlands aangeboden voor het bereik.",
               ["Inleiding"]),  # taalkeuze keyword fallback (neutral heading)
        _block("fm", "front_matter", "Voorblad", ["Voorblad"], is_front_matter=True),
        _block("noise", "paragraph", "Een webshop verkoopt producten.", ["Inleiding"]),
    ],
}


def test_handoff_has_all_criteria_and_doc_identity():
    h = build_full_content_handoff(_REPORT)
    assert h["document_id"] == "fc-001"
    assert h["source_name"] == "fc.pdf"
    assert set(h["criteria"].keys()) == set(CRITERIA_KEYS)
    for c in h["criteria"].values():
        assert c["count"] is None
        assert isinstance(c["evidence_items"], list)


def test_section_blocks_are_full_text_untrimmed():
    h = build_full_content_handoff(_REPORT)
    bep = h["criteria"]["beperking"]["evidence_items"]
    ids = {it["block_id"] for it in bep}
    assert {"b1", "b2"} <= ids  # both beperking-section blocks present
    b1 = next(it for it in bep if it["block_id"] == "b1")
    # excerpt is the FULL normalized text (not trimmed to a few hundred chars).
    assert len(b1["excerpt"]) >= len(" ".join(_LONG_BEP.split())) - 2
    # Lean shape: section items carry no metadata boilerplate.
    assert "classification_source" not in b1
    assert "context_warning" not in b1
    assert "signal_class" not in b1 and "evidence_strength" not in b1


def test_taalkeuze_keyword_fallback_is_tagged():
    h = build_full_content_handoff(_REPORT)
    taal = h["criteria"]["taalkeuze"]["evidence_items"]
    assert any(it["block_id"] == "b4" for it in taal), "language keyword block should be admitted"
    b4 = next(it for it in taal if it["block_id"] == "b4")
    assert b4["classification_source"] == "keyword"
    assert b4["context_warning"]  # honest 'judge relevance yourself' warning


def test_neutral_and_front_matter_blocks_excluded():
    h = build_full_content_handoff(_REPORT)
    all_ids = {it["block_id"] for c in h["criteria"].values() for it in c["evidence_items"]}
    assert "fm" not in all_ids          # front matter excluded
    assert "noise" not in all_ids       # neutral, no keyword → excluded everywhere


def test_collect_for_criterion_document_order():
    blocks = _REPORT["blocks"]
    bep = collect_for_criterion(blocks, "beperking")
    order = [it["block_id"] for it in bep]
    assert order == sorted(order, key=lambda x: [b["block_id"] for b in blocks].index(x))
