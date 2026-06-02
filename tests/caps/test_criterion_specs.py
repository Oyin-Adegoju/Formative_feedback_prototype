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

# ---------------------------------------------------------------------------
# Individual criterion: BEPERKING
# ---------------------------------------------------------------------------


def test_beperking_key():
    assert BEPERKING.key == "beperking"


def test_beperking_label():
    assert BEPERKING.label == "Beperking & deskresearch"


def test_beperking_minimum_count_is_none():
    assert BEPERKING.minimum_count is None


def test_beperking_strong_from_is_none():
    assert BEPERKING.strong_from is None


def test_beperking_heading_hints():
    for hint in ("beperking", "deskresearch", "onderzoek", "doelgroep", "bron"):
        assert hint in BEPERKING.heading_hints, f"BEPERKING.heading_hints missing '{hint}'"


def test_beperking_text_hints():
    for hint in ("beperking", "deskresearch", "doelgroep met beperking", "bron", "onderzoek"):
        assert hint in BEPERKING.text_hints, f"BEPERKING.text_hints missing '{hint}'"

# ---------------------------------------------------------------------------
# Individual criterion: STAKEHOLDERS
# ---------------------------------------------------------------------------


def test_stakeholders_key():
    assert STAKEHOLDERS.key == "stakeholders"


def test_stakeholders_minimum_count():
    assert STAKEHOLDERS.minimum_count == 4


def test_stakeholders_strong_from():
    assert STAKEHOLDERS.strong_from == 6


def test_stakeholders_heading_hints():
    assert "stakeholder" in STAKEHOLDERS.heading_hints


def test_stakeholders_text_hints():
    for hint in ("stakeholder", "belang", "invloed"):
        assert hint in STAKEHOLDERS.text_hints, f"STAKEHOLDERS.text_hints missing '{hint}'"
# ---------------------------------------------------------------------------
# Individual criterion: REQUIREMENTS
# ---------------------------------------------------------------------------


def test_requirements_key():
    assert REQUIREMENTS.key == "requirements"


def test_requirements_minimum_count():
    assert REQUIREMENTS.minimum_count == 15


def test_requirements_strong_from():
    assert REQUIREMENTS.strong_from == 25


def test_requirements_heading_hints():
    for hint in ("requirement", "functionele", "niet-functionele", "moscow", "use case"):
        assert hint in REQUIREMENTS.heading_hints, f"REQUIREMENTS.heading_hints missing '{hint}'"


def test_requirements_text_hints():
    for hint in ("requirement", "must have", "should have", "could have", "FR", "NFR", "systeem moet"):
        assert hint in REQUIREMENTS.text_hints, f"REQUIREMENTS.text_hints missing '{hint}'"
# ---------------------------------------------------------------------------
# Individual criterion: TAALKEUZE
# ---------------------------------------------------------------------------


def test_taalkeuze_key():
    assert TAALKEUZE.key == "taalkeuze"


def test_taalkeuze_minimum_count_is_none():
    assert TAALKEUZE.minimum_count is None


def test_taalkeuze_strong_from_is_none():
    assert TAALKEUZE.strong_from is None


def test_taalkeuze_heading_hints():
    for hint in ("taalkeuze", "taal", "meertaligheid"):
        assert hint in TAALKEUZE.heading_hints, f"TAALKEUZE.heading_hints missing '{hint}'"


def test_taalkeuze_text_hints():
    for hint in ("taal", "meertalig", "nederlands", "engels", "vertaling"):
        assert hint in TAALKEUZE.text_hints, f"TAALKEUZE.text_hints missing '{hint}'"


# ---------------------------------------------------------------------------
# Individual criterion: SECURITY
# ---------------------------------------------------------------------------


def test_security_key():
    assert SECURITY.key == "security"


def test_security_minimum_count():
    assert SECURITY.minimum_count == 1


def test_security_strong_from():
    assert SECURITY.strong_from == 3


def test_security_heading_hints():
    for hint in ("security", "beveiliging", "privacy", "avg"):
        assert hint in SECURITY.heading_hints, f"SECURITY.heading_hints missing '{hint}'"


def test_security_text_hints():
    for hint in ("security", "beveiliging", "authenticatie", "autorisatie", "encryptie", "avg", "privacy", "owasp"):
        assert hint in SECURITY.text_hints, f"SECURITY.text_hints missing '{hint}'"

# ---------------------------------------------------------------------------
# Lookup constants
# ---------------------------------------------------------------------------


def test_criteria_keys_order_matches_registry():
    expected = tuple(c.key for c in INFIRFS_REQUIREMENTS_CRITERIA)
    assert CRITERIA_KEYS == expected


def test_criteria_by_key_contains_all_keys():
    for criterion in INFIRFS_REQUIREMENTS_CRITERIA:
        assert criterion.key in CRITERIA_BY_KEY


def test_criteria_by_key_returns_same_object():
    for criterion in INFIRFS_REQUIREMENTS_CRITERIA:
        assert CRITERIA_BY_KEY[criterion.key] is criterion


def test_blocker_keys_contains_all_5_keys():
    assert BLOCKER_KEYS == {c.key for c in INFIRFS_REQUIREMENTS_CRITERIA}
    assert len(BLOCKER_KEYS) == 5


def test_countable_keys_exact_set():
    assert COUNTABLE_KEYS == {"stakeholders", "requirements", "security"}


def test_beperking_not_in_countable_keys():
    assert "beperking" not in COUNTABLE_KEYS


def test_taalkeuze_not_in_countable_keys():
    assert "taalkeuze" not in COUNTABLE_KEYS

