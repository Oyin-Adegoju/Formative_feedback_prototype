"""Tests for src/feedback/feedback_builder.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.caps.models import (
    CapsRunMeta,
    CapsRunResult,
    CapsScorecard,
    CriterionResult,
    EvidenceRef,
)
from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.feedback_builder import (
    _assemble_prompt,
    _collect_block_ids,
    _fallback,
    _format_scorecard,
    _manual_review_section,
    generate_feedback,
)
from src.feedback.feedback_validator import FeedbackValidationError
from src.feedback.output_schema import DISCLAIMER
from src.llm.llm_client import LlmCallError

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_BLOCK_A = "doc1_0001"
_BLOCK_B = "doc1_0002"
_BLOCK_C = "doc1_0003"


def _make_criterion_result(
    key: str,
    status: str = "sufficient",
    block_ids: list[str] | None = None,
    count: int | None = None,
    notes: list[str] | None = None,
    manual_review: bool = False,
) -> CriterionResult:
    evidence = [
        EvidenceRef(block_id=bid, page_no=1, block_type="paragraph")
        for bid in (block_ids or [])
    ]
    return CriterionResult(
        criterion_key=key,
        status=status,
        stoplight="yellow" if status == "sufficient" else "green" if status == "strong" else "red",
        is_blocker=True,
        evidence=evidence,
        count=count,
        notes=notes or [],
        manual_review=manual_review,
    )


def _make_caps_result(
    stoplight: str = "green",
    doc_id: str = "doc1",
    manual_review_required: bool = False,
    manual_review_flags: list[str] | None = None,
    blockers_triggered: list[str] | None = None,
    criterion_overrides: dict | None = None,
) -> CapsRunResult:
    defaults = {
        "beperking": _make_criterion_result("beperking", block_ids=[_BLOCK_A]),
        "stakeholders": _make_criterion_result("stakeholders", block_ids=[_BLOCK_B], count=5),
        "requirements": _make_criterion_result("requirements", block_ids=[_BLOCK_C], count=20),
        "taalkeuze": _make_criterion_result("taalkeuze"),
        "security": _make_criterion_result("security", count=2),
    }
    if criterion_overrides:
        defaults.update(criterion_overrides)
    scorecard = CapsScorecard(doc_id=doc_id, results=defaults, hidden_score=10)
    return CapsRunResult(
        doc_id=doc_id,
        source_name="test.pdf",
        scorecard=scorecard,
        overall_stoplight=stoplight,
        run_meta=CapsRunMeta(
            input_source="parser_direct",
            page_count=5,
            block_count=20,
        ),
        manual_review_required=manual_review_required,
        manual_review_flags=manual_review_flags or [],
        blockers_triggered=blockers_triggered or [],
    )


def _valid_llm_json(stoplight: str = "green") -> str:
    return json.dumps({
        "stoplight": stoplight,
        "student_samenvatting": "Je document voldoet aan de basisvereisten.",
        "docent_toelichting": "Het document is volledig en goed onderbouwd.",
        "feed_up": "Het doel is een volledig requirements-document.",
        "feedback": [
            {
                "criterium": "beperking",
                "observatie": "De beperking is duidelijk beschreven.",
                "evidence_ref": [_BLOCK_A],
            },
        ],
        "feed_forward": ["Voeg meer bronnen toe."],
        "taalgebruik": "Helder geschreven.",
    })
# ---------------------------------------------------------------------------
# _collect_block_ids
# ---------------------------------------------------------------------------


def test_collect_block_ids_returns_ids_in_stable_order():
    caps = _make_caps_result()
    ids = _collect_block_ids(caps)
    assert _BLOCK_A in ids
    assert _BLOCK_B in ids
    assert _BLOCK_C in ids


def test_collect_block_ids_no_duplicates():
    caps = _make_caps_result(criterion_overrides={
        "beperking": _make_criterion_result("beperking", block_ids=[_BLOCK_A, _BLOCK_A]),
    })
    ids = _collect_block_ids(caps)
    assert ids.count(_BLOCK_A) == 1


def test_collect_block_ids_empty_when_no_evidence():
    caps = _make_caps_result(criterion_overrides={
        key: _make_criterion_result(key, block_ids=[])
        for key in CRITERIA_KEYS
    })
    assert _collect_block_ids(caps) == []


def test_collect_block_ids_order_follows_criteria_keys():
    caps = _make_caps_result()
    ids = _collect_block_ids(caps)
    # beperking comes before stakeholders in CRITERIA_KEYS
    assert ids.index(_BLOCK_A) < ids.index(_BLOCK_B)


