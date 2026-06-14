"""Tests for src/feedback/merge_builder.py and the merged → feedback prompt.

Covers the merged contract shape, final_stoplight derivation, the
criteria_requiring_extra_review list, the absence of legacy CAPS manual_review
fields, and feedback-writer prompt assembly from the merged input.
"""

from __future__ import annotations

import json

import pytest

from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback import feedback_builder
from src.feedback.merge_builder import (
    build_merged_feedback_input,
    collect_known_block_ids,
    derive_final_stoplight,
)


def _handoff(overall_stoplight: str = "green", blockers: list[str] | None = None) -> dict:
    return {
        "document_id": "doc7",
        "source_name": "report.pdf",
        "overall_stoplight": overall_stoplight,
        "blockers_triggered": blockers or [],
        "criteria": {
            key: {
                "status": "sufficient",
                "stoplight": "yellow",
                "count": (4 if key == "stakeholders" else None),
                "notes": [f"caps note for {key}"],
                "missing_signals": [],
                "evidence_items": [
                    {
                        "block_id": f"{key}_b1",
                        "page_no": 1,
                        "block_type": "paragraph",
                        "heading_path": [],
                        "excerpt": "x",
                        "selection_reason": "r",
                        "signal_class": "positive",
                    }
                ],
            }
            for key in CRITERIA_KEYS
        },
    }


def _quality(review_keys: set[str] | None = None) -> dict:
    review_keys = review_keys or set()
    return {
        "document_id": "doc7",
        "criteria": {
            key: {
                "diagnostics": {"concreteness": "middel"},
                "strengths": [f"sterk punt {key}"],
                "weaknesses": [f"zwak punt {key}"],
                "manual_review": key in review_keys,
                "manual_review_reason": (["reden"] if key in review_keys else []),
                "reason": "samenvattende reden",
            }
            for key in CRITERIA_KEYS
        },
    }


# ---------------------------------------------------------------------------
# final_stoplight derivation
# ---------------------------------------------------------------------------


def test_derive_red_when_caps_red():
    assert derive_final_stoplight("red", _quality({"security"})["criteria"]) == "red"


def test_derive_yellow_when_green_but_manual_review():
    assert derive_final_stoplight("green", _quality({"beperking"})["criteria"]) == "yellow"


def test_derive_green_when_green_and_no_review():
    assert derive_final_stoplight("green", _quality(set())["criteria"]) == "green"


# ---------------------------------------------------------------------------
# Merged contract shape
# ---------------------------------------------------------------------------


def test_merged_has_all_top_level_keys():
    merged = build_merged_feedback_input(_handoff(), _quality())
    for key in (
        "document_id", "source_name", "final_stoplight",
        "blockers_triggered", "criteria_requiring_extra_review", "criteria",
    ):
        assert key in merged
    assert set(merged["criteria"].keys()) == set(CRITERIA_KEYS)


def test_merged_criterion_merges_caps_and_qwen():
    merged = build_merged_feedback_input(_handoff(), _quality())
    crit = merged["criteria"]["stakeholders"]
    # CAPS-sourced
    assert crit["caps_status"] == "sufficient"
    assert crit["caps_stoplight"] == "yellow"
    assert crit["count"] == 4
    assert crit["caps_notes"] == ["caps note for stakeholders"]
    assert crit["evidence_items"][0]["block_id"] == "stakeholders_b1"
    # Qwen-sourced
    assert crit["qwen_strengths"] == ["sterk punt stakeholders"]
    assert crit["qwen_weaknesses"] == ["zwak punt stakeholders"]
    assert crit["manual_review"] is False


def test_review_list_matches_manual_review_flags():
    merged = build_merged_feedback_input(_handoff(), _quality({"security", "taalkeuze"}))
    assert sorted(merged["criteria_requiring_extra_review"]) == ["security", "taalkeuze"]
    assert merged["final_stoplight"] == "yellow"


def test_merged_has_no_legacy_manual_review_or_hidden_score_fields():
    """The old CAPS manual_review_required / manual_review_flags / hidden_score
    contract must never reappear anywhere in the merged input."""
    merged = build_merged_feedback_input(_handoff(), _quality({"beperking"}))
    blob = json.dumps(merged)
    for forbidden in ("manual_review_required", "manual_review_flags", "hidden_score"):
        assert f'"{forbidden}"' not in blob


def test_collect_known_block_ids_from_merged():
    merged = build_merged_feedback_input(_handoff(), _quality())
    ids = collect_known_block_ids(merged)
    assert ids == {f"{key}_b1" for key in CRITERIA_KEYS}


def test_missing_criterion_in_quality_raises():
    quality = _quality()
    del quality["criteria"]["security"]
    with pytest.raises(KeyError):
        build_merged_feedback_input(_handoff(), quality)


# ---------------------------------------------------------------------------
# Feedback-writer prompt assembly from the merged input
# ---------------------------------------------------------------------------


def test_feedback_prompt_fills_stoplight_keys_and_merged_json():
    merged = dict(build_merged_feedback_input(_handoff(), _quality({"security"})))
    template = (
        "stop=<<STOPLIGHT>>\n"
        "keys=<<CRITERION_KEYS>>\n"
        "merged=<<MERGED_FEEDBACK_INPUT_JSON>>"
    )
    prompt = feedback_builder._assemble_prompt(merged, template)
    assert "stop=yellow" in prompt
    for key in CRITERIA_KEYS:
        assert key in prompt
    assert "<<STOPLIGHT>>" not in prompt
    assert "<<MERGED_FEEDBACK_INPUT_JSON>>" not in prompt
    merged_json = prompt.split("merged=", 1)[1]
    assert json.loads(merged_json)["final_stoplight"] == "yellow"


def test_real_feedback_template_has_no_placeholders_after_assembly():
    merged = dict(build_merged_feedback_input(_handoff(), _quality()))
    template = feedback_builder._PROMPT_PATH.read_text(encoding="utf-8")
    prompt = feedback_builder._assemble_prompt(merged, template)
    assert "<<STOPLIGHT>>" not in prompt
    assert "<<CRITERION_KEYS>>" not in prompt
    assert "<<MERGED_FEEDBACK_INPUT_JSON>>" not in prompt
