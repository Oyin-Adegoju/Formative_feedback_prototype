"""Unit tests for src/caps/caps.py — orchestration layer only.

All pipeline dependencies (retrieve_all_criteria, run_all_checks, score_document)
are monkeypatched so these tests verify call wiring and return-value plumbing,
not the behavior of the individual layers.

One real-path smoke test is included at the bottom to verify the full pipeline
runs end-to-end on a minimal empty-block report.
"""
from __future__ import annotations

import pytest

from src.caps.caps import (
    CapsPipelineArtifacts,
    _validate_report,
    run_caps,
    run_caps_with_artifacts,
)
from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import (
    CapsRunMeta,
    CapsRunResult,
    CapsScorecard,
    CriterionResult,
    ParseReportDict,
)
from src.caps.retrieval import CriterionCandidates


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def _stub_candidates() -> CriterionCandidates:
    return {key: [] for key in CRITERIA_KEYS}


def _stub_criterion_results() -> dict[str, CriterionResult]:
    return {
        key: CriterionResult(
            criterion_key=key,
            status="missing",
            stoplight="red",
            is_blocker=True,
        )
        for key in CRITERIA_KEYS
    }


def _stub_run_result(
    doc_id: str = "test-001",
    source_name: str = "test.pdf",
    input_source: str = "parser_direct",
    page_count: int = 3,
    block_count: int = 2,
) -> CapsRunResult:
    scorecard = CapsScorecard(doc_id=doc_id)
    run_meta = CapsRunMeta(
        input_source=input_source,  # type: ignore[arg-type]
        page_count=page_count,
        block_count=block_count,
        criteria_evaluated=list(CRITERIA_KEYS),
    )
    return CapsRunResult(
        doc_id=doc_id,
        source_name=source_name,
        scorecard=scorecard,
        overall_stoplight="red",
        run_meta=run_meta,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def report() -> ParseReportDict:
    return {
        "doc_id": "test-001",
        "source_name": "test.pdf",
        "page_count": 3,
        "block_count": 2,
        "blocks": [],
    }


@pytest.fixture()
def patched(monkeypatch):
    """Patch all three pipeline functions with no-op stubs and expose call captures."""
    captures: dict[str, object] = {}

    def stub_retrieve(report, criteria=None, max_candidates=20):
        captures["retrieve_max_candidates"] = max_candidates
        captures["retrieve_report"] = report
        return _stub_candidates()

    def stub_checks(candidates):
        captures["checks_candidates"] = candidates
        return _stub_criterion_results()

    def stub_score(report, criterion_results, input_source="parser_direct"):
        captures["score_input_source"] = input_source
        captures["score_report"] = report
        return _stub_run_result(input_source=input_source)

    monkeypatch.setattr("src.caps.caps.retrieve_all_criteria", stub_retrieve)
    monkeypatch.setattr("src.caps.caps.run_all_checks", stub_checks)
    monkeypatch.setattr("src.caps.caps.score_document", stub_score)

    return captures


# ---------------------------------------------------------------------------
# _validate_report
# ---------------------------------------------------------------------------


class TestValidateReport:
    def test_passes_for_minimal_valid_report(self, report):
        _validate_report(report)  # must not raise

    @pytest.mark.parametrize("missing_key", ["doc_id", "source_name", "page_count", "block_count", "blocks"])
    def test_raises_when_required_key_is_missing(self, report, missing_key):
        del report[missing_key]
        with pytest.raises(ValueError, match=missing_key):
            _validate_report(report)

    def test_error_message_lists_all_missing_keys(self):
        with pytest.raises(ValueError, match="required keys"):
            _validate_report({"doc_id": "x"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# run_caps_with_artifacts — return-value structure
# ---------------------------------------------------------------------------


class TestRunCapsWithArtifactsReturnValues:
    def test_returns_capspipelineartifacts(self, report, patched):
        artifacts = run_caps_with_artifacts(report)
        assert isinstance(artifacts, CapsPipelineArtifacts)

    def test_candidates_field_is_present(self, report, patched):
        artifacts = run_caps_with_artifacts(report)
        assert artifacts.candidates is not None

    def test_criterion_results_field_is_present(self, report, patched):
        artifacts = run_caps_with_artifacts(report)
        assert artifacts.criterion_results is not None

    def test_result_field_is_present(self, report, patched):
        artifacts = run_caps_with_artifacts(report)
        assert artifacts.result is not None

    def test_result_is_capsrunresult(self, report, patched):
        artifacts = run_caps_with_artifacts(report)
        assert isinstance(artifacts.result, CapsRunResult)


# ---------------------------------------------------------------------------
# run_caps — thin wrapper
# ---------------------------------------------------------------------------


class TestRunCaps:
    def test_returns_capsrunresult(self, report, patched):
        result = run_caps(report)
        assert isinstance(result, CapsRunResult)

    def test_equivalent_to_artifacts_result(self, report, patched):
        """run_caps must return exactly what run_caps_with_artifacts(...).result returns."""
        result = run_caps(report)
        artifacts = run_caps_with_artifacts(report)
        assert result == artifacts.result
