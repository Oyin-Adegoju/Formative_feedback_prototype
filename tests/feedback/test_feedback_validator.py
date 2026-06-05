"""Tests for src/feedback/feedback_validator.py."""

from __future__ import annotations

import json

import pytest

from src.caps.models import (
    CapsRunMeta,
    CapsRunResult,
    CapsScorecard,
    CriterionResult,
    EvidenceRef,
)
from src.feedback.feedback_validator import (
    FeedbackValidationError,
    _collect_known_block_ids,
    _llm_text_fields,
    validate,
)
from src.feedback.output_schema import DISCLAIMER

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BLOCK_A = "doc1_0001"
_BLOCK_B = "doc1_0002"
_BLOCK_C = "doc1_0003"


def _make_criterion_result(
    key: str,
    status: str = "sufficient",
    block_ids: list[str] | None = None,
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
        manual_review=manual_review,
    )


def _make_caps_result(
    stoplight: str = "green",
    doc_id: str = "doc1",
    criterion_block_ids: dict[str, list[str]] | None = None,
) -> CapsRunResult:
    """Build a minimal CapsRunResult for testing."""
    bids = criterion_block_ids or {
        "beperking": [_BLOCK_A],
        "stakeholders": [_BLOCK_B],
        "requirements": [_BLOCK_C],
        "taalkeuze": [],
        "security": [],
    }
    results = {
        key: _make_criterion_result(key, block_ids=bids.get(key, []))
        for key in ("beperking", "stakeholders", "requirements", "taalkeuze", "security")
    }
    scorecard = CapsScorecard(doc_id=doc_id, results=results, hidden_score=10)
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
    )


def _valid_llm_output(stoplight: str = "green") -> dict:
    """Return a dict that passes all guardrails."""
    return {
        "stoplight": stoplight,
        "student_samenvatting": "Je document voldoet aan de basisvereisten.",
        "docent_toelichting": "Het document is volledig en goed onderbouwd.",
        "feed_up": "Het doel is een volledig requirements-document met alle vijf criteria.",
        "feedback": [
            {
                "criterium": "beperking",
                "observatie": "De beperking is duidelijk beschreven.",
                "evidence_ref": [_BLOCK_A],
            },
            {
                "criterium": "stakeholders",
                "observatie": "De stakeholders zijn goed in kaart gebracht.",
                "evidence_ref": [_BLOCK_B],
            },
        ],
        "feed_forward": [
            "Voeg meer bronnen toe aan de deskresearch.",
            "Werk de gevolgen van de taalkeuze verder uit.",
        ],
        "taalgebruik": "Het document is helder geschreven.",
    }


def _to_json(d: dict) -> str:
    return json.dumps(d)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_collect_known_block_ids_returns_all_ids():
    caps = _make_caps_result(criterion_block_ids={
        "beperking": [_BLOCK_A, _BLOCK_B],
        "stakeholders": [_BLOCK_C],
        "requirements": [],
        "taalkeuze": [],
        "security": [],
    })
    ids = _collect_known_block_ids(caps)
    assert ids == {_BLOCK_A, _BLOCK_B, _BLOCK_C}


def test_collect_known_block_ids_empty_when_no_evidence():
    caps = _make_caps_result(criterion_block_ids={
        k: [] for k in ("beperking", "stakeholders", "requirements", "taalkeuze", "security")
    })
    assert _collect_known_block_ids(caps) == frozenset()


def test_llm_text_fields_concatenates_all_text_fields():
    data = {
        "student_samenvatting": "samenvatting",
        "docent_toelichting": "toelichting",
        "feed_up": "doel",
        "taalgebruik": "stijl",
        "feed_forward": ["tip1", "tip2"],
        "feedback": [
            {"observatie": "obs1", "evidence_ref": ["doc1_0001"]},
            {"observatie": "obs2", "evidence_ref": []},
        ],
    }
    result = _llm_text_fields(data)
    assert "samenvatting" in result
    assert "toelichting" in result
    assert "doel" in result
    assert "stijl" in result
    assert "tip1" in result
    assert "tip2" in result
    assert "obs1" in result
    assert "obs2" in result


def test_llm_text_fields_excludes_evidence_ref_ids():
    data = {
        "student_samenvatting": "",
        "docent_toelichting": "",
        "feed_up": "",
        "taalgebruik": "",
        "feed_forward": [],
        "feedback": [{"observatie": "", "evidence_ref": ["doc1_0001"]}],
    }
    result = _llm_text_fields(data)
    assert "doc1_0001" not in result


