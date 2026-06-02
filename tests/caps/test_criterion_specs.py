"""Tests for src/caps/criterion_specs.py."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from caps.criterion_specs import (
    BEPERKING,
    BLOCKER_KEYS,
    COUNTABLE_KEYS,
    CRITERIA_BY_KEY,
    CRITERIA_KEYS,
    INFIRFS_REQUIREMENTS_CRITERIA,
    REQUIREMENTS,
    SECURITY,
    STAKEHOLDERS,
    TAALKEUZE,
    CriterionSpec,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "parser_reports"

_REPORT_FILES = [
    _FIXTURES / "2e138dc4_report.json",
    _FIXTURES / "2f4d9d91_report.json",
    _FIXTURES / "35baf7d3_report.json",
]


def _load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
# CriterionSpec dataclass
# ---------------------------------------------------------------------------


def test_criterion_spec_is_frozen():
    spec = CriterionSpec(
        key="test",
        label="Test",
        description="desc",
        is_blocker=False,
        relevant_block_types=frozenset({"paragraph"}),
        heading_hints=("hint",),
        text_hints=("hint",),
    )
    with pytest.raises((FrozenInstanceError, AttributeError)):
        spec.key = "modified"  # type: ignore[misc]


def test_criterion_spec_optional_defaults():
    spec = CriterionSpec(
        key="x",
        label="X",
        description="d",
        is_blocker=True,
        relevant_block_types=frozenset({"paragraph"}),
        heading_hints=("h",),
        text_hints=("t",),
    )
    assert spec.minimum_count is None
    assert spec.strong_from is None
    assert spec.notes == ""
    assert spec.manual_review_trigger == ""
# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_contains_exactly_5_criteria():
    assert len(INFIRFS_REQUIREMENTS_CRITERIA) == 5


def test_registry_order():
    keys = [c.key for c in INFIRFS_REQUIREMENTS_CRITERIA]
    assert keys == ["beperking", "stakeholders", "requirements", "taalkeuze", "security"]


def test_registry_all_items_are_criterion_spec():
    for item in INFIRFS_REQUIREMENTS_CRITERIA:
        assert isinstance(item, CriterionSpec)


def test_registry_all_keys_are_unique():
    keys = [c.key for c in INFIRFS_REQUIREMENTS_CRITERIA]
    assert len(keys) == len(set(keys))


def test_registry_all_criteria_have_required_fields():
    for criterion in INFIRFS_REQUIREMENTS_CRITERIA:
        assert criterion.key, f"{criterion.key}: key is empty"
        assert criterion.label, f"{criterion.key}: label is empty"
        assert criterion.description, f"{criterion.key}: description is empty"
        assert criterion.is_blocker is True, f"{criterion.key}: is_blocker is not True"
        assert criterion.relevant_block_types, f"{criterion.key}: relevant_block_types is empty"
        assert criterion.heading_hints, f"{criterion.key}: heading_hints is empty"
        assert criterion.text_hints, f"{criterion.key}: text_hints is empty"
        assert criterion.notes, f"{criterion.key}: notes is empty"
        assert criterion.manual_review_trigger, f"{criterion.key}: manual_review_trigger is empty"
# ---------------------------------------------------------------------------
# Relevant block types
# ---------------------------------------------------------------------------

_EXPECTED_CONTENT_TYPES = {"heading", "paragraph", "bullet", "table"}
_EXCLUDED_BLOCK_TYPES = {"front_matter", "noise", "template", "caption", "appendix"}


def test_all_criteria_use_exactly_content_block_types():
    for criterion in INFIRFS_REQUIREMENTS_CRITERIA:
        assert criterion.relevant_block_types == frozenset(_EXPECTED_CONTENT_TYPES), (
            f"{criterion.key}: relevant_block_types differs from expected content types"
        )


def test_non_content_block_types_excluded():
    for criterion in INFIRFS_REQUIREMENTS_CRITERIA:
        for excluded in _EXCLUDED_BLOCK_TYPES:
            assert excluded not in criterion.relevant_block_types, (
                f"{criterion.key}: '{excluded}' should not be in relevant_block_types"
            )

