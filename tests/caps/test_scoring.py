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
    # --- red: blockers ---

    def test_red_when_blocker_triggered(self):
        assert determine_overall_stoplight(["beperking"], _all_sufficient()) == "red"

    def test_red_when_both_blocker_and_manual_review(self):
        # Blockers override everything; manual_review on top does not change the outcome.
        results = {k: _make_result(k, "sufficient") for k in CRITERIA_KEYS}
        results["beperking"] = _make_result("beperking", "sufficient", manual_review=True)
        assert determine_overall_stoplight(["beperking"], results) == "red"

    # --- red: manual review threshold (half or more) ---

    def test_red_when_manual_reviews_reach_exactly_half(self):
        # 2 of 4 criteria: 2*2=4 >= 4 → red
        results = {
            f"c{i}": _make_result(f"c{i}", "sufficient", is_blocker=False, manual_review=(i < 2))
            for i in range(4)
        }
        assert determine_overall_stoplight([], results) == "red"

    def test_red_when_manual_reviews_exceed_half(self):
        # 3 of 5 criteria: 3*2=6 >= 5 → red
        results = {
            f"c{i}": _make_result(f"c{i}", "sufficient", is_blocker=False, manual_review=(i < 3))
            for i in range(5)
        }
        assert determine_overall_stoplight([], results) == "red"

    # --- yellow: at least one flag, fewer than half ---

    def test_yellow_when_one_manual_review_of_five(self):
        # 1 of 5: 1*2=2 < 5 → yellow
        results = {k: _make_result(k, "sufficient") for k in CRITERIA_KEYS}
        results["beperking"] = _make_result("beperking", "sufficient", manual_review=True)
        assert determine_overall_stoplight([], results) == "yellow"

    def test_yellow_when_fewer_than_half_manual_reviews(self):
        # 1 of 4: 1*2=2 < 4 → yellow
        results = {
            f"c{i}": _make_result(f"c{i}", "sufficient", is_blocker=False, manual_review=(i < 1))
            for i in range(4)
        }
        assert determine_overall_stoplight([], results) == "yellow"

    # --- green: no blockers, zero manual review flags ---

    def test_green_when_all_sufficient_no_flags(self):
        results = {k: _make_result(k, "sufficient") for k in CRITERIA_KEYS}
        assert determine_overall_stoplight([], results) == "green"

    def test_green_when_all_strong_no_flags(self):
        results = {k: _make_result(k, "strong") for k in CRITERIA_KEYS}
        assert determine_overall_stoplight([], results) == "green"

    def test_green_with_mixed_sufficient_and_strong(self):
        statuses = ["sufficient", "strong", "sufficient", "strong", "sufficient"]
        results = {k: _make_result(k, s) for k, s in zip(CRITERIA_KEYS, statuses)}
        assert determine_overall_stoplight([], results) == "green"

    def test_green_when_partial_non_blocker_no_manual_review(self):
        # Criterion status alone does not drive the overall stoplight under the new policy.
        # A partial non-blocker with no manual_review flag produces green, not yellow.
        results = {k: _make_result(k, "sufficient") for k in CRITERIA_KEYS}
        results["beperking"] = _make_result("beperking", "partial", is_blocker=False)
        assert determine_overall_stoplight([], results) == "green"


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


# ---------------------------------------------------------------------------
# 7. score_document
# ---------------------------------------------------------------------------


class TestScoreDocument:
    def test_doc_id_set_from_report(self):
        run = score_document(_minimal_report(doc_id="doc-99"), _all_sufficient())
        assert run.doc_id == "doc-99"

    def test_source_name_set_from_report(self):
        run = score_document(_minimal_report(source_name="myfile.pdf"), _all_sufficient())
        assert run.source_name == "myfile.pdf"

    def test_hidden_score_is_sum_of_criterion_scores(self):
        # all sufficient → 2 * 5 = 10
        run = score_document(_minimal_report(), _all_sufficient())
        assert run.scorecard.hidden_score == 10

    def test_manual_review_required_false_when_no_flags(self):
        run = score_document(_minimal_report(), _all_sufficient())
        assert run.manual_review_required is False

    def test_manual_review_required_true_when_any_flag(self):
        results = _all_sufficient()
        results["beperking"] = _make_result("beperking", "sufficient", manual_review=True)
        run = score_document(_minimal_report(), results)
        assert run.manual_review_required is True

    def test_blockers_triggered_empty_when_all_sufficient(self):
        run = score_document(_minimal_report(), _all_sufficient())
        assert run.blockers_triggered == []

    def test_blockers_triggered_contains_failing_blocker(self):
        results = _all_sufficient()
        results["stakeholders"] = _make_result("stakeholders", "missing", is_blocker=True)
        run = score_document(_minimal_report(), results)
        assert "stakeholders" in run.blockers_triggered

    def test_manual_review_flags_correct(self):
        results = _all_sufficient()
        results["taalkeuze"] = _make_result("taalkeuze", "sufficient", manual_review=True)
        run = score_document(_minimal_report(), results)
        assert run.manual_review_flags == ["taalkeuze"]

    def test_criteria_evaluated_is_rubric_order(self):
        run = score_document(_minimal_report(), _all_sufficient())
        assert run.run_meta.criteria_evaluated == list(CRITERIA_KEYS)

    def test_scorecard_results_preserve_rubric_order_regardless_of_input_order(self):
        reversed_results = {k: _all_sufficient()[k] for k in reversed(CRITERIA_KEYS)}
        run = score_document(_minimal_report(), reversed_results)
        assert list(run.scorecard.results.keys()) == list(CRITERIA_KEYS)

    def test_overall_stoplight_green_when_all_sufficient(self):
        run = score_document(_minimal_report(), _all_sufficient())
        assert run.overall_stoplight == "green"

    def test_overall_stoplight_red_when_blocker_triggered(self):
        results = _all_sufficient()
        results["requirements"] = _make_result("requirements", "missing", is_blocker=True)
        run = score_document(_minimal_report(), results)
        assert run.overall_stoplight == "red"

    def test_overall_stoplight_yellow_when_one_of_five_manual_review(self):
        # 1 of 5 criteria → 1*2=2 < 5 → yellow, not red
        results = _all_sufficient()
        results["security"] = _make_result("security", "sufficient", manual_review=True)
        run = score_document(_minimal_report(), results)
        assert run.overall_stoplight == "yellow"

    def test_overall_stoplight_red_when_manual_reviews_reach_half(self):
        # 3 of 5 criteria → 3*2=6 >= 5 → red
        results = _all_sufficient()
        for key in list(CRITERIA_KEYS)[:3]:
            results[key] = _make_result(key, "sufficient", manual_review=True)
        run = score_document(_minimal_report(), results)
        assert run.overall_stoplight == "red"

    def test_input_source_forwarded_to_run_meta(self):
        run = score_document(_minimal_report(), _all_sufficient(), input_source="anonymized")
        assert run.run_meta.input_source == "anonymized"

    def test_page_and_block_count_in_run_meta(self):
        report = _minimal_report()
        report["page_count"] = 7
        report["block_count"] = 33
        run = score_document(report, _all_sufficient())
        assert run.run_meta.page_count == 7
        assert run.run_meta.block_count == 33

    def test_scorecard_doc_id_matches_report(self):
        run = score_document(_minimal_report(doc_id="x-42"), _all_sufficient())
        assert run.scorecard.doc_id == "x-42"
