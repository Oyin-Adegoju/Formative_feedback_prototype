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


