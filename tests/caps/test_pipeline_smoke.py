"""End-to-end smoke test for the CAPS pipeline.

Exercises the real pipeline (retrieval → checks → scoring) without mocking.
Two synthetic reports are fed through run_caps: one strong, one weak.
"""

from __future__ import annotations

from src.caps.caps import run_caps
from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import CapsRunResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STOPLIGHT_RANK = {"red": 0, "yellow": 1, "green": 2}


def _block(
    block_id: str,
    page_no: int,
    block_type: str,
    text: str,
    heading_path: list[str],
    *,
    is_front_matter: bool = False,
    is_appendix: bool = False,
    table_meta: dict | None = None,
) -> dict:
    b: dict = {
        "block_id": block_id,
        "page_no": page_no,
        "block_type": block_type,
        "text": text,
        "heading_path": heading_path,
        "is_front_matter": is_front_matter,
        "is_appendix": is_appendix,
    }
    if table_meta is not None:
        b["table_meta"] = table_meta
    return b


# ---------------------------------------------------------------------------
# Strong report — provides clear evidence for all 5 criteria
# ---------------------------------------------------------------------------

_STRONG_BLOCKS = [
    # Criterion 1 – beperking: limitation + substantive research in 2 body blocks each
    _block(
        "s01", 2, "paragraph",
        "De gekozen beperking is een visuele beperking. Wij richten ons op "
        "slechtziende en blinde gebruikers. Uit deskresearch blijkt dat deze "
        "doelgroep met beperking beperkte toegang heeft tot online winkels.",
        ["Beperking & deskresearch"],
    ),
    _block(
        "s02", 3, "paragraph",
        "Literatuuronderzoek toont aan dat toegankelijkheid voor de doelgroep "
        "met beperking essentieel is voor inclusie. Bron: WHO rapport 2023. "
        "Onderzoek van Universiteit Utrecht bevestigt de gekozen afbakening.",
        ["Beperking & deskresearch"],
    ),
    # Criterion 2 – stakeholders: 7 named roles in a table + belang + invloed in text
    _block(
        "s03", 5, "table",
        "Stakeholder belang invloed Opdrachtgever hoog laag Eindgebruiker hoog hoog "
        "Ontwikkelaar middel hoog Projectmanager hoog hoog Designer middel middel "
        "Beheerder laag hoog Leverancier laag laag",
        ["Stakeholders", "Stakeholder analyse"],
        table_meta={
            "row_count": 7,
            "column_count": 3,
            "header_row": "Stakeholder | Belang | Invloed",
            "cells": [
                ["Opdrachtgever", "hoog", "laag"],
                ["Eindgebruiker", "hoog", "hoog"],
                ["Ontwikkelaar", "middel", "hoog"],
                ["Projectmanager", "hoog", "hoog"],
                ["Designer", "middel", "middel"],
                ["Beheerder", "laag", "hoog"],
                ["Leverancier", "laag", "laag"],
            ],
            "index_cells": [],
        },
    ),
    # Criterion 3 – requirements: 26 unique requirement IDs (20 FR + 6 NFR) + MoSCoW prio
    _block(
        "s04", 7, "paragraph",
        "FR-01 must have FR-02 must have FR-03 should have FR-04 should have "
        "FR-05 could have FR-06 must have FR-07 must have FR-08 should have "
        "FR-09 could have FR-10 must have FR-11 must have FR-12 should have "
        "FR-13 could have FR-14 must have FR-15 must have FR-16 should have "
        "FR-17 should have FR-18 could have FR-19 must have FR-20 should have "
        "NFR-01 must have NFR-02 should have NFR-03 could have "
        "NFR-04 must have NFR-05 should have NFR-06 could have",
        ["Requirements", "Functionele eisen"],
    ),
    # Criterion 4 – taalkeuze: explicit language choice + 4 consequence signals
    _block(
        "s05", 9, "paragraph",
        "De webshop wordt volledig in het nederlands aangeboden. Dit heeft "
        "gevolgen voor het bereik van de doelgroep. Vertaalkosten worden "
        "vermeden. Lokalisatie is niet nodig. De begrijpelijkheid voor "
        "nederlandstalige gebruikers wordt hiermee geoptimaliseerd.",
        ["Taalkeuze"],
    ),
    # Criterion 5 – security: 3 concrete mechanisms
    _block(
        "s06", 11, "paragraph",
        "De webshop maakt gebruik van authenticatie via een inlogsysteem. "
        "Autorisatie is geregeld via gebruikersrollen. Encryptie wordt "
        "toegepast conform de beveiligingsrichtlijnen van het project.",
        ["Security", "Beveiliging"],
    ),
]

STRONG_REPORT: dict = {
    "doc_id": "smoke-strong-001",
    "source_name": "strong_student_report.pdf",
    "page_count": 15,
    "block_count": len(_STRONG_BLOCKS),
    "blocks": _STRONG_BLOCKS,
}

# ---------------------------------------------------------------------------
# Weak report — thin or missing evidence for every criterion
# ---------------------------------------------------------------------------

_WEAK_BLOCKS = [
    # beperking: only limitation signal, no research
    _block(
        "w01", 1, "paragraph",
        "We richten ons op een doelgroep met beperking.",
        ["Inleiding"],
    ),
    # stakeholders: 2 roles, no belang/invloed
    _block(
        "w02", 2, "table",
        "Opdrachtgever Eindgebruiker",
        ["Stakeholders"],
        table_meta={
            "row_count": 2,
            "column_count": 1,
            "header_row": None,
            "cells": [["Opdrachtgever"], ["Eindgebruiker"]],
            "index_cells": [],
        },
    ),
    # requirements: only 5 IDs
    _block(
        "w03", 3, "paragraph",
        "FR-01 FR-02 FR-03 FR-04 FR-05",
        ["Eisen"],
    ),
    # taalkeuze: language only, no consequences
    _block(
        "w04", 4, "paragraph",
        "De webshop is in het nederlands.",
        ["Inleiding"],
    ),
    # security: generic language only, no concrete mechanism
    _block(
        "w05", 5, "paragraph",
        "De webshop moet veilig zijn. Beveiliging is belangrijk.",
        ["Inleiding"],
    ),
]

WEAK_REPORT: dict = {
    "doc_id": "smoke-weak-001",
    "source_name": "weak_student_report.pdf",
    "page_count": 8,
    "block_count": len(_WEAK_BLOCKS),
    "blocks": _WEAK_BLOCKS,
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_strong_report_completes_without_error():
    result = run_caps(STRONG_REPORT)

    assert isinstance(result, CapsRunResult)
    assert set(result.scorecard.results.keys()) == set(CRITERIA_KEYS)
    assert isinstance(result.scorecard.hidden_score, int)
    assert result.overall_stoplight in {"red", "yellow", "green"}


def test_weak_report_completes_without_error():
    result = run_caps(WEAK_REPORT)

    assert isinstance(result, CapsRunResult)
    assert set(result.scorecard.results.keys()) == set(CRITERIA_KEYS)
    assert isinstance(result.scorecard.hidden_score, int)
    assert result.overall_stoplight in {"red", "yellow", "green"}


def test_strong_scores_higher_than_weak():
    strong = run_caps(STRONG_REPORT)
    weak = run_caps(WEAK_REPORT)

    assert strong.scorecard.hidden_score > weak.scorecard.hidden_score


def test_strong_stoplight_not_worse_than_weak():
    strong = run_caps(STRONG_REPORT)
    weak = run_caps(WEAK_REPORT)

    assert _STOPLIGHT_RANK[strong.overall_stoplight] >= _STOPLIGHT_RANK[weak.overall_stoplight]


# ---------------------------------------------------------------------------
# missing_signals and evidence availability (downstream Qwen contract)
# ---------------------------------------------------------------------------


def test_criterion_result_output_fields_always_present():
    """Every criterion result must expose notes, missing_signals, and evidence as lists."""
    result = run_caps(STRONG_REPORT)
    for key, r in result.scorecard.results.items():
        assert isinstance(r.notes, list), f"{key}: notes must be a list"
        assert isinstance(r.missing_signals, list), f"{key}: missing_signals must be a list"
        assert isinstance(r.evidence, list), f"{key}: evidence must be a list"


def test_strong_report_has_no_unexpected_missing_signals():
    """Strong report provides full evidence — all criteria should have empty missing_signals."""
    result = run_caps(STRONG_REPORT)
    for key, r in result.scorecard.results.items():
        assert r.missing_signals == [], (
            f"{key}: expected no missing_signals for strong report, got {r.missing_signals}"
        )


def test_weak_report_has_missing_signals_on_most_criteria():
    """Weak report has thin evidence — most criteria should report missing signals."""
    result = run_caps(WEAK_REPORT)
    criteria_with_missing = [
        key for key, r in result.scorecard.results.items() if r.missing_signals
    ]
    assert len(criteria_with_missing) >= 3, (
        f"Expected ≥3 criteria with missing_signals in weak report, "
        f"got {len(criteria_with_missing)}: {criteria_with_missing}"
    )


def test_no_manual_review_fields_on_run_result():
    """CapsRunResult must not expose manual_review_required or manual_review_flags."""
    result = run_caps(STRONG_REPORT)
    assert not hasattr(result, "manual_review_required")
    assert not hasattr(result, "manual_review_flags")


def test_no_manual_review_field_on_criterion_result():
    """CriterionResult must not expose manual_review."""
    result = run_caps(STRONG_REPORT)
    for key, r in result.scorecard.results.items():
        assert not hasattr(r, "manual_review"), (
            f"{key}: manual_review must not be present on CriterionResult"
        )


def test_caps_overall_stoplight_is_never_yellow():
    """Document-level CAPS stoplight is red or green only — never yellow.

    All current criteria are blockers, so CAPS alone yields only red (blocked)
    or green (all pass). Document-level yellow is reserved for the downstream
    Qwen stage. (Per-criterion stoplight may still be yellow — that is a
    separate level; see models.StoplightLabel.)
    """
    for report in (STRONG_REPORT, WEAK_REPORT):
        result = run_caps(report)
        assert result.overall_stoplight in ("red", "green"), (
            f"unexpected document stoplight {result.overall_stoplight!r}"
        )
