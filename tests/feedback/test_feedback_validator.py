"""Tests for src/feedback/feedback_validator.py.

The validator now consumes the MERGED CAPS + Qwen contract
(src/feedback/merge_builder.py), not a raw CapsRunResult. Fixtures build a
minimal merged dict directly.
"""

from __future__ import annotations

import json

import pytest

from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.feedback_validator import FeedbackValidationError, validate
from src.feedback.merge_builder import collect_known_block_ids
from src.feedback.output_schema import DISCLAIMER

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BLOCK_A = "doc1_0001"
_BLOCK_B = "doc1_0002"
_BLOCK_C = "doc1_0003"


def _evidence_item(block_id: str) -> dict:
    return {
        "block_id": block_id,
        "page_no": 1,
        "block_type": "paragraph",
        "heading_path": [],
        "excerpt": "",
        "selection_reason": "",
        "signal_class": "positive",
    }


def _make_merged(
    final_stoplight: str = "green",
    doc_id: str = "doc1",
    criterion_block_ids: dict[str, list[str]] | None = None,
) -> dict:
    """Build a minimal merged feedback input for testing the validator."""
    bids = criterion_block_ids or {
        "beperking": [_BLOCK_A],
        "stakeholders": [_BLOCK_B],
        "requirements": [_BLOCK_C],
        "taalkeuze": [],
        "security": [],
    }
    criteria = {
        key: {
            "caps_status": "sufficient",
            "caps_stoplight": "yellow",
            "count": None,
            "caps_notes": [],
            "missing_signals": [],
            "evidence_items": [_evidence_item(b) for b in bids.get(key, [])],
            "qwen_diagnostics": {},
            "qwen_strengths": [],
            "qwen_weaknesses": [],
            "manual_review": False,
            "manual_review_reason": [],
        }
        for key in CRITERIA_KEYS
    }
    return {
        "document_id": doc_id,
        "source_name": "test.pdf",
        "final_stoplight": final_stoplight,
        "blockers_triggered": [],
        "criteria_requiring_extra_review": [],
        "criteria": criteria,
    }


def _valid_llm_output(stoplight: str = "green") -> dict:
    """Return a dict that passes all guardrails (all 5 criteria present)."""
    return {
        "stoplight": stoplight,
        "student_samenvatting": "Je document voldoet aan de basisvereisten.",
        "docent_toelichting": f"Het stoplicht is {stoplight}, de docent kan een globale controle uitvoeren.",
        "feed_up": "Het doel is een volledig requirements-document met alle vijf criteria.",
        "feedback": [
            {"criterium": "beperking", "observatie": "De beperking is duidelijk beschreven.", "evidence_ref": [_BLOCK_A]},
            {"criterium": "stakeholders", "observatie": "De stakeholders zijn goed in kaart gebracht.", "evidence_ref": [_BLOCK_B]},
            {"criterium": "requirements", "observatie": "De requirements zijn volledig uitgewerkt.", "evidence_ref": [_BLOCK_C]},
            {"criterium": "taalkeuze", "observatie": "De taalkeuze is verantwoord.", "evidence_ref": []},
            {"criterium": "security", "observatie": "De beveiligingsmaatregelen zijn beschreven.", "evidence_ref": []},
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
# collect_known_block_ids (now lives in merge_builder)
# ---------------------------------------------------------------------------


def test_collect_known_block_ids_returns_all_ids():
    merged = _make_merged(criterion_block_ids={
        "beperking": [_BLOCK_A, _BLOCK_B],
        "stakeholders": [_BLOCK_C],
        "requirements": [],
        "taalkeuze": [],
        "security": [],
    })
    assert collect_known_block_ids(merged) == {_BLOCK_A, _BLOCK_B, _BLOCK_C}


def test_collect_known_block_ids_empty_when_no_evidence():
    merged = _make_merged(criterion_block_ids={k: [] for k in CRITERIA_KEYS})
    assert collect_known_block_ids(merged) == frozenset()


# ---------------------------------------------------------------------------
# Valid output passes
# ---------------------------------------------------------------------------


def test_valid_output_returns_feedback_result():
    merged = _make_merged(final_stoplight="green")
    result = validate(_to_json(_valid_llm_output("green")), merged)
    assert result["document_id"] == "doc1"
    assert result["stoplight"] == "green"
    assert result["disclaimer"] == DISCLAIMER


def test_valid_output_document_id_from_merged_not_llm():
    merged = _make_merged(final_stoplight="green", doc_id="real_doc_id")
    payload = _valid_llm_output("green")
    payload["document_id"] = "fake_doc_id"
    result = validate(_to_json(payload), merged)
    assert result["document_id"] == "real_doc_id"


def test_valid_output_stoplight_from_merged_yellow():
    merged = _make_merged(final_stoplight="yellow")
    result = validate(_to_json(_valid_llm_output("yellow")), merged)
    assert result["stoplight"] == "yellow"


def test_valid_output_with_red_stoplight():
    merged = _make_merged(final_stoplight="red")
    result = validate(_to_json(_valid_llm_output("red")), merged)
    assert result["stoplight"] == "red"


def test_valid_output_empty_feed_forward_is_allowed():
    merged = _make_merged(final_stoplight="green")
    payload = _valid_llm_output("green")
    payload["feed_forward"] = []
    result = validate(_to_json(payload), merged)
    assert result["feed_forward"] == []


def test_valid_output_empty_evidence_ref_is_allowed():
    merged = _make_merged(final_stoplight="green")
    payload = _valid_llm_output("green")
    for entry in payload["feedback"]:
        entry["evidence_ref"] = []
    result = validate(_to_json(payload), merged)
    assert all(entry["evidence_ref"] == [] for entry in result["feedback"])


# ---------------------------------------------------------------------------
# JSON parse failure
# ---------------------------------------------------------------------------


def test_malformed_json_raises():
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate("this is not json", _make_merged())
    assert "JSON parse error" in exc_info.value.reason


def test_json_array_instead_of_object_raises():
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate("[]", _make_merged())
    assert "not a JSON object" in exc_info.value.reason


def test_validation_error_carries_raw_string():
    raw = "not json at all"
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(raw, _make_merged())
    assert exc_info.value.raw == raw


# ---------------------------------------------------------------------------
# Missing required fields / empty samenvatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", [
    "student_samenvatting", "docent_toelichting", "feed_up",
    "feedback", "feed_forward", "taalgebruik",
])
def test_missing_required_field_raises(field: str):
    payload = _valid_llm_output("green")
    del payload[field]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Missing required fields" in exc_info.value.reason
    assert field in exc_info.value.reason


def test_empty_student_samenvatting_raises():
    payload = _valid_llm_output("green")
    payload["student_samenvatting"] = "   "
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "student_samenvatting is empty" in exc_info.value.reason


# ---------------------------------------------------------------------------
# Stoplight mismatch (against merged final_stoplight)
# ---------------------------------------------------------------------------


def test_stoplight_mismatch_raises():
    merged = _make_merged(final_stoplight="green")
    payload = _valid_llm_output("red")
    payload["docent_toelichting"] = "Het stoplicht is green, lichte controle volstaat."
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), merged)
    assert "Stoplight mismatch" in exc_info.value.reason


def test_stoplight_missing_from_llm_output_raises():
    payload = _valid_llm_output("green")
    del payload["stoplight"]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Missing required fields" in exc_info.value.reason
    assert "stoplight" in exc_info.value.reason


def test_yellow_final_stoplight_round_trips():
    """A merge-derived yellow stoplight must validate cleanly end to end."""
    merged = _make_merged(final_stoplight="yellow")
    payload = _valid_llm_output("yellow")
    payload["docent_toelichting"] = "Het stoplicht is yellow, controleer het document gericht."
    result = validate(_to_json(payload), merged)
    assert result["stoplight"] == "yellow"


# ---------------------------------------------------------------------------
# Score / status-label leak detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,field", [
    ("Je hebt 8/10 gehaald op dit onderdeel.", "student_samenvatting"),
    ("Het stoplicht is green, score: 12 punten.", "docent_toelichting"),
    ("Het cijfer: 7 is voldoende.", "feed_up"),
    ("Je scoort 12/15 op de rubric.", "student_samenvatting"),
])
def test_score_leak_in_text_field_raises(text: str, field: str):
    payload = _valid_llm_output("green")
    payload[field] = text
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Score leak" in exc_info.value.reason


def test_legitimate_numbers_do_not_trigger_score_leak():
    payload = _valid_llm_output("green")
    payload["student_samenvatting"] = "Je document bevat 4 stakeholders en 15 requirements."
    result = validate(_to_json(payload), _make_merged("green"))
    assert result is not None


def test_internal_status_label_leak_raises():
    payload = _valid_llm_output("green")
    payload["taalgebruik"] = "De requirements zijn partial uitgewerkt."
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Internal status label" in exc_info.value.reason


# ---------------------------------------------------------------------------
# Disclaimer
# ---------------------------------------------------------------------------


def test_modified_disclaimer_raises():
    payload = _valid_llm_output("green")
    payload["disclaimer"] = "Dit is een officiële beoordeling."
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Disclaimer was modified" in exc_info.value.reason


def test_absent_disclaimer_is_injected():
    payload = _valid_llm_output("green")
    assert "disclaimer" not in payload
    result = validate(_to_json(payload), _make_merged("green"))
    assert result["disclaimer"] == DISCLAIMER


# ---------------------------------------------------------------------------
# feedback list validation
# ---------------------------------------------------------------------------


def test_feedback_not_a_list_raises():
    payload = _valid_llm_output("green")
    payload["feedback"] = "not a list"
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "'feedback' must be a list" in exc_info.value.reason


def test_unknown_criterion_key_raises():
    payload = _valid_llm_output("green")
    payload["feedback"][0]["criterium"] = "verzonnen_criterium"
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Unknown criterion key" in exc_info.value.reason


def test_missing_criterium_field_in_entry_raises():
    payload = _valid_llm_output("green")
    payload["feedback"] = [{"observatie": "ok", "evidence_ref": []}]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "criterium" in exc_info.value.reason


def test_unknown_evidence_ref_block_id_raises():
    payload = _valid_llm_output("green")
    payload["feedback"][0]["evidence_ref"] = ["hallucinated_block_id"]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Unknown evidence_ref block ID" in exc_info.value.reason
    assert "hallucinated_block_id" in exc_info.value.reason


def test_known_evidence_ref_passes():
    payload = _valid_llm_output("green")
    payload["feedback"][0]["evidence_ref"] = [_BLOCK_A]
    result = validate(_to_json(payload), _make_merged("green"))
    assert result["feedback"][0]["evidence_ref"] == [_BLOCK_A]


# ---------------------------------------------------------------------------
# feed_forward validation
# ---------------------------------------------------------------------------


def test_feed_forward_not_a_list_raises():
    payload = _valid_llm_output("green")
    payload["feed_forward"] = "geef meer bronnen op"
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "'feed_forward' must be a list" in exc_info.value.reason


# ---------------------------------------------------------------------------
# Output shape + criteria completeness
# ---------------------------------------------------------------------------


def test_output_contains_all_required_keys():
    result = validate(_to_json(_valid_llm_output("green")), _make_merged("green"))
    for key in (
        "document_id", "stoplight", "student_samenvatting", "docent_toelichting",
        "feed_up", "feedback", "feed_forward", "taalgebruik", "disclaimer",
    ):
        assert key in result, f"Missing key in FeedbackResult: {key}"


def test_all_five_criteria_present_passes():
    result = validate(_to_json(_valid_llm_output("green")), _make_merged("green"))
    returned = {e["criterium"] for e in result["feedback"]}
    assert returned == set(CRITERIA_KEYS)


def test_missing_criteria_raises():
    payload = _valid_llm_output("green")
    payload["feedback"] = [{"criterium": "beperking", "observatie": "ok", "evidence_ref": []}]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Missing feedback criteria" in exc_info.value.reason


def test_duplicate_criterion_raises():
    payload = _valid_llm_output("green")
    payload["feedback"] = [
        {"criterium": "beperking", "observatie": "eerste", "evidence_ref": []},
        {"criterium": "beperking", "observatie": "duplicaat", "evidence_ref": []},
        {"criterium": "stakeholders", "observatie": "ok", "evidence_ref": []},
        {"criterium": "requirements", "observatie": "ok", "evidence_ref": []},
        {"criterium": "taalkeuze", "observatie": "ok", "evidence_ref": []},
        {"criterium": "security", "observatie": "ok", "evidence_ref": []},
    ]
    with pytest.raises(FeedbackValidationError) as exc_info:
        validate(_to_json(payload), _make_merged("green"))
    assert "Duplicate" in exc_info.value.reason


# ---------------------------------------------------------------------------
# docent_toelichting must mention the final stoplight (step 3b)
# ---------------------------------------------------------------------------


def test_docent_toelichting_must_contain_final_stoplight():
    for stoplight in ("green", "yellow", "red"):
        payload = _valid_llm_output(stoplight)
        payload["docent_toelichting"] = "Geen stoplicht vermeld hier."
        with pytest.raises(FeedbackValidationError) as exc_info:
            validate(_to_json(payload), _make_merged(stoplight))
        assert "does not mention the final stoplight" in exc_info.value.reason
        assert stoplight in exc_info.value.reason


def test_docent_toelichting_stoplight_case_insensitive():
    payload = _valid_llm_output("green")
    payload["docent_toelichting"] = "Stoplicht: GREEN — lichte controle volstaat."
    result = validate(_to_json(payload), _make_merged("green"))
    assert result is not None
