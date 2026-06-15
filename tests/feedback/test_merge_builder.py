"""Tests for src/feedback/merge_builder.py and the merged → feedback prompt.

Covers the deterministic aggregation (judgement→stoplight mapping, manual_review
guard, count-floor guard, overall stoplight), the merged contract shape, the
criteria_requiring_extra_review list, the absence of legacy CAPS verdict fields,
and feedback-writer prompt assembly from the merged input.
"""

from __future__ import annotations

import json

import pytest

from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback import feedback_builder
from src.feedback.merge_builder import (
    apply_count_floor,
    apply_manual_review_guard,
    build_merged_feedback_input,
    collect_known_block_ids,
    derive_criterion_stoplight,
    derive_overall_stoplight,
    map_judgement_to_stoplight,
)


def _handoff(counts: dict[str, int | None] | None = None) -> dict:
    """A pre-Qwen extraction handoff. Default counts are above every minimum so
    the count floor never fires unless a test sets a low count explicitly."""
    base = {"stakeholders": 10, "requirements": 20, "security": 3}
    base.update(counts or {})
    return {
        "document_id": "doc7",
        "source_name": "report.pdf",
        "criteria": {
            key: {
                "count": base.get(key),
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


def _quality(
    judgements: dict[str, str] | None = None,
    review_keys: set[str] | None = None,
) -> dict:
    """Quality diagnosis fixture. Default judgement is 'strong' for every
    criterion (→ green); override per key via `judgements`."""
    judgements = judgements or {}
    review_keys = review_keys or set()
    return {
        "document_id": "doc7",
        "criteria": {
            key: {
                "criterion_judgement": judgements.get(key, "strong"),
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


def _overall(handoff: dict, quality: dict) -> str:
    return build_merged_feedback_input(handoff, quality)["final_stoplight"]


def _stoplight(merged: dict, key: str) -> str:
    return merged["criteria"][key]["criterion_stoplight"]


# ---------------------------------------------------------------------------
# Pure helper: judgement → base stoplight
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("judgement,expected", [
    ("strong", "green"),
    ("mixed", "yellow"),
    ("weak", "red"),
])
def test_map_judgement_to_stoplight(judgement, expected):
    assert map_judgement_to_stoplight(judgement) == expected


def test_map_unknown_judgement_defaults_to_yellow():
    assert map_judgement_to_stoplight("???") == "yellow"


# ---------------------------------------------------------------------------
# Pure helper: manual_review guard
# ---------------------------------------------------------------------------


def test_manual_review_downgrades_green_to_yellow():
    assert apply_manual_review_guard("green", True) == "yellow"


def test_manual_review_no_op_when_false():
    assert apply_manual_review_guard("green", False) == "green"


def test_manual_review_never_changes_yellow_or_red():
    assert apply_manual_review_guard("yellow", True) == "yellow"
    assert apply_manual_review_guard("red", True) == "red"


# ---------------------------------------------------------------------------
# Pure helper: count floor
# ---------------------------------------------------------------------------


def test_count_floor_forces_red_when_below_minimum():
    assert apply_count_floor("stakeholders", "green", 3, 4) == "red"
    assert apply_count_floor("requirements", "yellow", 10, 15) == "red"


def test_count_floor_no_op_when_at_or_above_minimum():
    assert apply_count_floor("stakeholders", "green", 4, 4) == "green"
    assert apply_count_floor("requirements", "green", 20, 15) == "green"


def test_count_floor_not_applied_to_non_floored_criteria():
    # security/beperking/taalkeuze are never floored, even below their minimum.
    assert apply_count_floor("security", "green", 0, 1) == "green"
    assert apply_count_floor("beperking", "green", None, None) == "green"
    assert apply_count_floor("taalkeuze", "green", None, None) == "green"


def test_count_floor_no_op_when_count_unknown():
    assert apply_count_floor("stakeholders", "green", None, 4) == "green"


# ---------------------------------------------------------------------------
# Pure helper: per-criterion derivation (full chain) + overall
# ---------------------------------------------------------------------------


def test_derive_criterion_stoplight_chain_floor_wins_over_judgement():
    # strong (→green) but stakeholders count below minimum → red.
    assert derive_criterion_stoplight("stakeholders", "strong", False, 3, 4) == "red"


def test_derive_criterion_stoplight_chain_manual_review_then_no_floor():
    # strong (→green) + manual_review → yellow; count ok so floor no-op.
    assert derive_criterion_stoplight("stakeholders", "strong", True, 10, 4) == "yellow"


@pytest.mark.parametrize("stoplights,expected", [
    (["green", "green", "green", "green", "green"], "green"),
    (["green", "yellow", "green", "green", "green"], "yellow"),
    (["green", "yellow", "red", "green", "green"], "red"),
    (["red", "red", "red", "red", "red"], "red"),
])
def test_derive_overall_stoplight(stoplights, expected):
    assert derive_overall_stoplight(stoplights) == expected


# ---------------------------------------------------------------------------
# Approved worked scenarios (end-to-end through build_merged_feedback_input)
# ---------------------------------------------------------------------------


def test_one_weak_rest_strong_is_overall_red():
    merged = build_merged_feedback_input(
        _handoff(), _quality({"security": "weak"})
    )
    assert _stoplight(merged, "security") == "red"
    assert merged["final_stoplight"] == "red"


def test_all_strong_one_manual_review_is_overall_yellow():
    merged = build_merged_feedback_input(
        _handoff(), _quality(review_keys={"beperking"})
    )
    assert _stoplight(merged, "beperking") == "yellow"
    assert merged["final_stoplight"] == "yellow"


def test_two_mixed_no_weak_is_overall_yellow():
    merged = build_merged_feedback_input(
        _handoff(), _quality({"taalkeuze": "mixed", "security": "mixed"})
    )
    assert _stoplight(merged, "taalkeuze") == "yellow"
    assert _stoplight(merged, "security") == "yellow"
    assert merged["final_stoplight"] == "yellow"


def test_one_mixed_one_weak_is_overall_red():
    merged = build_merged_feedback_input(
        _handoff(), _quality({"stakeholders": "mixed", "requirements": "weak"})
    )
    # stakeholders count default (10) ≥ min 4 → judgement mixed → yellow.
    assert _stoplight(merged, "stakeholders") == "yellow"
    assert _stoplight(merged, "requirements") == "red"
    assert merged["final_stoplight"] == "red"


def test_all_strong_no_manual_review_is_overall_green():
    merged = build_merged_feedback_input(_handoff(), _quality())
    assert all(
        merged["criteria"][k]["criterion_stoplight"] == "green" for k in CRITERIA_KEYS
    )
    assert merged["final_stoplight"] == "green"


def test_stakeholders_count_floor_overrides_strong():
    merged = build_merged_feedback_input(
        _handoff({"stakeholders": 3}), _quality()  # 3 < minimum 4
    )
    assert _stoplight(merged, "stakeholders") == "red"
    assert merged["final_stoplight"] == "red"


def test_requirements_count_floor_overrides_strong():
    merged = build_merged_feedback_input(
        _handoff({"requirements": 10}), _quality()  # 10 < minimum 15
    )
    assert _stoplight(merged, "requirements") == "red"
    assert merged["final_stoplight"] == "red"


def test_security_below_minimum_is_not_floored():
    # security has minimum 1 but is NOT a floored criterion: strong stays green.
    merged = build_merged_feedback_input(
        _handoff({"security": 0}), _quality()
    )
    assert _stoplight(merged, "security") == "green"
    assert merged["final_stoplight"] == "green"


def test_manual_review_does_not_turn_yellow_into_red():
    # mixed (→yellow) with manual_review stays yellow, never red.
    merged = build_merged_feedback_input(
        _handoff(), _quality({"taalkeuze": "mixed"}, review_keys={"taalkeuze"})
    )
    assert _stoplight(merged, "taalkeuze") == "yellow"


# ---------------------------------------------------------------------------
# Merged contract shape
# ---------------------------------------------------------------------------


def test_merged_has_all_top_level_keys():
    merged = build_merged_feedback_input(_handoff(), _quality())
    for key in (
        "document_id", "source_name", "final_stoplight",
        "criteria_requiring_extra_review", "criteria",
    ):
        assert key in merged
    # The CAPS-pre-verdict blockers_triggered is no longer part of the contract.
    assert "blockers_triggered" not in merged
    assert set(merged["criteria"].keys()) == set(CRITERIA_KEYS)


def test_merged_criterion_merges_caps_and_qwen():
    merged = build_merged_feedback_input(
        _handoff(), _quality({"stakeholders": "mixed"})
    )
    crit = merged["criteria"]["stakeholders"]
    # CAPS-sourced (extraction only — no status/stoplight verdict).
    assert "caps_status" not in crit
    assert "caps_stoplight" not in crit
    assert crit["count"] == 10
    assert crit["caps_notes"] == ["caps note for stakeholders"]
    assert crit["evidence_items"][0]["block_id"] == "stakeholders_b1"
    # Qwen-sourced + derived
    assert crit["qwen_strengths"] == ["sterk punt stakeholders"]
    assert crit["qwen_weaknesses"] == ["zwak punt stakeholders"]
    assert crit["criterion_judgement"] == "mixed"
    assert crit["criterion_stoplight"] == "yellow"
    assert crit["manual_review"] is False


def test_review_list_matches_manual_review_flags():
    merged = build_merged_feedback_input(
        _handoff(), _quality(review_keys={"security", "taalkeuze"})
    )
    assert sorted(merged["criteria_requiring_extra_review"]) == ["security", "taalkeuze"]
    assert merged["final_stoplight"] == "yellow"


def test_merged_has_no_legacy_or_caps_verdict_fields():
    """The old CAPS manual_review_required / manual_review_flags / hidden_score
    contract AND the removed pre-Qwen CAPS verdicts must never reappear in the
    merged input."""
    merged = build_merged_feedback_input(_handoff(), _quality(review_keys={"beperking"}))
    blob = json.dumps(merged)
    for forbidden in (
        "manual_review_required", "manual_review_flags", "hidden_score",
        "overall_stoplight", "blockers_triggered", "caps_status", "caps_stoplight",
    ):
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
    merged = dict(build_merged_feedback_input(
        _handoff(), _quality(review_keys={"security"})
    ))
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
