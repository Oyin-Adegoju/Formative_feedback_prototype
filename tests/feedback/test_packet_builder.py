"""Tests for the evidence-packet builder (src/feedback/packet_builder.py).

Three sections:
  1. Structural tests (synthetic strong/weak reports) — fast, no file I/O.
  2. Selection-rule tests (criterion-specific behaviour).
  3. Integration tests (all 9 real anonymised documents across quality levels).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from src.caps.caps import run_caps_with_artifacts
from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.evidence import EvidenceItem, EvidencePacket, SignalClass
from src.feedback.packet_builder import (
    _MAX_ITEMS,
    build_evidence_packets,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_DATA_DIR = pathlib.Path(__file__).parents[2] / "data" / "anonymized"

_SIGNAL_CLASSES: frozenset[SignalClass] = frozenset({"positive", "weak", "absent_marker"})


def _block(
    block_id: str,
    page_no: int,
    block_type: str,
    text: str,
    heading_path: list[str],
    *,
    is_front_matter: bool = False,
    is_appendix: bool = False,
    table_meta: dict | None = None,
) -> dict:
    b: dict = {
        "block_id": block_id,
        "page_no": page_no,
        "block_type": block_type,
        "text": text,
        "heading_path": heading_path,
        "is_front_matter": is_front_matter,
        "is_appendix": is_appendix,
    }
    if table_meta is not None:
        b["table_meta"] = table_meta
    return b


# Strong synthetic report — clear evidence for all 5 criteria.
_STRONG_BLOCKS = [
    _block(
        "s01", 2, "paragraph",
        "De gekozen beperking is een visuele beperking. Wij richten ons op "
        "slechtziende en blinde gebruikers. Uit deskresearch blijkt dat deze "
        "doelgroep met beperking beperkte toegang heeft tot online winkels.",
        ["Beperking & deskresearch"],
    ),
    _block(
        "s02", 3, "paragraph",
        "Literatuuronderzoek toont aan dat toegankelijkheid voor de doelgroep "
        "met beperking essentieel is voor inclusie. Bron: WHO rapport 2023.",
        ["Beperking & deskresearch"],
    ),
    _block(
        "s03", 5, "table",
        "Stakeholder belang invloed Opdrachtgever hoog laag Eindgebruiker hoog hoog "
        "Ontwikkelaar middel hoog Projectmanager hoog hoog Designer middel middel "
        "Beheerder laag hoog",
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
    _block(
        "s04", 7, "table",
        "Naam Prio Omschrijving Inloggen Must Gebruiker kan inloggen "
        "Registreren Must Nieuw account aanmaken Zoeken Should Producten zoeken "
        "Bestellen Must Product bestellen Winkelwagen Should Items bewaren "
        "Betalen Must Betaling verwerken",
        ["Requirements", "Functionele requirements"],
        table_meta={
            "row_count": 6, "column_count": 3,
            "header_row": "Naam | Prio | Omschrijving",
            "cells": [
                ["Inloggen", "Must", "Gebruiker kan inloggen"],
                ["Registreren", "Must", "Nieuw account aanmaken"],
                ["Zoeken", "Should", "Producten zoeken"],
                ["Bestellen", "Must", "Product bestellen"],
                ["Winkelwagen", "Should", "Items bewaren"],
                ["Betalen", "Must", "Betaling verwerken"],
            ],
            "index_cells": [],
        },
    ),
    _block(
        "s04b", 7, "table",
        "Naam Prio Omschrijving Performantie Must Pagina laad snel "
        "Beveiliging Must Authenticatie verplicht Betrouwbaarheid Should "
        "Uptime 99.9% Schaalbaarheid Could Horizontaal schalen",
        ["Requirements", "Niet-functionele requirements"],
        table_meta={
            "row_count": 4, "column_count": 3,
            "header_row": "Naam | Prio | Omschrijving",
            "cells": [
                ["Performantie", "Must", "Pagina laad snel"],
                ["Beveiliging", "Must", "Authenticatie verplicht"],
                ["Betrouwbaarheid", "Should", "Uptime 99.9%"],
                ["Schaalbaarheid", "Could", "Horizontaal schalen"],
            ],
            "index_cells": [],
        },
    ),
    _block(
        "s05", 9, "paragraph",
        "De webshop wordt volledig in het nederlands aangeboden. Dit heeft "
        "gevolgen voor het bereik van de doelgroep. Vertaalkosten worden "
        "vermeden. Lokalisatie is niet nodig.",
        ["Taalkeuze"],
    ),
    _block(
        "s06", 11, "paragraph",
        "De webshop maakt gebruik van authenticatie via een inlogsysteem. "
        "Autorisatie is geregeld via gebruikersrollen. Encryptie wordt "
        "toegepast conform de AVG.",
        ["Security", "Beveiliging"],
    ),
]

_STRONG_REPORT: dict = {
    "doc_id": "pkt-strong-001",
    "source_name": "strong.pdf",
    "page_count": 15,
    "block_count": len(_STRONG_BLOCKS),
    "blocks": _STRONG_BLOCKS,
}

# Weak synthetic report — thin / missing evidence for every criterion.
_WEAK_BLOCKS = [
    _block("w01", 1, "paragraph", "We richten ons op een doelgroep.", ["Inleiding"]),
    _block(
        "w02", 2, "table",
        "Opdrachtgever Eindgebruiker",
        ["Stakeholders"],
        table_meta={
            "row_count": 2, "column_count": 1,
            "header_row": None,
            "cells": [["Opdrachtgever"], ["Eindgebruiker"]],
            "index_cells": [],
        },
    ),
    _block("w03", 3, "paragraph", "FR-01 FR-02 FR-03", ["Eisen"]),
    _block("w04", 4, "paragraph", "De webshop is in het nederlands.", ["Inleiding"]),
    _block("w05", 5, "paragraph", "De webshop moet veilig zijn.", ["Inleiding"]),
]

_WEAK_REPORT: dict = {
    "doc_id": "pkt-weak-001",
    "source_name": "weak.pdf",
    "page_count": 8,
    "block_count": len(_WEAK_BLOCKS),
    "blocks": _WEAK_BLOCKS,
}


def _load_real_doc(subdir: str, doc_id: str) -> dict[str, Any]:
    """Load and normalise an anonymised JSON report."""
    path = _DATA_DIR / subdir / f"{doc_id}_anonymized.json"
    raw: dict = json.loads(path.read_text(encoding="utf-8"))
    if "page_count" not in raw or raw["page_count"] is None:
        raw["page_count"] = max(b["page_no"] for b in raw["blocks"])
    return raw


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def strong_packets():
    artifacts = run_caps_with_artifacts(_STRONG_REPORT, input_source="parser_direct")
    return build_evidence_packets(artifacts), artifacts


@pytest.fixture(scope="module")
def weak_packets():
    artifacts = run_caps_with_artifacts(_WEAK_REPORT, input_source="parser_direct")
    return build_evidence_packets(artifacts), artifacts


# ---------------------------------------------------------------------------
# Section 1: Structural tests
# ---------------------------------------------------------------------------


def test_all_criteria_present_in_strong(strong_packets):
    packets, _ = strong_packets
    assert set(packets.keys()) == set(CRITERIA_KEYS)


def test_all_criteria_present_in_weak(weak_packets):
    packets, _ = weak_packets
    assert set(packets.keys()) == set(CRITERIA_KEYS)


def test_packet_is_evidencepacket_instance(strong_packets):
    packets, _ = strong_packets
    for key in CRITERIA_KEYS:
        assert isinstance(packets[key], EvidencePacket)


def test_item_count_limits_respected_strong(strong_packets):
    packets, _ = strong_packets
    for key in CRITERIA_KEYS:
        limit = _MAX_ITEMS[key]
        assert len(packets[key].evidence_items) <= limit, (
            f"{key}: {len(packets[key].evidence_items)} items > limit {limit}"
        )


def test_item_count_limits_respected_weak(weak_packets):
    packets, _ = weak_packets
    for key in CRITERIA_KEYS:
        limit = _MAX_ITEMS[key]
        assert len(packets[key].evidence_items) <= limit


def test_all_signal_classes_are_valid(strong_packets):
    packets, _ = strong_packets
    for key, pkt in packets.items():
        for item in pkt.evidence_items:
            assert item.signal_class in _SIGNAL_CLASSES, (
                f"{key}/{item.block_id}: unexpected signal_class '{item.signal_class}'"
            )


def test_evidence_items_have_non_empty_block_ids(strong_packets):
    packets, _ = strong_packets
    for key, pkt in packets.items():
        for item in pkt.evidence_items:
            assert item.block_id, f"{key}: empty block_id"


def test_criterion_key_matches_packet_key(strong_packets):
    packets, _ = strong_packets
    for key, pkt in packets.items():
        assert pkt.criterion_key == key


def test_notes_copied_from_caps(strong_packets):
    packets, artifacts = strong_packets
    for key in CRITERIA_KEYS:
        cr = artifacts.criterion_results[key]
        assert packets[key].notes == cr.notes


def test_excerpts_are_non_empty_strings(strong_packets):
    packets, _ = strong_packets
    for key, pkt in packets.items():
        for item in pkt.evidence_items:
            assert isinstance(item.excerpt, str) and item.excerpt.strip(), (
                f"{key}/{item.block_id}: empty excerpt"
            )


# ---------------------------------------------------------------------------
# Section 2: Selection-rule tests
# ---------------------------------------------------------------------------


def test_stakeholders_prefers_table_evidence(strong_packets):
    """Stakeholder packet should include the table block when one is available."""
    packets, _ = strong_packets
    pkt = packets["stakeholders"]
    block_types = {item.block_type for item in pkt.evidence_items}
    assert "table" in block_types, (
        "Stakeholders packet should prefer table-type evidence"
    )


def test_requirements_prefers_table_evidence(strong_packets):
    """Requirements packet should include table blocks when available."""
    packets, _ = strong_packets
    pkt = packets["requirements"]
    block_types = [item.block_type for item in pkt.evidence_items]
    assert "table" in block_types, (
        "Requirements packet should prefer table-type evidence"
    )


def test_requirements_table_ranked_before_paragraph():
    """When both table and paragraph hits exist, tables come first."""
    report = {
        "doc_id": "pkt-req-order",
        "source_name": "req_order.pdf",
        "page_count": 5,
        "block_count": 2,
        "blocks": [
            _block(
                "r01", 1, "paragraph",
                "FR-01 FR-02 FR-03 FR-04 FR-05 FR-06 FR-07 FR-08 "
                "FR-09 FR-10 FR-11 FR-12 FR-13 FR-14 FR-15 FR-16",
                ["Requirements"],
            ),
            _block(
                "r02", 2, "table",
                "Naam Prio Omschrijving Must Have feature Could feature Should feature",
                ["Requirements", "Functionele requirements"],
                table_meta={
                    "row_count": 3, "column_count": 3,
                    "header_row": "Naam | Prio | Omschrijving",
                    "cells": [
                        ["Must Have feature", "Must", "desc1"],
                        ["Could feature", "Could", "desc2"],
                        ["Should feature", "Should", "desc3"],
                    ],
                    "index_cells": [],
                },
            ),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    pkt = packets["requirements"]
    assert len(pkt.evidence_items) > 0
    # The first item should be a table (most informative for requirements).
    assert pkt.evidence_items[0].block_type == "table"


def test_no_positive_items_when_criterion_is_missing():
    """A criterion with status=missing should produce no positive evidence items."""
    # Minimal report that triggers missing for all criteria.
    report = {
        "doc_id": "pkt-all-missing",
        "source_name": "empty.pdf",
        "page_count": 2,
        "block_count": 1,
        "blocks": [
            _block("x01", 1, "paragraph", "Dit is een webshop project.", ["Inleiding"]),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    for key, pkt in packets.items():
        cr = artifacts.criterion_results[key]
        if cr.status == "missing":
            positives = [i for i in pkt.evidence_items if i.signal_class == "positive"]
            assert not positives, (
                f"{key} (status=missing) should not have positive items, "
                f"got {[i.block_id for i in positives]}"
            )


def test_missing_signals_non_empty_for_missing_criterion():
    """missing_signals should be populated whenever a criterion status is missing or partial."""
    report = {
        "doc_id": "pkt-missing-sigs",
        "source_name": "empty2.pdf",
        "page_count": 2,
        "block_count": 1,
        "blocks": [
            _block("y01", 1, "paragraph", "Dit is een webshop project.", ["Inleiding"]),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    for key, pkt in packets.items():
        cr = artifacts.criterion_results[key]
        if cr.status in ("missing", "partial"):
            assert pkt.missing_signals, (
                f"{key} (status={cr.status}): expected non-empty missing_signals"
            )


def test_absent_marker_for_heading_only_hit():
    """A heading-only block (no text-hint match) should produce an absent_marker item."""
    report = {
        "doc_id": "pkt-heading-only",
        "source_name": "headings.pdf",
        "page_count": 3,
        "block_count": 2,
        "blocks": [
            # Heading whose heading_path contains "Security" (matches heading hint)
            # but whose block text is neutral — so matched_text_hints stays empty,
            # making _is_heading_only() return True → classified as absent_marker.
            _block("h01", 2, "heading", "3.1 Overzicht", ["Security"]),
            # Unrelated paragraph to avoid all-empty retrieval.
            _block("h02", 2, "paragraph", "De webshop verkoopt producten.", ["Inleiding"]),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    sec_pkt = packets["security"]
    signal_classes = {i.signal_class for i in sec_pkt.evidence_items}
    # Either no items (no retrieval hits) or an absent_marker from the heading.
    if sec_pkt.evidence_items:
        assert "absent_marker" in signal_classes, (
            "Heading-only security block should produce an absent_marker item"
        )


def test_beperking_partial_limitation_only_has_missing_research():
    """When only limitation is found (no research), missing_signals mentions research."""
    report = {
        "doc_id": "pkt-bep-lim-only",
        "source_name": "lim_only.pdf",
        "page_count": 2,
        "block_count": 2,
        "blocks": [
            _block(
                "b01", 1, "paragraph",
                "Wij richten ons op slechtziende gebruikers. De gekozen beperking "
                "is een visuele beperking. Dit is onze specifieke doelgroep.",
                ["Beperking"],
            ),
            _block(
                "b02", 2, "paragraph",
                "De webshop biedt producten aan voor deze doelgroep.",
                ["Inleiding"],
            ),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    pkt = packets["beperking"]
    cr = artifacts.criterion_results["beperking"]
    if cr.status == "partial":
        missing_lower = " ".join(pkt.missing_signals).lower()
        assert "deskresearch" in missing_lower or "bronvermelding" in missing_lower or "onderbouwing" in missing_lower, (
            f"Expected research missing_signal, got: {pkt.missing_signals}"
        )


def test_taalkeuze_partial_choice_only_has_missing_consequences():
    """When only language choice is found, missing_signals mentions consequences."""
    report = {
        "doc_id": "pkt-taal-choice-only",
        "source_name": "taal_choice.pdf",
        "page_count": 2,
        "block_count": 1,
        "blocks": [
            _block(
                "t01", 1, "paragraph",
                "De webshop wordt volledig in het nederlands aangeboden. "
                "De taal van de webshop is dus Nederlands.",
                ["Taalkeuze"],
            ),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    pkt = packets["taalkeuze"]
    cr = artifacts.criterion_results["taalkeuze"]
    if cr.status == "partial":
        # Should mention missing consequences
        missing_lower = " ".join(pkt.missing_signals).lower()
        assert any(w in missing_lower for w in ("gevolgen", "consequen", "bereik", "vertaal")), (
            f"Expected consequence missing_signal, got: {pkt.missing_signals}"
        )


def test_security_sufficient_produces_positive_concrete_items():
    """A security-sufficient verdict should produce positive items with concrete mechanisms."""
    report = {
        "doc_id": "pkt-sec-suff",
        "source_name": "sec_suff.pdf",
        "page_count": 3,
        "block_count": 1,
        "blocks": [
            _block(
                "c01", 2, "paragraph",
                "De webshop gebruikt authenticatie en autorisatie voor toegangscontrole. "
                "Wachtwoorden worden opgeslagen met bcrypt hashing. AVG-compliance "
                "is geborgd via een privacyverklaring.",
                ["Security", "Beveiliging"],
            ),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    pkt = packets["security"]
    cr = artifacts.criterion_results["security"]
    if cr.status in ("sufficient", "strong"):
        positives = [i for i in pkt.evidence_items if i.signal_class == "positive"]
        assert positives, "Sufficient security criterion should have at least one positive item"


def test_table_excerpt_includes_header_row():
    """Table excerpts should include the header_row when it is present."""
    report = {
        "doc_id": "pkt-tbl-header",
        "source_name": "tbl_header.pdf",
        "page_count": 2,
        "block_count": 1,
        "blocks": [
            _block(
                "t02", 1, "table",
                "Stakeholder Belang Invloed Opdrachtgever hoog laag",
                ["Stakeholders"],
                table_meta={
                    "row_count": 1, "column_count": 3,
                    "header_row": "Stakeholder | Belang | Invloed",
                    "cells": [["Opdrachtgever", "hoog", "laag"]],
                    "index_cells": [],
                },
            ),
        ],
    }
    artifacts = run_caps_with_artifacts(report, input_source="parser_direct")
    packets = build_evidence_packets(artifacts)
    pkt = packets["stakeholders"]
    if pkt.evidence_items:
        table_items = [i for i in pkt.evidence_items if i.block_type == "table"]
        if table_items:
            assert "Stakeholder" in table_items[0].excerpt or "Belang" in table_items[0].excerpt


def test_strong_packets_have_positive_items_for_present_criteria(strong_packets):
    """For each criterion that CAPS rates sufficient/strong, the packet should
    contain at least one positive evidence item."""
    packets, artifacts = strong_packets
    for key in CRITERIA_KEYS:
        cr = artifacts.criterion_results[key]
        pkt = packets[key]
        if cr.status in ("sufficient", "strong"):
            positives = [i for i in pkt.evidence_items if i.signal_class == "positive"]
            assert positives, (
                f"{key} (status={cr.status}): expected at least one positive item, "
                f"got {[i.signal_class for i in pkt.evidence_items]}"
            )


# ---------------------------------------------------------------------------
# Section 3: Integration tests — real anonymised documents
# ---------------------------------------------------------------------------

_REAL_DOCS = [
    ("Goed",        "2e138dc4"),
    ("Goed",        "8e5ee17e"),
    ("Goed",        "c90afb25"),
    ("Voldoende",   "1023e8a9"),
    ("Voldoende",   "104812d2"),
    ("Voldoende",   "2f4d9d91"),
    ("onvoldoende", "23276484"),
    ("onvoldoende", "35baf7d3"),
    ("onvoldoende", "f14254dd"),
]


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_real_doc_all_criteria_present(subdir, doc_id):
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    packets = build_evidence_packets(artifacts)
    assert set(packets.keys()) == set(CRITERIA_KEYS), (
        f"{doc_id}: packets keys mismatch"
    )


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_real_doc_item_count_limits(subdir, doc_id):
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    packets = build_evidence_packets(artifacts)
    for key in CRITERIA_KEYS:
        limit = _MAX_ITEMS[key]
        n = len(packets[key].evidence_items)
        assert n <= limit, f"{doc_id}/{key}: {n} items > limit {limit}"


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_real_doc_signal_classes_valid(subdir, doc_id):
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    packets = build_evidence_packets(artifacts)
    for key, pkt in packets.items():
        for item in pkt.evidence_items:
            assert item.signal_class in _SIGNAL_CLASSES, (
                f"{doc_id}/{key}/{item.block_id}: invalid signal_class '{item.signal_class}'"
            )


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_real_doc_no_positive_when_missing(subdir, doc_id):
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    packets = build_evidence_packets(artifacts)
    for key in CRITERIA_KEYS:
        cr = artifacts.criterion_results[key]
        pkt = packets[key]
        if cr.status == "missing":
            positives = [i for i in pkt.evidence_items if i.signal_class == "positive"]
            assert not positives, (
                f"{doc_id}/{key} (missing): unexpected positive items: "
                f"{[i.block_id for i in positives]}"
            )


@pytest.mark.parametrize("subdir,doc_id", [
    ("Goed", "2e138dc4"),
    ("Goed", "8e5ee17e"),
    ("Goed", "c90afb25"),
])
def test_good_docs_have_mostly_positive_items(subdir, doc_id):
    """'Goed' documents should produce mainly positive items across criteria."""
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    packets = build_evidence_packets(artifacts)

    all_items = [item for pkt in packets.values() for item in pkt.evidence_items]
    if not all_items:
        return  # nothing to check
    positive_rate = sum(1 for i in all_items if i.signal_class == "positive") / len(all_items)
    assert positive_rate >= 0.5, (
        f"{doc_id}: only {positive_rate:.0%} positive items in a 'Goed' document"
    )


@pytest.mark.parametrize("subdir,doc_id", [
    ("Voldoende", "104812d2"),
    ("Voldoende", "2f4d9d91"),
])
def test_voldoende_requirements_has_table_evidence(subdir, doc_id):
    """'Voldoende' documents with requirements tables should surface them as evidence."""
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    packets = build_evidence_packets(artifacts)
    pkt = packets["requirements"]
    cr = artifacts.criterion_results["requirements"]
    if cr.status in ("sufficient", "strong"):
        types = {i.block_type for i in pkt.evidence_items}
        assert "table" in types, (
            f"{doc_id}/requirements: expected table evidence, got {types}"
        )


@pytest.mark.parametrize("subdir,doc_id", [
    ("onvoldoende", "23276484"),
    ("onvoldoende", "35baf7d3"),
    ("onvoldoende", "f14254dd"),
])
def test_onvoldoende_failing_criteria_have_missing_signals(subdir, doc_id):
    """Failing criteria in 'onvoldoende' documents should have non-empty missing_signals."""
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    packets = build_evidence_packets(artifacts)
    for key in CRITERIA_KEYS:
        cr = artifacts.criterion_results[key]
        pkt = packets[key]
        if cr.status in ("missing", "partial"):
            assert pkt.missing_signals, (
                f"{doc_id}/{key} (status={cr.status}): expected missing_signals"
            )
