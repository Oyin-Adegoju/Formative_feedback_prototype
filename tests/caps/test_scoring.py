"""Unit tests for src/caps/scoring.py — document-level scoring aggregation."""
from __future__ import annotations

import pytest

from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import CriterionResult, ParseReportDict
from src.caps.scoring import (
    _validate_criterion_result_keys,
    collect_blockers,
    collect_manual_review_flags,
    compute_hidden_score,
    determine_overall_stoplight,
    score_document,
    status_to_score,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_result(
    key: str,
    status: str,
    is_blocker: bool = True,
    manual_review: bool = False,
) -> CriterionResult:
    stoplight = "green" if status in ("sufficient", "strong") else "red"
    return CriterionResult(
        criterion_key=key,
        status=status,
        stoplight=stoplight,
        is_blocker=is_blocker,
        manual_review=manual_review,
    )


def _all_sufficient() -> dict[str, CriterionResult]:
    return {k: _make_result(k, "sufficient") for k in CRITERIA_KEYS}


def _minimal_report(doc_id: str = "doc-1", source_name: str = "test.pdf") -> ParseReportDict:
    return {
        "doc_id": doc_id,
        "source_name": source_name,
        "page_count": 10,
        "block_count": 50,
        "blocks": [],
    }


# ---------------------------------------------------------------------------
# 1. status_to_score
# ---------------------------------------------------------------------------


class TestStatusToScore:
    def test_missing_maps_to_0(self):
        assert status_to_score("missing") == 0

    def test_partial_maps_to_1(self):
        assert status_to_score("partial") == 1

    def test_sufficient_maps_to_2(self):
        assert status_to_score("sufficient") == 2

    def test_strong_maps_to_3(self):
        assert status_to_score("strong") == 3


# ---------------------------------------------------------------------------
# 2. compute_hidden_score
# ---------------------------------------------------------------------------


class TestComputeHiddenScore:
    def test_all_missing_gives_zero(self):
        results = {k: _make_result(k, "missing") for k in CRITERIA_KEYS}
        assert compute_hidden_score(results) == 0

    def test_all_strong_gives_fifteen(self):
        results = {k: _make_result(k, "strong") for k in CRITERIA_KEYS}
        assert compute_hidden_score(results) == 15

    def test_all_sufficient_gives_ten(self):
        assert compute_hidden_score(_all_sufficient()) == 10

    def test_mixed_statuses_sum_correctly(self):
        # strong(3) + sufficient(2) + partial(1) + missing(0) + sufficient(2) = 8
        statuses = ["strong", "sufficient", "partial", "missing", "sufficient"]
        results = {k: _make_result(k, s) for k, s in zip(CRITERIA_KEYS, statuses)}
        assert compute_hidden_score(results) == 8

    def test_single_result_partial(self):
        assert compute_hidden_score({"k": _make_result("k", "partial")}) == 1


# ---------------------------------------------------------------------------
# 3. collect_blockers
# ---------------------------------------------------------------------------


class TestCollectBlockers:
    def test_blocker_with_missing_is_included(self):
        results = {"beperking": _make_result("beperking", "missing", is_blocker=True)}
        assert collect_blockers(results) == ["beperking"]

    def test_blocker_with_partial_is_included(self):
        results = {"beperking": _make_result("beperking", "partial", is_blocker=True)}
        assert collect_blockers(results) == ["beperking"]

    def test_blocker_with_sufficient_is_excluded(self):
        results = {"beperking": _make_result("beperking", "sufficient", is_blocker=True)}
        assert collect_blockers(results) == []

    def test_blocker_with_strong_is_excluded(self):
        results = {"beperking": _make_result("beperking", "strong", is_blocker=True)}
        assert collect_blockers(results) == []

    def test_non_blocker_with_missing_is_excluded(self):
        results = {"beperking": _make_result("beperking", "missing", is_blocker=False)}
        assert collect_blockers(results) == []

    def test_non_blocker_with_partial_is_excluded(self):
        results = {"beperking": _make_result("beperking", "partial", is_blocker=False)}
        assert collect_blockers(results) == []

    def test_multiple_blockers_all_returned(self):
        results = {
            "beperking": _make_result("beperking", "missing", is_blocker=True),
            "stakeholders": _make_result("stakeholders", "partial", is_blocker=True),
            "requirements": _make_result("requirements", "sufficient", is_blocker=True),
        }
        assert set(collect_blockers(results)) == {"beperking", "stakeholders"}

    def test_no_blockers_triggered_returns_empty(self):
        assert collect_blockers(_all_sufficient()) == []


# ---------------------------------------------------------------------------
# 4. collect_manual_review_flags
# ---------------------------------------------------------------------------


class TestCollectManualReviewFlags:
    def test_manual_review_true_is_included(self):
        results = {"beperking": _make_result("beperking", "sufficient", manual_review=True)}
        assert collect_manual_review_flags(results) == ["beperking"]

    def test_manual_review_false_is_excluded(self):
        results = {"beperking": _make_result("beperking", "sufficient", manual_review=False)}
        assert collect_manual_review_flags(results) == []

    def test_multiple_flags_all_returned(self):
        results = {
            "beperking": _make_result("beperking", "partial", manual_review=True),
            "stakeholders": _make_result("stakeholders", "sufficient", manual_review=False),
            "requirements": _make_result("requirements", "sufficient", manual_review=True),
        }
        assert set(collect_manual_review_flags(results)) == {"beperking", "requirements"}

    def test_no_flags_returns_empty(self):
        assert collect_manual_review_flags(_all_sufficient()) == []


# ---------------------------------------------------------------------------
# 5. determine_overall_stoplight
# ---------------------------------------------------------------------------


class TestDetermineOverallStoplight:
    def test_red_when_blocker_triggered(self):
        assert determine_overall_stoplight(["beperking"], False, _all_sufficient()) == "red"

    def test_red_when_manual_review_required(self):
        assert determine_overall_stoplight([], True, _all_sufficient()) == "red"

    def test_red_when_both_blocker_and_manual_review(self):
        assert determine_overall_stoplight(["beperking"], True, _all_sufficient()) == "red"

    def test_green_when_all_sufficient_no_flags(self):
        results = {k: _make_result(k, "sufficient") for k in CRITERIA_KEYS}
        assert determine_overall_stoplight([], False, results) == "green"

    def test_green_when_all_strong_no_flags(self):
        results = {k: _make_result(k, "strong") for k in CRITERIA_KEYS}
        assert determine_overall_stoplight([], False, results) == "green"

    def test_green_with_mixed_sufficient_and_strong(self):
        statuses = ["sufficient", "strong", "sufficient", "strong", "sufficient"]
        results = {k: _make_result(k, s) for k, s in zip(CRITERIA_KEYS, statuses)}
        assert determine_overall_stoplight([], False, results) == "green"

    def test_yellow_when_partial_non_blocker_not_in_blockers_list(self):
        # Non-blocker with partial: blockers_triggered stays empty, but not all sufficient.
        results = {k: _make_result(k, "sufficient") for k in CRITERIA_KEYS}
        results["beperking"] = _make_result("beperking", "partial", is_blocker=False)
        assert determine_overall_stoplight([], False, results) == "yellow"

    def test_yellow_when_missing_non_blocker(self):
        results = {k: _make_result(k, "sufficient") for k in CRITERIA_KEYS}
        results["beperking"] = _make_result("beperking", "missing", is_blocker=False)
        assert determine_overall_stoplight([], False, results) == "yellow"


# ---------------------------------------------------------------------------
# 6. _validate_criterion_result_keys
# ---------------------------------------------------------------------------


class TestValidateCriterionResultKeys:
    def test_passes_with_all_expected_keys(self):
        _validate_criterion_result_keys(_all_sufficient())  # no exception

    def test_raises_when_key_missing(self):
        results = _all_sufficient()
        del results["beperking"]
        with pytest.raises(ValueError, match="missing"):
            _validate_criterion_result_keys(results)

    def test_raises_when_unexpected_key_present(self):
        results = _all_sufficient()
        results["bogus_key"] = _make_result("bogus_key", "sufficient")
        with pytest.raises(ValueError, match="unexpected"):
            _validate_criterion_result_keys(results)

    def test_raises_when_both_missing_and_unexpected(self):
        results = _all_sufficient()
        del results["security"]
        results["bogus_key"] = _make_result("bogus_key", "sufficient")
        with pytest.raises(ValueError) as exc_info:
            _validate_criterion_result_keys(results)
        msg = str(exc_info.value)
        assert "missing" in msg
        assert "unexpected" in msg
