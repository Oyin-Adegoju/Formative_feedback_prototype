"""Unit tests for src/caps/retrieval.py.

Tests cover only retrieval behaviour: which blocks are included/excluded,
how hints fire, how scores are computed, and how results are sorted.
No checks, scoring, or counting logic is tested here.
"""

from __future__ import annotations

import pytest

from src.caps.criterion_specs import (
    CRITERIA_KEYS,
    CriterionSpec,
    INFIRFS_REQUIREMENTS_CRITERIA,
)
from src.caps.models import BlockDict, ParseReportDict, TableMetaDict
from src.caps.retrieval import RetrievalHit, retrieve_all_criteria, retrieve_for_criterion

# ---------------------------------------------------------------------------
# Fake criterion — fully controlled hints and block types
# ---------------------------------------------------------------------------

_FAKE_SPEC = CriterionSpec(
    key="fake",
    label="Fake",
    description="Fake criterion for testing",
    is_blocker=False,
    relevant_block_types=frozenset({"paragraph", "table", "bullet"}),
    heading_hints=("fakehead", "sectionlabel"),
    text_hints=("fakekeyword", "secondkeyword"),
)

# Score expectations derived from retrieval.py constants:
#   type match   +1.0
#   heading hint +3.0 per match
#   text hint    +1.5 per match
#   appendix     × 0.4  (applied last)
#   caption      × 0.6  (applied last)


# ---------------------------------------------------------------------------
# Block / report builder helpers
# ---------------------------------------------------------------------------


def make_block(
    block_id: str,
    *,
    block_type: str = "paragraph",
    text: str = "",
    heading_path: list[str] | None = None,
    is_front_matter: bool = False,
    is_appendix: bool = False,
    page_no: int = 1,
    table_meta: TableMetaDict | None = None,
) -> BlockDict:
    block: BlockDict = {
        "block_id": block_id,
        "page_no": page_no,
        "block_type": block_type,
        "text": text,
        "heading_path": heading_path or [],
        "is_front_matter": is_front_matter,
        "is_appendix": is_appendix,
    }
    if table_meta is not None:
        block["table_meta"] = table_meta
    return block


def make_report(blocks: list[BlockDict]) -> ParseReportDict:
    return {
        "doc_id": "test-doc",
        "source_name": "test.pdf",
        "page_count": 1,
        "block_count": len(blocks),
        "blocks": blocks,
    }


# ---------------------------------------------------------------------------
# 1. Excluded block types are ignored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("excluded_type", ["front_matter", "noise", "template"])
def test_excluded_block_types_are_never_retrieved(excluded_type: str) -> None:
    block = make_block("b1", block_type=excluded_type, text="fakekeyword")
    assert retrieve_for_criterion([block], _FAKE_SPEC) == []


def test_is_front_matter_flag_excludes_any_block_type() -> None:
    # A paragraph with is_front_matter=True is still excluded — type doesn't matter.
    block = make_block("b1", block_type="paragraph", text="fakekeyword", is_front_matter=True)
    assert retrieve_for_criterion([block], _FAKE_SPEC) == []


# ---------------------------------------------------------------------------
# 2. Type match alone (no heading or text hint) → not returned
# ---------------------------------------------------------------------------


def test_type_match_only_is_not_enough_to_retrieve() -> None:
    # paragraph is in relevant_block_types but text has no hint match
    block = make_block("b1", block_type="paragraph", text="no matching content here at all")
    assert retrieve_for_criterion([block], _FAKE_SPEC) == []


def test_block_with_irrelevant_type_and_no_hints_not_retrieved() -> None:
    block = make_block("b1", block_type="heading", text="unrelated text")
    assert retrieve_for_criterion([block], _FAKE_SPEC) == []


# ---------------------------------------------------------------------------
# 3. Heading hints boost retrieval and populate matched_heading_hints
# ---------------------------------------------------------------------------


def test_heading_hint_in_heading_path_returns_block() -> None:
    block = make_block("b1", heading_path=["fakeHead Overview"])
    hits = retrieve_for_criterion([block], _FAKE_SPEC)
    assert len(hits) == 1


def test_heading_hint_populates_matched_heading_hints() -> None:
    block = make_block("b1", heading_path=["fakehead section"])
    hit = retrieve_for_criterion([block], _FAKE_SPEC)[0]
    assert "fakehead" in hit.matched_heading_hints
    assert hit.matched_text_hints == []


def test_heading_hint_match_scores_higher_than_single_text_hint() -> None:
    # heading hint: type(1.0) + heading(3.0) = 4.0
    # text hint:    type(1.0) + text(1.5)    = 2.5
    heading_block = make_block("b1", heading_path=["fakehead section"])
    text_block = make_block("b2", text="fakekeyword")
    hits = retrieve_for_criterion([heading_block, text_block], _FAKE_SPEC)
    scores = {h.block["block_id"]: h.score for h in hits}
    assert scores["b1"] > scores["b2"]


# ---------------------------------------------------------------------------
# 4. Text hints boost retrieval and populate matched_text_hints
# ---------------------------------------------------------------------------


def test_text_hint_in_block_text_returns_block() -> None:
    block = make_block("b1", text="this block mentions fakekeyword")
    hits = retrieve_for_criterion([block], _FAKE_SPEC)
    assert len(hits) == 1


def test_text_hint_populates_matched_text_hints() -> None:
    block = make_block("b1", text="fakekeyword in content")
    hit = retrieve_for_criterion([block], _FAKE_SPEC)[0]
    assert "fakekeyword" in hit.matched_text_hints
    assert hit.matched_heading_hints == []


def test_multiple_text_hints_increase_score() -> None:
    single = make_block("b1", text="fakekeyword only")
    double = make_block("b2", text="fakekeyword and secondkeyword both")
    hits = retrieve_for_criterion([single, double], _FAKE_SPEC)
    scores = {h.block["block_id"]: h.score for h in hits}
    assert scores["b2"] > scores["b1"]


# ---------------------------------------------------------------------------
# 5. Table blocks are searchable through text, header_row, and cells
# ---------------------------------------------------------------------------

_EMPTY_TABLE_META: TableMetaDict = {
    "row_count": 0,
    "column_count": 0,
    "header_row": None,
    "cells": [],
    "index_cells": [],
}


def test_table_searchable_through_block_text() -> None:
    block = make_block(
        "b1",
        block_type="table",
        text="fakekeyword in plain text",
        table_meta=_EMPTY_TABLE_META,
    )
    hits = retrieve_for_criterion([block], _FAKE_SPEC)
    assert len(hits) == 1
    assert "fakekeyword" in hits[0].matched_text_hints


def test_table_searchable_through_header_row_as_text_hint() -> None:
    block = make_block(
        "b1",
        block_type="table",
        text="",
        table_meta={
            "row_count": 1,
            "column_count": 2,
            "header_row": "fakekeyword | column B",
            "cells": [],
            "index_cells": [],
        },
    )
    hits = retrieve_for_criterion([block], _FAKE_SPEC)
    assert len(hits) == 1
    assert "fakekeyword" in hits[0].matched_text_hints


def test_table_header_row_matches_heading_hint() -> None:
    # header_row is treated as a heading-level signal for heading hint matching.
    block = make_block(
        "b1",
        block_type="table",
        text="",
        table_meta={
            "row_count": 1,
            "column_count": 1,
            "header_row": "fakehead column names",
            "cells": [],
            "index_cells": [],
        },
    )
    hits = retrieve_for_criterion([block], _FAKE_SPEC)
    assert len(hits) == 1
    assert "fakehead" in hits[0].matched_heading_hints


def test_table_searchable_through_cells() -> None:
    block = make_block(
        "b1",
        block_type="table",
        text="",
        table_meta={
            "row_count": 2,
            "column_count": 2,
            "header_row": None,
            "cells": [["name", "role"], ["fakekeyword item", None]],
            "index_cells": [],
        },
    )
    hits = retrieve_for_criterion([block], _FAKE_SPEC)
    assert len(hits) == 1
    assert "fakekeyword" in hits[0].matched_text_hints


def test_table_none_cells_are_skipped_without_error() -> None:
    block = make_block(
        "b1",
        block_type="table",
        text="fakekeyword",
        table_meta={
            "row_count": 1,
            "column_count": 2,
            "header_row": None,
            "cells": [[None, None]],
            "index_cells": [],
        },
    )
    hits = retrieve_for_criterion([block], _FAKE_SPEC)
    assert len(hits) == 1


# ---------------------------------------------------------------------------
# 6. Appendix blocks are not excluded but are downweighted
# ---------------------------------------------------------------------------


def test_appendix_block_is_still_retrieved() -> None:
    block = make_block("b1", text="fakekeyword", is_appendix=True)
    assert len(retrieve_for_criterion([block], _FAKE_SPEC)) == 1


def test_appendix_block_scores_lower_than_equivalent_body_block() -> None:
    normal = make_block("b1", text="fakekeyword", is_appendix=False)
    appendix = make_block("b2", text="fakekeyword", is_appendix=True)
    hits = retrieve_for_criterion([normal, appendix], _FAKE_SPEC)
    scores = {h.block["block_id"]: h.score for h in hits}
    assert scores["b2"] < scores["b1"]


def test_appendix_score_is_multiplied_by_04() -> None:
    # paragraph + 1 text hint without appendix: 1.0 + 1.5 = 2.5
    # with appendix: 2.5 × 0.4 = 1.0
    block = make_block("b1", text="fakekeyword", is_appendix=True)
    hit = retrieve_for_criterion([block], _FAKE_SPEC)[0]
    assert hit.score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 7. Caption blocks are not excluded but are downweighted
# ---------------------------------------------------------------------------


def test_caption_block_is_still_retrieved() -> None:
    block = make_block("b1", block_type="caption", text="fakekeyword")
    assert len(retrieve_for_criterion([block], _FAKE_SPEC)) == 1


def test_caption_block_scores_lower_than_equivalent_paragraph() -> None:
    para = make_block("b1", block_type="paragraph", text="fakekeyword")
    caption = make_block("b2", block_type="caption", text="fakekeyword")
    hits = retrieve_for_criterion([para, caption], _FAKE_SPEC)
    scores = {h.block["block_id"]: h.score for h in hits}
    assert scores["b2"] < scores["b1"]


def test_caption_score_is_multiplied_by_06() -> None:
    # caption not in relevant_block_types → no type bonus; 1 text hint: 1.5 × 0.6 = 0.9
    block = make_block("b1", block_type="caption", text="fakekeyword")
    hit = retrieve_for_criterion([block], _FAKE_SPEC)[0]
    assert hit.score == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# 8. Sorting: score descending, then block_id ascending
# ---------------------------------------------------------------------------


def test_sorting_is_score_descending() -> None:
    # low: type(1.0) + text(1.5) = 2.5
    # high: type(1.0) + heading(3.0) + text×2(3.0) = 7.0
    low = make_block("b1", text="fakekeyword")
    high = make_block("b2", text="fakekeyword secondkeyword", heading_path=["fakehead"])
    hits = retrieve_for_criterion([low, high], _FAKE_SPEC)
    assert hits[0].block["block_id"] == "b2"
    assert hits[1].block["block_id"] == "b1"


def test_sorting_block_id_ascending_when_scores_are_equal() -> None:
    b_z = make_block("zzz", text="fakekeyword")
    b_a = make_block("aaa", text="fakekeyword")
    b_m = make_block("mmm", text="fakekeyword")
    hits = retrieve_for_criterion([b_z, b_m, b_a], _FAKE_SPEC)
    ids = [h.block["block_id"] for h in hits]
    assert ids == ["aaa", "mmm", "zzz"]


def test_sorting_is_stable_across_repeated_calls() -> None:
    blocks = [make_block(f"b{i:02d}", text="fakekeyword") for i in range(5)]
    first = [h.block["block_id"] for h in retrieve_for_criterion(blocks, _FAKE_SPEC)]
    second = [h.block["block_id"] for h in retrieve_for_criterion(blocks, _FAKE_SPEC)]
    assert first == second


# ---------------------------------------------------------------------------
# 9. retrieve_for_criterion respects max_candidates
# ---------------------------------------------------------------------------


def test_max_candidates_limits_returned_results() -> None:
    blocks = [make_block(f"b{i:02d}", text="fakekeyword") for i in range(15)]
    hits = retrieve_for_criterion(blocks, _FAKE_SPEC, max_candidates=5)
    assert len(hits) == 5


def test_max_candidates_zero_returns_empty_list() -> None:
    blocks = [make_block("b1", text="fakekeyword")]
    hits = retrieve_for_criterion(blocks, _FAKE_SPEC, max_candidates=0)
    assert hits == []


def test_max_candidates_larger_than_hits_returns_all_hits() -> None:
    blocks = [make_block("b1", text="fakekeyword"), make_block("b2", text="fakekeyword")]
    hits = retrieve_for_criterion(blocks, _FAKE_SPEC, max_candidates=100)
    assert len(hits) == 2


def test_max_candidates_one_returns_highest_scoring_block() -> None:
    low = make_block("b1", text="fakekeyword")
    high = make_block("b2", text="fakekeyword secondkeyword", heading_path=["fakehead"])
    hits = retrieve_for_criterion([low, high], _FAKE_SPEC, max_candidates=1)
    assert len(hits) == 1
    assert hits[0].block["block_id"] == "b2"


# ---------------------------------------------------------------------------
# 10. retrieve_all_criteria returns all criterion keys, even if some are empty
# ---------------------------------------------------------------------------


def test_retrieve_all_criteria_returns_all_five_criterion_keys() -> None:
    report = make_report(blocks=[])
    result = retrieve_all_criteria(report)
    for key in CRITERIA_KEYS:
        assert key in result


def test_retrieve_all_criteria_empty_list_when_no_blocks_match() -> None:
    report = make_report(blocks=[])
    result = retrieve_all_criteria(report)
    for key in CRITERIA_KEYS:
        assert result[key] == []


def test_retrieve_all_criteria_populates_matching_criterion() -> None:
    # "stakeholder" is a text_hint for the stakeholders criterion
    block = make_block("b1", block_type="paragraph", text="stakeholder analysis overview")
    report = make_report(blocks=[block])
    result = retrieve_all_criteria(report)
    assert len(result["stakeholders"]) == 1
    assert result["stakeholders"][0].block["block_id"] == "b1"


def test_retrieve_all_criteria_some_empty_some_populated() -> None:
    # Only the security criterion should match this block
    block = make_block("b1", block_type="paragraph", text="authenticatie en autorisatie beveiliging")
    report = make_report(blocks=[block])
    result = retrieve_all_criteria(report)
    # security matches, all keys still present
    assert len(result["security"]) > 0
    for key in CRITERIA_KEYS:
        assert key in result


def test_retrieve_all_criteria_respects_max_candidates() -> None:
    # 30 blocks all matching stakeholders; max_candidates=3 must cap each criterion
    blocks = [
        make_block(f"b{i:02d}", block_type="paragraph", text="stakeholder belang invloed")
        for i in range(30)
    ]
    report = make_report(blocks=blocks)
    result = retrieve_all_criteria(report, max_candidates=3)
    assert len(result["stakeholders"]) == 3
