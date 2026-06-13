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
    assert spec.downstream_review_hint == ""
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
        assert criterion.downstream_review_hint, f"{criterion.key}: downstream_review_hint is empty"
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


def test_stakeholders_hints_exclude_generic_user_terms():
    """Generic user/customer/role words must not be stakeholder hints.

    These words occur across doelgroep, requirements, use cases and taalkeuze,
    so promoting them to stakeholder hints floods retrieval with non-stakeholder
    content. Encodes the precision decision rather than snapshotting every hint.
    """
    generic = {"gebruikers", "gebruiker", "klant", "klanten", "rol", "functie"}
    overlap_heading = generic & set(STAKEHOLDERS.heading_hints)
    overlap_text = generic & set(STAKEHOLDERS.text_hints)
    assert not overlap_heading, f"generic terms leaked into heading_hints: {overlap_heading}"
    assert not overlap_text, f"generic terms leaked into text_hints: {overlap_text}"
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

# ---------------------------------------------------------------------------
# Smoke test helpers
# ---------------------------------------------------------------------------


def _criterion_has_hint_match(criterion: CriterionSpec, report: dict) -> bool:
    """Return True if any block in *report* matches a hint for *criterion*.

    Only inspects blocks whose block_type is in criterion.relevant_block_types.
    A match is:
      - a heading_hints item found in any lowercase entry of block["heading_path"], or
      - a text_hints item found in lowercase block["text"].
    """
    for block in report.get("blocks", []):
        if block.get("block_type") not in criterion.relevant_block_types:
            continue
        heading_path_lower = [h.lower() for h in block.get("heading_path", [])]
        text_lower = (block.get("text") or "").lower()
        for hint in criterion.heading_hints:
            if any(hint in entry for entry in heading_path_lower):
                return True
        for hint in criterion.text_hints:
            if hint in text_lower:
                return True
    return False

# ---------------------------------------------------------------------------
# Smoke test 1: reports load and contain content blocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("report_path", _REPORT_FILES, ids=lambda p: p.name)
def test_report_has_blocks(report_path: Path):
    report = _load_report(report_path)
    assert isinstance(report.get("blocks"), list)
    assert len(report["blocks"]) > 0


@pytest.mark.parametrize("report_path", _REPORT_FILES, ids=lambda p: p.name)
def test_report_contains_at_least_one_content_block(report_path: Path):
    report = _load_report(report_path)
    content_types = {"heading", "paragraph", "bullet", "table"}
    found = any(b.get("block_type") in content_types for b in report["blocks"])
    assert found, f"{report_path.name}: no content block found"
# ---------------------------------------------------------------------------
# Smoke test 2 + 3: every criterion has at least one hint match across reports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "criterion",
    INFIRFS_REQUIREMENTS_CRITERIA,
    ids=lambda c: c.key,
)
def test_criterion_has_hint_match_in_at_least_one_report(criterion: CriterionSpec):
    reports = [_load_report(p) for p in _REPORT_FILES]
    matched = any(_criterion_has_hint_match(criterion, r) for r in reports)
    assert matched, (
        f"Criterion '{criterion.key}' found no hint match in any of the 3 parser reports. "
        "Check that heading_hints and text_hints are compatible with real parser output."
    )
# ---------------------------------------------------------------------------
# Smoke test 4: non-content blocks are ignored by the helper
# ---------------------------------------------------------------------------


def test_non_content_blocks_ignored_by_helper():
    fake_report: dict = {
        "blocks": [
            {
                "block_type": "front_matter",
                "heading_path": ["beperking", "stakeholder", "security"],
                "text": "beperking stakeholder security requirements taalkeuze",
            },
            {
                "block_type": "noise",
                "heading_path": ["beperking"],
                "text": "must have should have FR NFR",
            },
        ]
    }
    for criterion in INFIRFS_REQUIREMENTS_CRITERIA:
        assert not _criterion_has_hint_match(criterion, fake_report), (
            f"Criterion '{criterion.key}': helper matched a non-content block"
        )
