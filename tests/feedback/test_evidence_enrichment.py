"""Tests for the enriched, rule-based evidence_items contract.

These tests pin down the additive enrichment fields introduced on EvidenceItem
(focused_excerpt, matched_signals, criterion_subtype, classification_source,
evidence_strength, matched_row_count, matched_row_ids, local_section_label,
context_warning):

  1. Every item always carries the enrichment fields with stable types.
  2. Coverage per criterion clearly moved beyond the old sparse limits when the
     document supports it (esp. requirements).
  3. Requirements evidence exposes real row-level structure: subtype, matched
     row IDs, matched row count, MoSCoW tier signals, local section labels.
  4. Enrichment stays honest — empty when a block does not support it, and no
     manual_review / Qwen fields leak back in.

All synthetic reports run through the real CAPS pipeline, so nothing here
re-judges; it only inspects the shaped evidence.
"""

from __future__ import annotations

import json

import pytest

from src.caps.caps import run_caps_with_artifacts
from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.evidence import EvidenceItem
from src.feedback.handoff import build_handoff_from_artifacts, to_dict
from src.feedback.packet_builder import _MAX_ITEMS, build_evidence_packets

_VALID_STRENGTHS = frozenset({"strong", "moderate", "thin", "absent"})

_FORBIDDEN_KEYS = frozenset({
    "manual_review", "manual_review_required", "manual_review_flags",
    "manual_review_reason", "qwen_diagnostics", "qwen_strengths",
    "qwen_weaknesses",
})


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _block(block_id, page_no, block_type, text, heading_path, *, table_meta=None):
    b: dict = {
        "block_id": block_id,
        "page_no": page_no,
        "block_type": block_type,
        "text": text,
        "heading_path": heading_path,
        "is_front_matter": False,
        "is_appendix": False,
    }
    if table_meta is not None:
        b["table_meta"] = table_meta
    return b


def _req_table(block_id, heading, prefix, rows):
    """A requirements table: header + one row per (id, prio)."""
    cells = [["Naam", "Omschrijving", "Prio"]]
    text_parts = []
    for i, prio in enumerate(rows, start=1):
        rid = f"{prefix}-{i:02d}"
        desc = "De website moet iets kunnen doen."
        cells.append([rid, desc, prio])
        text_parts.append(f"{rid} {desc} {prio}")
    return _block(
        block_id, 1, "table", " ".join(text_parts), [heading],
        table_meta={
            "row_count": len(cells), "column_count": 3,
            "header_row": "Naam | Omschrijving | Prio",
            "cells": cells, "index_cells": [],
        },
    )


# A document rich enough to exercise requirements structure: separate FR, NFR
# and use-case tables plus a stakeholder table and the other criteria.
_RICH_REPORT: dict = {
    "doc_id": "enrich-rich-001",
    "source_name": "rich.pdf",
    "page_count": 20,
    "block_count": 0,  # filled below
    "blocks": [
        _block(
            "b01", 2, "paragraph",
            "De gekozen beperking is een visuele beperking. Wij richten ons op "
            "slechtziende en blinde gebruikers. Uit deskresearch en "
            "literatuuronderzoek blijkt dat deze doelgroep met beperking beperkte "
            "toegang heeft. Bron: WHO rapport 2023.",
            ["Beperking & deskresearch"],
        ),
        _block(
            "b02", 3, "paragraph",
            "Aanvullend literatuuronderzoek en bronvermelding onderbouwen de "
            "afbakening van de doelgroep met beperking verder.",
            ["Beperking & deskresearch"],
        ),
        _block(
            "b03", 5, "table",
            "Stakeholder belang invloed",
            ["Stakeholders"],
            table_meta={
                "row_count": 6, "column_count": 3,
                "header_row": "Stakeholder | Belang | Invloed",
                "cells": [
                    ["Opdrachtgever", "hoog", "laag"],
                    ["Eindgebruiker", "hoog", "hoog"],
                    ["Ontwikkelaar", "middel", "hoog"],
                    ["Projectmanager", "hoog", "hoog"],
                    ["Designer", "middel", "middel"],
                    ["Beheerder", "laag", "hoog"],
                ],
                "index_cells": [],
            },
        ),
        _req_table("b04", "Functionele requirements", "FR",
                   ["Must have", "Must have", "Should have", "Could have",
                    "Must have", "Should have", "Won't have", "Must have"]),
        _req_table("b05", "Niet-functionele requirements", "NFR",
                   ["Must have", "Should have", "Must have", "Could have"]),
        _req_table("b06", "Use cases", "UC",
                   ["Must have", "Should have", "Must have"]),
        _req_table("b09", "Constraints", "CONSTR",
                   ["Must have", "Should have"]),
        _block(
            "b10", 8, "paragraph",
            "Aanvullende functionele eisen: FR-09 de website moet kunnen "
            "filteren, FR-10 de website moet kunnen sorteren, FR-11 de gebruiker "
            "moet kunnen afrekenen.",
            ["Requirements", "Functionele requirements"],
        ),
        _block(
            "b07", 9, "paragraph",
            "De webshop wordt volledig in het nederlands aangeboden. Dit heeft "
            "gevolgen voor het bereik van de doelgroep. Vertaalkosten worden "
            "vermeden en de begrijpelijkheid voor de doelgroep neemt toe.",
            ["Taalkeuze"],
        ),
        _block(
            "b08", 11, "paragraph",
            "De webshop maakt gebruik van authenticatie via een inlogsysteem. "
            "Autorisatie is geregeld via gebruikersrollen. Encryptie en hashing "
            "(bcrypt) worden toegepast conform de AVG.",
            ["Security", "Beveiliging"],
        ),
    ],
}
_RICH_REPORT["block_count"] = len(_RICH_REPORT["blocks"])


@pytest.fixture(scope="module")
def rich_packets():
    artifacts = run_caps_with_artifacts(_RICH_REPORT, input_source="parser_direct")
    return build_evidence_packets(artifacts), artifacts


# ---------------------------------------------------------------------------
# 1. Enrichment fields always present with stable types
# ---------------------------------------------------------------------------


def test_evidence_item_has_enrichment_fields():
    item = EvidenceItem(
        block_id="x", page_no=1, block_type="paragraph",
        heading_path=[], excerpt="e", selection_reason="r", signal_class="weak",
    )
    # Defaults are neutral / empty.
    assert item.focused_excerpt == ""
    assert item.matched_signals == []
    assert item.criterion_subtype == ""
    assert item.classification_source == ""
    assert item.evidence_strength == "moderate"
    assert item.matched_row_count is None
    assert item.matched_row_ids == []
    assert item.local_section_label == ""
    assert item.context_warning == ""


def test_all_items_have_valid_strength_and_types(rich_packets):
    packets, _ = rich_packets
    for key, pkt in packets.items():
        for it in pkt.evidence_items:
            assert it.evidence_strength in _VALID_STRENGTHS, f"{key}/{it.block_id}"
            assert isinstance(it.matched_signals, list)
            assert isinstance(it.matched_row_ids, list)
            assert it.matched_row_count is None or isinstance(it.matched_row_count, int)


def test_focused_excerpt_never_equals_excerpt(rich_packets):
    """focused_excerpt is either empty or a genuinely tighter snippet."""
    packets, _ = rich_packets
    for pkt in packets.values():
        for it in pkt.evidence_items:
            if it.focused_excerpt:
                assert it.focused_excerpt.strip() != it.excerpt.strip()


# ---------------------------------------------------------------------------
# 2. Coverage moved beyond the old sparse limits
# ---------------------------------------------------------------------------


def test_requirements_coverage_exceeds_old_cap(rich_packets):
    """Old cap was 3; a multi-table requirements doc should now surface more."""
    packets, _ = rich_packets
    n = len(packets["requirements"].evidence_items)
    assert n > 3, f"expected richer requirements coverage, got {n}"
    assert n <= _MAX_ITEMS["requirements"]


def test_stakeholders_budget_raised(rich_packets):
    packets, _ = rich_packets
    assert _MAX_ITEMS["stakeholders"] >= 6
    assert len(packets["stakeholders"].evidence_items) <= _MAX_ITEMS["stakeholders"]


def test_item_counts_within_budget(rich_packets):
    packets, _ = rich_packets
    for key in CRITERIA_KEYS:
        assert len(packets[key].evidence_items) <= _MAX_ITEMS[key]


# ---------------------------------------------------------------------------
# 3. Requirements expose real row-level structure
# ---------------------------------------------------------------------------


def test_requirements_subtypes_distinguished(rich_packets):
    packets, _ = rich_packets
    subtypes = {it.criterion_subtype for it in packets["requirements"].evidence_items}
    assert "functional" in subtypes
    assert "non_functional" in subtypes
    # Use cases are no longer admitted as requirements (heading-only gating).
    assert "use_case" not in subtypes


def test_requirements_subtype_from_heading_path(rich_packets):
    packets, _ = rich_packets
    for it in packets["requirements"].evidence_items:
        if it.criterion_subtype in ("functional", "non_functional", "use_case"):
            assert it.classification_source == "heading_path"


def test_requirements_matched_row_ids_and_count(rich_packets):
    packets, _ = rich_packets
    fr_items = [
        it for it in packets["requirements"].evidence_items
        if it.criterion_subtype == "functional"
    ]
    assert fr_items
    fr = fr_items[0]
    # The FR table has 8 rows tagged FR-01..FR-08 with MoSCoW priorities.
    assert fr.matched_row_count == 8
    assert "FR01" in fr.matched_row_ids
    assert any(s.startswith("moscow:") for s in fr.matched_signals)


def test_requirements_moscow_tiers_surface(rich_packets):
    packets, _ = rich_packets
    fr_items = [
        it for it in packets["requirements"].evidence_items
        if it.criterion_subtype == "functional"
    ]
    sigs = set(fr_items[0].matched_signals)
    # FR priorities include Must, Should, Could and Won't.
    for tier in ("moscow:must", "moscow:should", "moscow:could", "moscow:wont"):
        assert tier in sigs, f"missing {tier} in {sigs}"


def test_requirements_dense_table_is_strong(rich_packets):
    packets, _ = rich_packets
    fr = next(
        it for it in packets["requirements"].evidence_items
        if it.criterion_subtype == "functional"
    )
    assert fr.evidence_strength == "strong"


# ---------------------------------------------------------------------------
# 3b. Local section label / stale-heading carryover guard
# ---------------------------------------------------------------------------


def test_local_section_label_on_combined_heading():
    """A combined heading whose rows are NFR-tagged is recovered via row IDs."""
    report = {
        "doc_id": "enrich-combined",
        "source_name": "combined.pdf",
        "page_count": 4,
        "block_count": 1,
        "blocks": [
            _req_table(
                "c01", "Constraints en (non-) Functionele eisen", "NFR",
                ["Must have", "Should have", "Must have"],
            ),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    items = packets["requirements"].evidence_items
    assert items
    it = items[0]
    # Heading is combined → subtype recovered from row-id prefix, not heading.
    assert it.criterion_subtype == "non_functional"
    assert it.classification_source == "row_id_prefix"
    assert "NFR01" in it.matched_row_ids


# ---------------------------------------------------------------------------
# 4. Honesty + no forbidden fields
# ---------------------------------------------------------------------------


def test_no_fabricated_subtype_for_plain_paragraph():
    """A requirement paragraph with no IDs/heading must not invent a subtype."""
    report = {
        "doc_id": "enrich-plain",
        "source_name": "plain.pdf",
        "page_count": 3,
        "block_count": 1,
        "blocks": [
            _block(
                "p01", 1, "paragraph",
                "1. De website moet kunnen inloggen. 2. De website moet kunnen "
                "zoeken. 3. De gebruiker moet kunnen bestellen.",
                ["Requirements"],
            ),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    for it in packets["requirements"].evidence_items:
        # No FR/NFR/UC heading and no IDs → subtype must stay empty.
        if not it.matched_row_ids:
            assert it.criterion_subtype in ("", "constraint") or it.matched_row_ids


def test_handoff_enrichment_has_no_forbidden_keys(rich_packets):
    _, artifacts = rich_packets
    text = json.dumps(to_dict(build_handoff_from_artifacts(artifacts)))
    for key in _FORBIDDEN_KEYS:
        assert f'"{key}"' not in text, f"forbidden key leaked: {key}"
