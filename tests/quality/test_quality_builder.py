"""Tests for src/quality/quality_builder.py — prompt assembly + strict flow."""

from __future__ import annotations

import json

import pytest

from src.caps.criterion_specs import CRITERIA_KEYS
from src.quality import quality_builder
from src.quality.output_schema import QUALITY_DIMENSIONS
from src.quality.quality_builder import (
    QualityGenerationError,
    _assemble_prompt,
    generate_quality_diagnostics,
)


def _handoff() -> dict:
    return {
        "document_id": "doc42",
        "source_name": "report.pdf",
        "overall_stoplight": "green",
        "blockers_triggered": [],
        "criteria": {
            key: {
                "status": "sufficient",
                "stoplight": "yellow",
                "count": None,
                "notes": [],
                "missing_signals": [],
                "evidence_items": [],
            }
            for key in CRITERIA_KEYS
        },
    }


def _valid_quality_json(doc_id: str = "doc42") -> str:
    return json.dumps({
        "document_id": doc_id,
        "criteria": {
            key: {
                "diagnostics": {dim: "middel" for dim in QUALITY_DIMENSIONS[key]},
                "strengths": [],
                "weaknesses": [],
                "manual_review": False,
                "manual_review_reason": [],
                "reason": "Voldoende concreet.",
            }
            for key in CRITERIA_KEYS
        },
    })


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_assemble_prompt_fills_doc_id_and_handoff():
    template = "id=<<DOC_ID>>\nhandoff=<<CAPS_HANDOFF_JSON>>"
    prompt = _assemble_prompt(_handoff(), template)
    assert "id=doc42" in prompt
    assert "<<DOC_ID>>" not in prompt
    assert "<<CAPS_HANDOFF_JSON>>" not in prompt
    handoff_json = prompt.split("handoff=", 1)[1]
    assert json.loads(handoff_json)["document_id"] == "doc42"


def test_real_template_has_no_placeholders_after_assembly():
    template = quality_builder._PROMPT_PATH.read_text(encoding="utf-8")
    prompt = _assemble_prompt(_handoff(), template)
    assert "<<DOC_ID>>" not in prompt
    assert "<<CAPS_HANDOFF_JSON>>" not in prompt


# ---------------------------------------------------------------------------
# Strict flow: success, retry recovery, and hard failure (no fallback)
# ---------------------------------------------------------------------------


def test_generate_quality_success(monkeypatch):
    monkeypatch.setattr(
        quality_builder.llm_client, "complete",
        lambda *a, **k: _valid_quality_json(),
    )
    result = generate_quality_diagnostics(_handoff())
    assert result["document_id"] == "doc42"  # set from handoff
    assert set(result["criteria"].keys()) == set(CRITERIA_KEYS)


def test_generate_quality_passes_handoff_into_prompt(monkeypatch):
    captured: list[str] = []

    def fake_complete(prompt, *a, **k):
        captured.append(prompt)
        return _valid_quality_json()

    monkeypatch.setattr(quality_builder.llm_client, "complete", fake_complete)
    generate_quality_diagnostics(_handoff())
    assert captured and "doc42" in captured[0]


def test_generate_quality_retries_then_succeeds(monkeypatch):
    """A first invalid response (dropped dimension) is recovered by re-sampling."""
    bad = json.loads(_valid_quality_json())
    bad["criteria"]["security"]["diagnostics"].pop("explanation_quality")
    responses = [json.dumps(bad), _valid_quality_json()]
    prompts: list[str] = []

    def fake_complete(prompt, *a, **k):
        prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(quality_builder.llm_client, "complete", fake_complete)
    result = generate_quality_diagnostics(_handoff())
    assert set(result["criteria"]["security"]["diagnostics"].keys()) == \
        set(QUALITY_DIMENSIONS["security"])
    assert not responses                       # both attempts consumed
    assert "CORRECTIE" in prompts[1]           # retry carried a correction


def test_generate_quality_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr(
        quality_builder.llm_client, "complete", lambda *a, **k: "not json",
    )
    with pytest.raises(QualityGenerationError) as exc:
        generate_quality_diagnostics(_handoff(), max_attempts=2)
    assert "after 2 attempts" in exc.value.reason


def test_generate_quality_raises_on_llm_error(monkeypatch):
    from src.llm.llm_client import LlmCallError

    def boom(*a, **k):
        raise LlmCallError("connection refused")

    monkeypatch.setattr(quality_builder.llm_client, "complete", boom)
    with pytest.raises(QualityGenerationError) as exc:
        generate_quality_diagnostics(_handoff())
    assert "LLM call failed" in exc.value.reason
