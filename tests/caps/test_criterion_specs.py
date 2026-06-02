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


