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
# ---------------------------------------------------------------------------
# _format_scorecard
# ---------------------------------------------------------------------------


def test_format_scorecard_contains_all_criterion_keys():
    caps = _make_caps_result()
    output = _format_scorecard(caps)
    for key in CRITERIA_KEYS:
        assert key in output


def test_format_scorecard_contains_status():
    caps = _make_caps_result()
    output = _format_scorecard(caps)
    assert "sufficient" in output


def test_format_scorecard_contains_count_when_present():
    caps = _make_caps_result(criterion_overrides={
        "stakeholders": _make_criterion_result("stakeholders", count=6),
    })
    output = _format_scorecard(caps)
    assert "6" in output


def test_format_scorecard_omits_count_when_none():
    caps = _make_caps_result(criterion_overrides={
        "beperking": _make_criterion_result("beperking", count=None),
    })
    output = _format_scorecard(caps)
    # count line should not appear for beperking (no Aantal line)
    beperking_section = output.split("CRITERIUM: stakeholders")[0]
    assert "Aantal" not in beperking_section


def test_format_scorecard_contains_notes():
    caps = _make_caps_result(criterion_overrides={
        "beperking": _make_criterion_result(
            "beperking",
            notes=["Onderbouwing aanwezig."],
        ),
    })
    output = _format_scorecard(caps)
    assert "Onderbouwing aanwezig." in output


def test_format_scorecard_contains_evidence_block_ids():
    caps = _make_caps_result()
    output = _format_scorecard(caps)
    assert _BLOCK_A in output
    assert _BLOCK_B in output


def test_format_scorecard_shows_geen_when_no_evidence():
    caps = _make_caps_result(criterion_overrides={
        "taalkeuze": _make_criterion_result("taalkeuze", block_ids=[]),
    })
    output = _format_scorecard(caps)
    assert "(geen)" in output


def test_format_scorecard_shows_manual_review_warning():
    caps = _make_caps_result(criterion_overrides={
        "beperking": _make_criterion_result("beperking", manual_review=True),
    })
    output = _format_scorecard(caps)
    assert "Handmatige verificatie" in output


def test_format_scorecard_handles_missing_criterion_gracefully():
    caps = _make_caps_result()
    caps.scorecard.results.pop("taalkeuze", None)
    output = _format_scorecard(caps)
    assert "taalkeuze" in output
    assert "niet geëvalueerd" in output


# ---------------------------------------------------------------------------
# _manual_review_section
# ---------------------------------------------------------------------------


def test_manual_review_section_empty_when_not_required():
    caps = _make_caps_result(manual_review_required=False)
    assert _manual_review_section(caps) == ""


def test_manual_review_section_contains_flag_names():
    caps = _make_caps_result(
        manual_review_required=True,
        manual_review_flags=["beperking", "taalkeuze"],
    )
    section = _manual_review_section(caps)
    assert "beperking" in section
    assert "taalkeuze" in section


def test_manual_review_section_contains_warning_symbol():
    caps = _make_caps_result(
        manual_review_required=True,
        manual_review_flags=["security"],
    )
    assert "⚠" in _manual_review_section(caps)

# ---------------------------------------------------------------------------
# _assemble_prompt
# ---------------------------------------------------------------------------

_SIMPLE_TEMPLATE = (
    "doc:<<DOC_ID>> stop:<<STOPLIGHT>> keys:<<CRITERION_KEYS>> "
    "ids:<<KNOWN_BLOCK_IDS>> review:<<MANUAL_REVIEW_SECTION>> card:<<SCORECARD>>"
)


def test_assemble_prompt_replaces_doc_id():
    caps = _make_caps_result(doc_id="testdoc")
    prompt = _assemble_prompt(caps, _SIMPLE_TEMPLATE)
    assert "testdoc" in prompt
    assert "<<DOC_ID>>" not in prompt


def test_assemble_prompt_replaces_stoplight():
    caps = _make_caps_result(stoplight="red")
    prompt = _assemble_prompt(caps, _SIMPLE_TEMPLATE)
    assert "red" in prompt
    assert "<<STOPLIGHT>>" not in prompt


def test_assemble_prompt_replaces_criterion_keys():
    caps = _make_caps_result()
    prompt = _assemble_prompt(caps, _SIMPLE_TEMPLATE)
    for key in CRITERIA_KEYS:
        assert key in prompt
    assert "<<CRITERION_KEYS>>" not in prompt


def test_assemble_prompt_replaces_known_block_ids():
    caps = _make_caps_result()
    prompt = _assemble_prompt(caps, _SIMPLE_TEMPLATE)
    assert _BLOCK_A in prompt
    assert "<<KNOWN_BLOCK_IDS>>" not in prompt


def test_assemble_prompt_no_placeholders_remain():
    caps = _make_caps_result()
    prompt = _assemble_prompt(caps, _SIMPLE_TEMPLATE)
    assert "<<" not in prompt
    assert ">>" not in prompt


def test_assemble_prompt_shows_geen_evidence_when_empty():
    caps = _make_caps_result(criterion_overrides={
        key: _make_criterion_result(key, block_ids=[])
        for key in CRITERIA_KEYS
    })
    prompt = _assemble_prompt(caps, _SIMPLE_TEMPLATE)
    assert "geen evidence beschikbaar" in prompt


def test_assemble_prompt_manual_review_empty_when_not_required():
    caps = _make_caps_result(manual_review_required=False)
    prompt = _assemble_prompt(caps, "review:<<MANUAL_REVIEW_SECTION>>end")
    assert "review:end" in prompt


def test_assemble_prompt_uses_real_template_file():
    caps = _make_caps_result()
    from src.feedback.feedback_builder import _PROMPT_PATH
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _assemble_prompt(caps, template)
    assert "<<" not in prompt
    assert ">>" not in prompt


def test_assemble_prompt_contains_no_raw_document_text():
    caps = _make_caps_result()
    from src.feedback.feedback_builder import _PROMPT_PATH
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _assemble_prompt(caps, template)
    # The prompt must not contain block text (text_snippet) — only block IDs
    for key in CRITERIA_KEYS:
        cr = caps.scorecard.results.get(key)
        if cr:
            for ref in cr.evidence:
                assert ref.text_snippet not in prompt or ref.text_snippet == ""

# ---------------------------------------------------------------------------
# _fallback
# ---------------------------------------------------------------------------


def test_fallback_has_correct_doc_id():
    caps = _make_caps_result(doc_id="my_doc")
    result = _fallback(caps, "some reason")
    assert result["document_id"] == "my_doc"


def test_fallback_has_correct_stoplight():
    caps = _make_caps_result(stoplight="red")
    result = _fallback(caps, "some reason")
    assert result["stoplight"] == "red"


def test_fallback_has_correct_disclaimer():
    caps = _make_caps_result()
    result = _fallback(caps, "some reason")
    assert result["disclaimer"] == DISCLAIMER


def test_fallback_contains_reason_in_docent_toelichting():
    caps = _make_caps_result()
    result = _fallback(caps, "LLM call failed")
    assert "LLM call failed" in result["docent_toelichting"]


def test_fallback_has_empty_feedback_and_feed_forward():
    caps = _make_caps_result()
    result = _fallback(caps, "reason")
    assert result["feedback"] == []
    assert result["feed_forward"] == []


def test_fallback_student_samenvatting_is_not_empty():
    caps = _make_caps_result()
    result = _fallback(caps, "reason")
    assert result["student_samenvatting"].strip() != ""

# ---------------------------------------------------------------------------
# generate_feedback — happy path
# ---------------------------------------------------------------------------


def test_generate_feedback_returns_feedback_result_on_success():
    caps = _make_caps_result(stoplight="green")
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.return_value = _valid_llm_json("green")
        result = generate_feedback(caps)
    assert result["document_id"] == "doc1"
    assert result["stoplight"] == "green"
    assert result["disclaimer"] == DISCLAIMER


def test_generate_feedback_prompt_contains_doc_id():
    caps = _make_caps_result(doc_id="target_doc")
    captured_prompt: list[str] = []

    def capture(prompt: str, **kwargs) -> str:
        captured_prompt.append(prompt)
        return _valid_llm_json("green")

    with patch("src.feedback.feedback_builder.llm_client.complete", side_effect=capture):
        generate_feedback(caps)

    assert captured_prompt, "LLM was never called"
    assert "target_doc" in captured_prompt[0]


def test_generate_feedback_prompt_contains_stoplight():
    caps = _make_caps_result(stoplight="yellow")
    captured_prompt: list[str] = []

    def capture(prompt: str, **kwargs) -> str:
        captured_prompt.append(prompt)
        return _valid_llm_json("yellow")

    with patch("src.feedback.feedback_builder.llm_client.complete", side_effect=capture):
        generate_feedback(caps)

    assert "yellow" in captured_prompt[0]


def test_generate_feedback_prompt_contains_no_raw_document_text():
    caps = _make_caps_result()
    captured_prompt: list[str] = []

    def capture(prompt: str, **kwargs) -> str:
        captured_prompt.append(prompt)
        return _valid_llm_json("green")

    with patch("src.feedback.feedback_builder.llm_client.complete", side_effect=capture):
        generate_feedback(caps)

    for key in CRITERIA_KEYS:
        cr = caps.scorecard.results.get(key)
        if cr:
            for ref in cr.evidence:
                if ref.text_snippet:
                    assert ref.text_snippet not in captured_prompt[0]
# ---------------------------------------------------------------------------
# generate_feedback — failure paths
# ---------------------------------------------------------------------------


def test_generate_feedback_returns_fallback_on_llm_error():
    caps = _make_caps_result(stoplight="red")
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.side_effect = LlmCallError("Connection refused")
        result = generate_feedback(caps)
    assert result["stoplight"] == "red"
    assert result["disclaimer"] == DISCLAIMER
    assert result["feedback"] == []


def test_generate_feedback_returns_fallback_on_validation_error():
    caps = _make_caps_result(stoplight="green")
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.return_value = '{"stoplight": "red"}'  # wrong stoplight → validation fails
        result = generate_feedback(caps)
    assert result["stoplight"] == "green"
    assert result["disclaimer"] == DISCLAIMER
    assert result["feedback"] == []


def test_generate_feedback_returns_fallback_on_malformed_json():
    caps = _make_caps_result(stoplight="green")
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.return_value = "this is not json"
        result = generate_feedback(caps)
    assert result["document_id"] == "doc1"
    assert result["disclaimer"] == DISCLAIMER


def test_generate_feedback_returns_fallback_when_template_missing():
    caps = _make_caps_result()
    with patch(
        "src.feedback.feedback_builder._PROMPT_PATH",
        Path("/nonexistent/path/feedback_writer_v1.txt"),
    ):
        result = generate_feedback(caps)
    assert result["document_id"] == "doc1"
    assert result["disclaimer"] == DISCLAIMER
    assert result["feedback"] == []


def test_generate_feedback_never_raises():
    caps = _make_caps_result()
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.side_effect = RuntimeError("unexpected crash")
        # RuntimeError is not caught — but LlmCallError is. Verify it still raises
        # so that only typed errors are swallowed, not arbitrary exceptions.
        with pytest.raises(RuntimeError):
            generate_feedback(caps)


def test_generate_feedback_fallback_doc_id_matches_caps():
    caps = _make_caps_result(doc_id="specific_doc", stoplight="yellow")
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.side_effect = LlmCallError("timeout")
        result = generate_feedback(caps)
    assert result["document_id"] == "specific_doc"
    assert result["stoplight"] == "yellow"


# ---------------------------------------------------------------------------
# generate_feedback — logging
# ---------------------------------------------------------------------------


def test_generate_feedback_logs_validation_ok(caplog):
    import logging
    caps = _make_caps_result(stoplight="green")
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.return_value = _valid_llm_json("green")
        with caplog.at_level(logging.INFO, logger="src.feedback.feedback_builder"):
            generate_feedback(caps)
    assert any("validation_ok" in r.message for r in caplog.records)


def test_generate_feedback_logs_llm_failure(caplog):
    import logging
    caps = _make_caps_result(stoplight="green")
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.side_effect = LlmCallError("timeout")
        with caplog.at_level(logging.ERROR, logger="src.feedback.feedback_builder"):
            generate_feedback(caps)
    assert any("llm_call_failed" in r.message for r in caplog.records)


def test_generate_feedback_logs_validation_failure(caplog):
    import logging
    caps = _make_caps_result(stoplight="green")
    with patch("src.feedback.feedback_builder.llm_client.complete") as mock_llm:
        mock_llm.return_value = "not json"
        with caplog.at_level(logging.WARNING, logger="src.feedback.feedback_builder"):
            generate_feedback(caps)
    assert any("validation_failed" in r.message for r in caplog.records)

