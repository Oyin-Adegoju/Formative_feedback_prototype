"""Tests for the pre-Qwen CAPS→Qwen handoff contract (src/feedback/handoff.py).

Verifies the stable boundary structure that downstream Qwen will consume:
  1. Structural tests (synthetic strong report) — fast, no file I/O.
  2. Contract tests (required fields, no removed manual_review fields, JSON-safe).
  3. Integration tests across all 9 real anonymised documents.

The handoff is a thin merge of CAPS verdicts (source of truth) and the existing
evidence packets. These tests assert the contract shape only — never re-judge.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from src.caps.caps import run_caps_with_artifacts
from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.evidence import EvidenceItem
from src.feedback.handoff import (
    CapsHandoff,
    CriterionHandoff,
    build_caps_handoff,
    build_handoff_from_artifacts,
    to_dict,
)
from src.feedback.packet_builder import build_evidence_packets

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DATA_DIR = pathlib.Path(__file__).parents[2] / "data" / "anonymized"

_HANDOFF_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "document_id", "source_name", "criteria",
})

_CRITERION_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "count", "notes", "missing_signals", "evidence_items",
})

_EVIDENCE_ITEM_FIELDS: frozenset[str] = frozenset({
    # original stable contract
    "block_id", "page_no", "block_type", "heading_path",
    "excerpt", "selection_reason", "signal_class",
    # rule-based enrichment (always present, may be empty)
    "focused_excerpt", "matched_signals", "criterion_subtype",
    "classification_source", "evidence_strength", "matched_row_count",
    "matched_row_ids", "local_section_label", "context_warning",
})

# The seven fields the contract guarantees were always present, kept separate so
# a test can assert the original shape is still a strict subset.
_EVIDENCE_ITEM_CORE_FIELDS: frozenset[str] = frozenset({
    "block_id", "page_no", "block_type", "heading_path",
    "excerpt", "selection_reason", "signal_class",
})

_VALID_EVIDENCE_STRENGTHS: frozenset[str] = frozenset(
    {"strong", "moderate", "thin", "absent"}
)

# Manual-review ownership was removed from CAPS — these must never reappear.
_FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "manual_review", "manual_review_required", "manual_review_flags",
    "manual_review_reason",
})

# Qwen-stage fields must not leak into the pre-Qwen handoff either.
_FORBIDDEN_QWEN_KEYS: frozenset[str] = frozenset({
    "qwen_diagnostics", "qwen_strengths", "qwen_weaknesses",
})

# CAPS scoring verdicts were removed from the pre-Qwen extraction handoff —
# these must never appear at document or criterion level.
_FORBIDDEN_VERDICT_KEYS: frozenset[str] = frozenset({
    "status", "stoplight", "overall_stoplight", "blockers_triggered",
})


# ---------------------------------------------------------------------------
# Synthetic report (clear evidence for all 5 criteria)
# ---------------------------------------------------------------------------


def _block(
    block_id: str,
    page_no: int,
    block_type: str,
    text: str,
    heading_path: list[str],
    *,
    table_meta: dict | None = None,
) -> dict:
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


_STRONG_REPORT: dict = {
    "doc_id": "handoff-strong-001",
    "source_name": "strong.pdf",
    "page_count": 15,
    "block_count": 5,
    "blocks": [
        _block(
            "s01", 2, "paragraph",
            "De gekozen beperking is een visuele beperking. Wij richten ons op "
            "slechtziende en blinde gebruikers. Uit deskresearch en "
            "literatuuronderzoek blijkt dat deze doelgroep met beperking "
            "beperkte toegang heeft. Bron: WHO rapport 2023.",
            ["Beperking & deskresearch"],
        ),
        _block(
            "s03", 5, "table",
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
        _block(
            "s04", 7, "table",
            "Naam Prio Omschrijving",
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
    ],
}


def _load_real_doc(subdir: str, doc_id: str) -> dict[str, Any]:
    """Load and normalise an anonymised JSON report (inject page_count if absent)."""
    path = _DATA_DIR / subdir / f"{doc_id}_anonymized.json"
    raw: dict = json.loads(path.read_text(encoding="utf-8"))
    if "page_count" not in raw or raw["page_count"] is None:
        raw["page_count"] = max(b["page_no"] for b in raw["blocks"])
    return raw


def _discover_real_docs(subdir: str | None = None) -> list[tuple[str, str]]:
    """Discover (subdir, doc_id) for every anonymized document on disk.

    The corpus is curated over time (documents added/removed), so the
    integration parametrisation is derived from disk instead of a hardcoded
    list — adding or deleting a document never breaks the suite.
    """
    subs = [subdir] if subdir else ["Goed", "Voldoende", "onvoldoende"]
    out: list[tuple[str, str]] = []
    for sub in subs:
        d = _DATA_DIR / sub
        if d.is_dir():
            for f in sorted(d.glob("*_anonymized.json")):
                out.append((sub, f.name[: -len("_anonymized.json")]))
    return out


_REAL_DOCS = _discover_real_docs()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def strong_handoff():
    artifacts = run_caps_with_artifacts(_STRONG_REPORT, input_source="parser_direct")
    return build_handoff_from_artifacts(artifacts), artifacts


# ---------------------------------------------------------------------------
# Section 1: Structural tests
# ---------------------------------------------------------------------------


def test_returns_caps_handoff_instance(strong_handoff):
    handoff, _ = strong_handoff
    assert isinstance(handoff, CapsHandoff)


def test_all_criteria_present(strong_handoff):
    handoff, _ = strong_handoff
    assert set(handoff.criteria.keys()) == set(CRITERIA_KEYS)


def test_criteria_in_stable_order(strong_handoff):
    handoff, _ = strong_handoff
    assert list(handoff.criteria.keys()) == list(CRITERIA_KEYS)


def test_document_identity_from_caps(strong_handoff):
    handoff, artifacts = strong_handoff
    assert handoff.document_id == artifacts.result.doc_id
    assert handoff.source_name == artifacts.result.source_name


def test_handoff_carries_no_document_verdict(strong_handoff):
    """The pre-Qwen handoff has no document-level stoplight/blocker verdict."""
    handoff, _ = strong_handoff
    assert not hasattr(handoff, "overall_stoplight")
    assert not hasattr(handoff, "blockers_triggered")


def test_criterion_is_handoff_instance(strong_handoff):
    handoff, _ = strong_handoff
    for key in CRITERIA_KEYS:
        assert isinstance(handoff.criteria[key], CriterionHandoff)


def test_extraction_fields_copied_verbatim_from_caps(strong_handoff):
    """CAPS is the source of truth for the extraction notes/missing_signals.
    count is intentionally neutralized to None (not trustworthy enough to
    expose); the verdicts (status/stoplight) are not carried."""
    handoff, artifacts = strong_handoff
    for key in CRITERIA_KEYS:
        cr = artifacts.result.scorecard.results[key]
        ch = handoff.criteria[key]
        assert ch.count is None
        assert ch.notes == cr.notes
        assert ch.missing_signals == cr.missing_signals
        # Verdicts are deliberately absent from the handoff entry.
        assert not hasattr(ch, "status")
        assert not hasattr(ch, "stoplight")


def test_evidence_items_reused_from_packets(strong_handoff):
    """evidence_items come unchanged from the criterion's EvidencePacket."""
    handoff, artifacts = strong_handoff
    packets = build_evidence_packets(artifacts)
    for key in CRITERIA_KEYS:
        assert handoff.criteria[key].evidence_items == packets[key].evidence_items
        for item in handoff.criteria[key].evidence_items:
            assert isinstance(item, EvidenceItem)


# ---------------------------------------------------------------------------
# Section 2: Serialized contract tests (to_dict)
# ---------------------------------------------------------------------------


def test_to_dict_top_level_keys(strong_handoff):
    handoff, _ = strong_handoff
    d = to_dict(handoff)
    assert set(d.keys()) == _HANDOFF_TOP_LEVEL_KEYS


def test_to_dict_criterion_required_fields(strong_handoff):
    handoff, _ = strong_handoff
    d = to_dict(handoff)
    for key in CRITERIA_KEYS:
        assert set(d["criteria"][key].keys()) == _CRITERION_REQUIRED_FIELDS


def test_to_dict_evidence_item_fields(strong_handoff):
    handoff, _ = strong_handoff
    d = to_dict(handoff)
    for key in CRITERIA_KEYS:
        for item in d["criteria"][key]["evidence_items"]:
            assert set(item.keys()) == _EVIDENCE_ITEM_FIELDS


def test_to_dict_evidence_item_core_fields_still_present(strong_handoff):
    """The original seven-field contract must remain a strict subset."""
    handoff, _ = strong_handoff
    d = to_dict(handoff)
    for key in CRITERIA_KEYS:
        for item in d["criteria"][key]["evidence_items"]:
            assert _EVIDENCE_ITEM_CORE_FIELDS <= set(item.keys())


def test_to_dict_evidence_strength_is_valid(strong_handoff):
    """evidence_strength is always one of the documented labels."""
    handoff, _ = strong_handoff
    d = to_dict(handoff)
    for key in CRITERIA_KEYS:
        for item in d["criteria"][key]["evidence_items"]:
            assert item["evidence_strength"] in _VALID_EVIDENCE_STRENGTHS


def test_to_dict_enrichment_field_types(strong_handoff):
    """Enrichment fields keep stable JSON types (lists/strings/int|None)."""
    handoff, _ = strong_handoff
    d = to_dict(handoff)
    for key in CRITERIA_KEYS:
        for item in d["criteria"][key]["evidence_items"]:
            assert isinstance(item["matched_signals"], list)
            assert isinstance(item["matched_row_ids"], list)
            assert isinstance(item["focused_excerpt"], str)
            assert isinstance(item["criterion_subtype"], str)
            assert isinstance(item["classification_source"], str)
            assert isinstance(item["local_section_label"], str)
            assert isinstance(item["context_warning"], str)
            assert item["matched_row_count"] is None or isinstance(
                item["matched_row_count"], int
            )


def test_requirements_evidence_exposes_structure(strong_handoff):
    """The synthetic strong report has a Functionele requirements table: its
    subtype, MoSCoW signal, and matched row count must surface on the item."""
    handoff, _ = strong_handoff
    d = to_dict(handoff)
    req_items = d["criteria"]["requirements"]["evidence_items"]
    assert req_items, "expected requirements evidence in the strong report"
    subtypes = {it["criterion_subtype"] for it in req_items}
    assert "functional" in subtypes
    # At least one table item reports a MoSCoW signal and a matched row count.
    assert any(
        any(s.startswith("moscow:") for s in it["matched_signals"])
        and it["matched_row_count"]
        for it in req_items
    )


def test_to_dict_is_json_serializable(strong_handoff):
    handoff, _ = strong_handoff
    # Must round-trip through JSON without error.
    text = json.dumps(to_dict(handoff), ensure_ascii=False)
    assert json.loads(text)["document_id"] == handoff.document_id


def test_no_manual_review_fields(strong_handoff):
    """No removed manual_review key may appear anywhere in the handoff JSON."""
    handoff, _ = strong_handoff
    text = json.dumps(to_dict(handoff))
    for key in _FORBIDDEN_KEYS:
        assert f'"{key}"' not in text, f"forbidden key leaked: {key}"


def test_no_qwen_fields(strong_handoff):
    """Qwen-stage fields must not appear in the pre-Qwen handoff."""
    handoff, _ = strong_handoff
    text = json.dumps(to_dict(handoff))
    for key in _FORBIDDEN_QWEN_KEYS:
        assert f'"{key}"' not in text, f"premature Qwen key present: {key}"


def test_no_hidden_score(strong_handoff):
    """hidden_score is an internal CAPS artifact and must stay out of the handoff."""
    handoff, _ = strong_handoff
    text = json.dumps(to_dict(handoff))
    assert "hidden_score" not in text


def test_no_caps_verdict_fields(strong_handoff):
    """CAPS scoring verdicts must not appear anywhere in the pre-Qwen handoff JSON."""
    handoff, _ = strong_handoff
    text = json.dumps(to_dict(handoff))
    for key in _FORBIDDEN_VERDICT_KEYS:
        assert f'"{key}"' not in text, f"removed CAPS verdict leaked: {key}"


def test_build_caps_handoff_matches_convenience(strong_handoff):
    """build_caps_handoff(result, packets) equals build_handoff_from_artifacts."""
    handoff, artifacts = strong_handoff
    packets = build_evidence_packets(artifacts)
    direct = build_caps_handoff(artifacts.result, packets)
    assert to_dict(direct) == to_dict(handoff)


# ---------------------------------------------------------------------------
# Section 3: Integration tests — real anonymised documents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_real_doc_handoff_contract(subdir, doc_id):
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    d = to_dict(build_handoff_from_artifacts(artifacts))

    # Document identity (no document-level verdict in the extraction handoff).
    assert d["document_id"] == raw["doc_id"]
    assert set(d.keys()) == _HANDOFF_TOP_LEVEL_KEYS

    # All criteria present with the required extraction fields and valid values.
    assert set(d["criteria"].keys()) == set(CRITERIA_KEYS)
    for key in CRITERIA_KEYS:
        crit = d["criteria"][key]
        assert set(crit.keys()) == _CRITERION_REQUIRED_FIELDS
        assert crit["count"] is None or isinstance(crit["count"], int)
        assert isinstance(crit["notes"], list)
        assert isinstance(crit["missing_signals"], list)
        assert isinstance(crit["evidence_items"], list)


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_real_doc_evidence_items_stable_shape(subdir, doc_id):
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    d = to_dict(build_handoff_from_artifacts(artifacts))
    for key in CRITERIA_KEYS:
        for item in d["criteria"][key]["evidence_items"]:
            assert set(item.keys()) == _EVIDENCE_ITEM_FIELDS
            assert item["block_id"], f"{doc_id}/{key}: empty block_id"
            assert isinstance(item["heading_path"], list)
            assert isinstance(item["page_no"], int)


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_real_doc_no_manual_review_or_qwen(subdir, doc_id):
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    text = json.dumps(to_dict(build_handoff_from_artifacts(artifacts)))
    for key in _FORBIDDEN_KEYS | _FORBIDDEN_QWEN_KEYS | _FORBIDDEN_VERDICT_KEYS:
        assert f'"{key}"' not in text, f"{doc_id}: forbidden key leaked: {key}"


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_real_doc_at_least_one_criterion_has_evidence(subdir, doc_id):
    """Across a real document, at least one criterion should surface evidence_items.

    A whole-document empty evidence set would signal a broken handoff wiring.
    """
    raw = _load_real_doc(subdir, doc_id)
    artifacts = run_caps_with_artifacts(raw, input_source="anonymized")
    d = to_dict(build_handoff_from_artifacts(artifacts))
    total_items = sum(
        len(d["criteria"][key]["evidence_items"]) for key in CRITERIA_KEYS
    )
    assert total_items > 0, f"{doc_id}: handoff produced no evidence_items at all"
