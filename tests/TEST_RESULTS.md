# CAPS Test Results

## How to update this file

After each test run, paste the pytest output under the relevant module section.
Add new modules by copying the template at the bottom of this file.

---

## `tests/caps/test_retrieval.py`

**Date:** 2026-06-01
**Status:** 35/35 PASSED
**Branch:** `feat/caps-rubric-contract`

| # | Test | Result |
|---|------|--------|
| 1 | `test_excluded_block_types_are_never_retrieved[front_matter]` | PASSED |
| 2 | `test_excluded_block_types_are_never_retrieved[noise]` | PASSED |
| 3 | `test_excluded_block_types_are_never_retrieved[template]` | PASSED |
| 4 | `test_is_front_matter_flag_excludes_any_block_type` | PASSED |
| 5 | `test_type_match_only_is_not_enough_to_retrieve` | PASSED |
| 6 | `test_block_with_irrelevant_type_and_no_hints_not_retrieved` | PASSED |
| 7 | `test_heading_hint_in_heading_path_returns_block` | PASSED |
| 8 | `test_heading_hint_populates_matched_heading_hints` | PASSED |
| 9 | `test_heading_hint_match_scores_higher_than_single_text_hint` | PASSED |
| 10 | `test_text_hint_in_block_text_returns_block` | PASSED |
| 11 | `test_text_hint_populates_matched_text_hints` | PASSED |
| 12 | `test_multiple_text_hints_increase_score` | PASSED |
| 13 | `test_table_searchable_through_block_text` | PASSED |
| 14 | `test_table_searchable_through_header_row_as_text_hint` | PASSED |
| 15 | `test_table_header_row_matches_heading_hint` | PASSED |
| 16 | `test_table_searchable_through_cells` | PASSED |
| 17 | `test_table_none_cells_are_skipped_without_error` | PASSED |
| 18 | `test_appendix_block_is_still_retrieved` | PASSED |
| 19 | `test_appendix_block_scores_lower_than_equivalent_body_block` | PASSED |
| 20 | `test_appendix_score_is_multiplied_by_04` | PASSED |
| 21 | `test_caption_block_is_still_retrieved` | PASSED |
| 22 | `test_caption_block_scores_lower_than_equivalent_paragraph` | PASSED |
| 23 | `test_caption_score_is_multiplied_by_06` | PASSED |
| 24 | `test_sorting_is_score_descending` | PASSED |
| 25 | `test_sorting_block_id_ascending_when_scores_are_equal` | PASSED |
| 26 | `test_sorting_is_stable_across_repeated_calls` | PASSED |
| 27 | `test_max_candidates_limits_returned_results` | PASSED |
| 28 | `test_max_candidates_zero_returns_empty_list` | PASSED |
| 29 | `test_max_candidates_larger_than_hits_returns_all_hits` | PASSED |
| 30 | `test_max_candidates_one_returns_highest_scoring_block` | PASSED |
| 31 | `test_retrieve_all_criteria_returns_all_five_criterion_keys` | PASSED |
| 32 | `test_retrieve_all_criteria_empty_list_when_no_blocks_match` | PASSED |
| 33 | `test_retrieve_all_criteria_populates_matching_criterion` | PASSED |
| 34 | `test_retrieve_all_criteria_some_empty_some_populated` | PASSED |
| 35 | `test_retrieve_all_criteria_respects_max_candidates` | PASSED |

**Coverage areas:**
- Excluded block types (`front_matter`, `noise`, `template`, `is_front_matter` flag)
- Type-only match rejection
- Heading hint matching and `matched_heading_hints` population
- Text hint matching and `matched_text_hints` population
- Table searchability (block text, header row as text hint, header row as heading hint, cells, None cells)
- Appendix downweighting (factor 0.4, exact score check)
- Caption downweighting (factor 0.6, exact score check)
- Sort order (score desc, block_id asc, stability)
- `max_candidates` enforcement (limit, zero, over-limit, top-1)
- `retrieve_all_criteria` (all keys present, empty lists, routing, max_candidates)

---

## `tests/caps/test_checks.py`

**Date:** —
**Status:** —

<!-- paste results here -->

---

## `tests/caps/test_scoring.py`

**Date:** —
**Status:** —

<!-- paste results here -->

---

## Template for new modules

```
## `tests/caps/test_<module>.py`

**Date:** YYYY-MM-DD
**Status:** N/N PASSED

| # | Test | Result |
|---|------|--------|
| 1 | `test_name` | PASSED / FAILED |

**Coverage areas:**
- …
```
