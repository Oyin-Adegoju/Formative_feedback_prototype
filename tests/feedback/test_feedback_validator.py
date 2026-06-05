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
# ---------------------------------------------------------------------------
# Valid output passes
# ---------------------------------------------------------------------------


def test_valid_output_returns_feedback_result():
    caps = _make_caps_result(stoplight="green")
    result = validate(_to_json(_valid_llm_output("green")), caps)
    assert result["document_id"] == "doc1"
    assert result["stoplight"] == "green"
    assert result["disclaimer"] == DISCLAIMER


def test_valid_output_document_id_from_caps_not_llm():
    caps = _make_caps_result(stoplight="green", doc_id="real_doc_id")
    payload = _valid_llm_output("green")
    payload["document_id"] = "fake_doc_id"
    result = validate(_to_json(payload), caps)
    assert result["document_id"] == "real_doc_id"


def test_valid_output_stoplight_from_caps_not_llm():
    caps = _make_caps_result(stoplight="yellow")
    result = validate(_to_json(_valid_llm_output("yellow")), caps)
    assert result["stoplight"] == "yellow"


def test_valid_output_disclaimer_always_injected():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload.pop("disclaimer", None)
    result = validate(_to_json(payload), caps)
    assert result["disclaimer"] == DISCLAIMER


def test_valid_output_with_red_stoplight():
    caps = _make_caps_result(stoplight="red")
    result = validate(_to_json(_valid_llm_output("red")), caps)
    assert result["stoplight"] == "red"


def test_valid_output_empty_feedback_list_is_allowed():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feedback"] = []
    result = validate(_to_json(payload), caps)
    assert result["feedback"] == []


def test_valid_output_empty_feed_forward_is_allowed():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feed_forward"] = []
    result = validate(_to_json(payload), caps)
    assert result["feed_forward"] == []


def test_valid_output_empty_evidence_ref_is_allowed():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feedback"] = [
        {"criterium": "beperking", "observatie": "ok", "evidence_ref": []}
    ]
    result = validate(_to_json(payload), caps)
    assert result["feedback"][0]["evidence_ref"] == []

# ---------------------------------------------------------------------------
# JSON parse failure
# ---------------------------------------------------------------------------


def test_malformed_json_raises():
    caps = _make_caps_result()
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate("this is not json", caps)
    assert "JSON parse error" in exc_info.value.reason


def test_json_array_instead_of_object_raises():
    caps = _make_caps_result()
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate("[]", caps)
    assert "not a JSON object" in exc_info.value.reason


def test_validation_error_carries_raw_string():
    caps = _make_caps_result()
    raw = "not json at all"
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(raw, caps)
    assert exc_info.value.raw == raw

# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", [
    "student_samenvatting",
    "docent_toelichting",
    "feed_up",
    "feedback",
    "feed_forward",
    "taalgebruik",
])
def test_missing_required_field_raises(field: str):
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    del payload[field]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Missing required fields" in exc_info.value.reason
    assert field in exc_info.value.reason


# ---------------------------------------------------------------------------
# Empty student_samenvatting
# ---------------------------------------------------------------------------


def test_empty_student_samenvatting_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["student_samenvatting"] = ""
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "student_samenvatting is empty" in exc_info.value.reason


def test_whitespace_only_student_samenvatting_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["student_samenvatting"] = "   "
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "student_samenvatting is empty" in exc_info.value.reason
# ---------------------------------------------------------------------------
# Stoplight mismatch
# ---------------------------------------------------------------------------


def test_stoplight_mismatch_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("red")
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Stoplight mismatch" in exc_info.value.reason
    assert "red" in exc_info.value.reason
    assert "green" in exc_info.value.reason


def test_stoplight_missing_from_llm_output_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    del payload["stoplight"]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Stoplight mismatch" in exc_info.value.reason


# ---------------------------------------------------------------------------
# Score leak detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,field", [
    ("Je hebt 8/10 gehaald op dit onderdeel.", "student_samenvatting"),
    ("De score is score: 12 punten.", "docent_toelichting"),
    ("Het cijfer: 7 is voldoende.", "feed_up"),
    ("Goed gedaan, score: 3 voor security.", "taalgebruik"),
    ("Je scoort 12/15 op de rubric.", "student_samenvatting"),
])
def test_score_leak_in_text_field_raises(text: str, field: str):
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload[field] = text
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Score leak" in exc_info.value.reason


def test_score_leak_in_feed_forward_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feed_forward"] = ["Goed gedaan!", "Je hebt score: 9 gehaald."]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Score leak" in exc_info.value.reason


def test_score_leak_in_observatie_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feedback"] = [
        {
            "criterium": "beperking",
            "observatie": "Je hebt 4/10 voor dit criterium.",
            "evidence_ref": [],
        }
    ]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Score leak" in exc_info.value.reason


def test_legitimate_numbers_do_not_trigger_score_leak():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["student_samenvatting"] = (
        "Je document bevat 4 stakeholders en 15 requirements."
    )
    result = validate(_to_json(payload), caps)
    assert result is not None
# ---------------------------------------------------------------------------
# Disclaimer
# ---------------------------------------------------------------------------


def test_modified_disclaimer_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["disclaimer"] = "Dit is een officiële beoordeling."
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Disclaimer was modified" in exc_info.value.reason


def test_correct_disclaimer_in_output_passes():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["disclaimer"] = DISCLAIMER
    result = validate(_to_json(payload), caps)
    assert result["disclaimer"] == DISCLAIMER


def test_absent_disclaimer_is_injected():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    assert "disclaimer" not in payload
    result = validate(_to_json(payload), caps)
    assert result["disclaimer"] == DISCLAIMER


# ---------------------------------------------------------------------------
# feedback list validation
# ---------------------------------------------------------------------------


def test_feedback_not_a_list_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feedback"] = "not a list"
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "'feedback' must be a list" in exc_info.value.reason


def test_unknown_criterion_key_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feedback"] = [
        {"criterium": "verzonnen_criterium", "observatie": "ok", "evidence_ref": []}
    ]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Unknown criterion key" in exc_info.value.reason
    assert "verzonnen_criterium" in exc_info.value.reason


def test_missing_criterium_field_in_entry_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feedback"] = [{"observatie": "ok", "evidence_ref": []}]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "criterium" in exc_info.value.reason


def test_unknown_evidence_ref_block_id_raises():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feedback"] = [
        {
            "criterium": "beperking",
            "observatie": "ok",
            "evidence_ref": ["hallucinated_block_id"],
        }
    ]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), caps)
    assert "Unknown evidence_ref block ID" in exc_info.value.reason
    assert "hallucinated_block_id" in exc_info.value.reason


def test_known_evidence_ref_passes():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    payload["feedback"] = [
        {
            "criterium": "beperking",
            "observatie": "ok",
            "evidence_ref": [_BLOCK_A],
        }
    ]
    result = validate(_to_json(payload), caps)
    assert result["feedback"][0]["evidence_ref"] == [_BLOCK_A]


def test_evidence_ref_from_different_criterion_passes():
    caps = _make_caps_result(stoplight="green")
    payload = _valid_llm_output("green")
    # _BLOCK_B belongs to stakeholders, but beperking entry references it — still valid
    payload["feedback"] = [
        {
            "criterium": "beperking",
            "observatie": "ok",
            "evidence_ref": [_BLOCK_B],
        }
    ]
    result = validate(_to_json(payload), caps)
    assert result["feedback"][0]["evidence_ref"] == [_BLOCK_B]