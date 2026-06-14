"""Tests for src/quality/quality_validator.py."""

from __future__ import annotations

import json

import pytest

from src.caps.criterion_specs import CRITERIA_KEYS
from src.quality.output_schema import QUALITY_DIMENSIONS
from src.quality.quality_validator import QualityValidationError, validate


def _criterion(key: str, manual_review: bool = False) -> dict:
    return {
        "diagnostics": {dim: "middel" for dim in QUALITY_DIMENSIONS[key]},
        "strengths": ["Concreet beschreven onderdeel."],
        "weaknesses": ["Onderbouwing kan steviger."],
        "manual_review": manual_review,
        "manual_review_reason": (["Dunne evidence."] if manual_review else []),
        "reason": "De evidence is grotendeels concreet maar niet volledig.",
    }


def _valid_quality() -> dict:
    return {
        "document_id": "ignored_by_validator",
        "criteria": {key: _criterion(key) for key in CRITERIA_KEYS},
    }


def _to_json(d: dict) -> str:
    return json.dumps(d)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_quality_passes_and_sets_document_id():
    result = validate(_to_json(_valid_quality()), document_id="real_doc")
    assert result["document_id"] == "real_doc"  # overridden, not from LLM
    assert set(result["criteria"].keys()) == set(CRITERIA_KEYS)


def test_manual_review_true_is_preserved():
    payload = _valid_quality()
    payload["criteria"]["security"] = _criterion("security", manual_review=True)
    result = validate(_to_json(payload), document_id="doc")
    assert result["criteria"]["security"]["manual_review"] is True


# ---------------------------------------------------------------------------
# JSON / structure
# ---------------------------------------------------------------------------


def test_malformed_json_raises():
    with pytest.raises(QualityValidationError) as exc:
        validate("not json", document_id="doc")
    assert "JSON parse error" in exc.value.reason


def test_missing_criteria_object_raises():
    with pytest.raises(QualityValidationError) as exc:
        validate(json.dumps({"document_id": "x"}), document_id="doc")
    assert "missing a 'criteria' object" in exc.value.reason


def test_wrong_criteria_keys_raises():
    payload = _valid_quality()
    payload["criteria"].pop("security")
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "criteria keys mismatch" in exc.value.reason


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def test_wrong_dimensions_raises():
    payload = _valid_quality()
    payload["criteria"]["beperking"]["diagnostics"].pop("concreteness")
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "dimensions mismatch" in exc.value.reason


def test_dropped_security_dimension_raises():
    """Reproduces the real Qwen failure: security missing explanation_quality."""
    payload = _valid_quality()
    payload["criteria"]["security"]["diagnostics"].pop("explanation_quality")
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "security" in exc.value.reason
    assert "dimensions mismatch" in exc.value.reason


def test_invalid_diagnostic_level_raises():
    payload = _valid_quality()
    payload["criteria"]["beperking"]["diagnostics"]["concreteness"] = "uitstekend"
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "invalid value" in exc.value.reason


def test_numeric_diagnostic_level_raises():
    payload = _valid_quality()
    payload["criteria"]["requirements"]["diagnostics"]["testability"] = "3"
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "invalid value" in exc.value.reason


# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------


def test_manual_review_not_bool_raises():
    payload = _valid_quality()
    payload["criteria"]["beperking"]["manual_review"] = "true"
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "manual_review must be a boolean" in exc.value.reason


def test_strengths_not_list_raises():
    payload = _valid_quality()
    payload["criteria"]["beperking"]["strengths"] = "een sterk punt"
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "strengths must be a list of strings" in exc.value.reason


# ---------------------------------------------------------------------------
# Leak detection
# ---------------------------------------------------------------------------


def test_score_leak_in_weaknesses_raises():
    payload = _valid_quality()
    payload["criteria"]["security"]["weaknesses"] = ["Slechts 4/10 uitgewerkt."]
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "Score leak" in exc.value.reason


def test_caps_status_label_leak_in_reason_raises():
    payload = _valid_quality()
    payload["criteria"]["security"]["reason"] = "De evidence is partial uitgewerkt."
    with pytest.raises(QualityValidationError) as exc:
        validate(_to_json(payload), document_id="doc")
    assert "status label" in exc.value.reason
