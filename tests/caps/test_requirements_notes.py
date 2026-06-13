"""Unit tests for the requirements criterion: section classification and notes.

Focus areas (per the requirements-note fix):
  1. Dutch functional / non-functional headings split correctly.
  2. English functional / non-functional headings split correctly.
  3. MoSCoW (Must/Should/Could/Won't) split PER requirement type.
  4. Spelling variants: "non functional" / "non-functional" / "functioneel" /
     "niet-functioneel" / "won't" / "wont".
  5. Combined "(non-)" headings are not guessed (classified "other").
  6. A realistic anonymized-report shape (loaded from data/anonymized) keeps
     both functional and non-functional requirements visible.

These tests build RetrievalHit objects directly so they exercise checks.py in
isolation, without the parser or retrieval layers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.caps.checks import (
    _classify_section,
    _hit_section,
    _id_type_counts,
    check_requirements,
)
from src.caps.criterion_specs import REQUIREMENTS
from src.caps.models import BlockDict
from src.caps.retrieval import RetrievalHit, retrieve_for_criterion

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _table_block(
    block_id: str,
    heading_path: list[str],
    rows: list[list[str | None]],
    page_no: int = 1,
) -> BlockDict:
    text = " ".join(c for row in rows for c in row if c)
    return {
        "block_id": block_id,
        "page_no": page_no,
        "block_type": "table",
        "text": text,
        "heading_path": heading_path,
        "is_front_matter": False,
        "is_appendix": False,
        "table_meta": {
            "row_count": len(rows),
            "column_count": max((len(r) for r in rows), default=0),
            "header_row": None,
            "cells": rows,
            "index_cells": [],
        },
    }


def _hit(block: BlockDict) -> RetrievalHit:
    return RetrievalHit(block=block, score=10.0)


def _req_rows(prefix: str, priorities: list[str]) -> list[list[str | None]]:
    """Build a requirements table: a header row plus one row per priority."""
    rows: list[list[str | None]] = [["Naam", "Omschrijving", "Prio"]]
    for i, prio in enumerate(priorities, start=1):
        rows.append(
            [f"{prefix}-{i:02d}", "De website moet iets kunnen doen.", prio]
        )
    return rows


def _notes_text(heading_fr: str, heading_nfr: str) -> str:
    """Run check_requirements on one FR table + one NFR table; return joined notes.

    FR priorities : MUST, MUST, SHOULD, COULD, WON'T  -> 5 functional items
    NFR priorities: MUST, MUST, SHOULD               -> 3 non-functional items
    """
    fr = _hit(
        _table_block(
            "b_fr",
            [heading_fr],
            _req_rows("FR", ["MUST HAVE", "MUST HAVE", "SHOULD HAVE",
                             "COULD HAVE", "WON'T HAVE"]),
        )
    )
    nfr = _hit(
        _table_block(
            "b_nfr",
            [heading_nfr],
            _req_rows("NFR", ["MUST HAVE", "MUST HAVE", "SHOULD HAVE"]),
        )
    )
    result = check_requirements(REQUIREMENTS, [fr, nfr])
    return "\n".join(result.notes)


# ---------------------------------------------------------------------------
# 1. _classify_section — Dutch, English, and spelling variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "heading,expected",
    [
        # Dutch
        ("Functionele requirements", "fr"),
        ("Functioneel", "fr"),
        ("Niet-functionele requirements", "nfr"),
        ("Niet-functioneel", "nfr"),
        ("Niet functioneel", "nfr"),
        # English
        ("Functional requirements", "fr"),
        ("Non-functional requirements", "nfr"),
        ("Non functional requirements", "nfr"),
        # Mixed / casing / spacing
        ("NON-FUNCTIONAL REQUIREMENTS", "nfr"),
        ("non functioneel requirement", "nfr"),
        # Use cases
        ("Use cases", "uc"),
        ("Use case 01: inloggen", "uc"),
        ("Use-case beschrijving", "uc"),
        # Combined header → not guessed
        ("Constraints en (non-) Functionele eisen", "other"),
        ("4.1 Constraints en (niet-) functionele eisen", "other"),
        # Unrelated
        ("Constraints", "other"),
        ("Stakeholder analyse", "other"),
    ],
)
def test_classify_section_single_heading(heading: str, expected: str) -> None:
    assert _classify_section([heading]) == expected


def test_classify_section_prefers_deepest_heading() -> None:
    # A precise subsection wins over a broad combined parent.
    path = ["Constraints en (non-) Functionele eisen", "Niet-functionele requirements"]
    assert _classify_section(path) == "nfr"

    path_fr = ["Constraints en (non-) Functionele eisen", "Functionele requirements"]
    assert _classify_section(path_fr) == "fr"


def test_classify_section_constraints_does_not_inherit() -> None:
    # The original bug: a "Constraints" block must NOT be classified as the
    # section that happened to precede it. With per-block classification it is
    # always "other" regardless of neighbours.
    assert _classify_section(["Constraints"]) == "other"


# ---------------------------------------------------------------------------
# 2. Dutch headings — counts and breakdown
# ---------------------------------------------------------------------------


def test_dutch_headings_split_functional_and_non_functional() -> None:
    notes = _notes_text("Functionele requirements", "Niet-functionele requirements")
    assert "Functioneel: ~5" in notes
    assert "Niet-functioneel: ~3" in notes


def test_dutch_short_headings_functioneel_niet_functioneel() -> None:
    notes = _notes_text("Functioneel", "Niet-functioneel")
    assert "Functioneel: ~5" in notes
    assert "Niet-functioneel: ~3" in notes


# ---------------------------------------------------------------------------
# 3. English headings — counts and breakdown
# ---------------------------------------------------------------------------


def test_english_headings_split_functional_and_non_functional() -> None:
    notes = _notes_text("Functional requirements", "Non-functional requirements")
    assert "Functioneel: ~5" in notes
    assert "Niet-functioneel: ~3" in notes


def test_english_non_functional_without_hyphen() -> None:
    notes = _notes_text("Functional requirements", "Non functional requirements")
    assert "Functioneel: ~5" in notes
    assert "Niet-functioneel: ~3" in notes


# ---------------------------------------------------------------------------
# 4. MoSCoW split per requirement type
# ---------------------------------------------------------------------------


def test_moscow_split_per_type() -> None:
    notes = _notes_text("Functionele requirements", "Niet-functionele requirements")
    # Functional priorities: MUST x2, SHOULD x1, COULD x1, WON'T x1
    assert "Functioneel prioriteiten: Must: 2, Should: 1, Could: 1, Won't: 1." in notes
    # Non-functional priorities: MUST x2, SHOULD x1
    assert "Niet-functioneel prioriteiten: Must: 2, Should: 1." in notes


def test_moscow_wont_variants_counted_as_wont() -> None:
    # "WON'T HAVE", "WONT HAVE", and bare "Won't" must all land in the Won't tier.
    fr = _hit(
        _table_block(
            "b_fr",
            ["Functionele requirements"],
            _req_rows("FR", ["MUST HAVE", "WON'T HAVE", "WONT HAVE", "Won't"]),
        )
    )
    result = check_requirements(REQUIREMENTS, [fr])
    notes = "\n".join(result.notes)
    assert "Functioneel prioriteiten: Must: 1, Won't: 3." in notes


# ---------------------------------------------------------------------------
# 5. Combined "(non-)" section is not guessed
# ---------------------------------------------------------------------------


def test_combined_section_not_split() -> None:
    combined = _hit(
        _table_block(
            "b_comb",
            ["Constraints en (non-) Functionele eisen"],
            _req_rows("REQ", ["MUST HAVE", "SHOULD HAVE", "COULD HAVE"]),
        )
    )
    result = check_requirements(REQUIREMENTS, [combined])
    notes = "\n".join(result.notes)
    assert "niet apart te onderscheiden" in notes
    # No fabricated functional/non-functional split:
    assert "Functioneel: ~" not in notes
    assert "Niet-functioneel: ~" not in notes
    # An overall (un-split) MoSCoW distribution is still reported:
    assert "Prioriteitsverdeling: Must: 1, Should: 1, Could: 1." in notes


# ---------------------------------------------------------------------------
# 5b. Combined / unlabelled sections: per-row FR/NFR ID fallback
# ---------------------------------------------------------------------------


def _combined(block_id: str, prefix: str, priorities: list[str]) -> RetrievalHit:
    return _hit(
        _table_block(
            block_id,
            ["Constraints en (non-) Functionele eisen"],
            _req_rows(prefix, priorities),
        )
    )


def test_combined_section_recovers_nfr_from_row_ids() -> None:
    # Heading is combined, but the rows are tagged NFR01.. → split is recovered.
    hit = _combined("b", "NFR", ["MUST HAVE", "MUST HAVE", "SHOULD HAVE"])
    notes = "\n".join(check_requirements(REQUIREMENTS, [hit]).notes)
    assert "Niet-functioneel: ~3" in notes
    assert "niet apart te onderscheiden" not in notes
    assert "Niet-functioneel prioriteiten: Must: 2, Should: 1." in notes


def test_combined_section_recovers_fr_from_row_ids() -> None:
    hit = _combined("b", "FR", ["MUST HAVE", "SHOULD HAVE"])
    notes = "\n".join(check_requirements(REQUIREMENTS, [hit]).notes)
    assert "Functioneel: ~2" in notes
    assert "niet apart te onderscheiden" not in notes


def test_combined_section_mixed_ids_split_by_id() -> None:
    rows: list[list[str | None]] = [
        ["Naam", "Omschrijving", "Prio"],
        ["FR-01", "De website moet iets doen.", "MUST HAVE"],
        ["FR-02", "De website moet iets doen.", "SHOULD HAVE"],
        ["NFR-01", "De website moet iets doen.", "MUST HAVE"],
        ["NFR-02", "De website moet iets doen.", "COULD HAVE"],
    ]
    hit = _hit(_table_block("b", ["Constraints en (non-) Functionele eisen"], rows))
    notes = "\n".join(check_requirements(REQUIREMENTS, [hit]).notes)
    # Each ID type contributes its own row count.
    assert "Functioneel: ~2" in notes
    assert "Niet-functioneel: ~2" in notes
    # A block mixing both ID types does not get a per-type MoSCoW split; it
    # falls back to an overall distribution instead of guessing per row.
    assert "Prioriteitsverdeling: Must: 2, Should: 1, Could: 1." in notes


def test_combined_section_constraint_ids_stay_other() -> None:
    # CONSTR ids are neither FR nor NFR → no split is invented.
    hit = _combined("b", "CONSTR", ["MUST HAVE", "MUST HAVE"])
    notes = "\n".join(check_requirements(REQUIREMENTS, [hit]).notes)
    assert "niet apart te onderscheiden" in notes
    assert "Functioneel: ~" not in notes
    assert "Niet-functioneel: ~" not in notes


def test_clear_heading_overrides_row_ids() -> None:
    # When the heading is clearly functional, rows tagged NFR do NOT flip it.
    hit = _hit(
        _table_block(
            "b",
            ["Functionele requirements"],
            _req_rows("NFR", ["MUST HAVE", "SHOULD HAVE"]),
        )
    )
    notes = "\n".join(check_requirements(REQUIREMENTS, [hit]).notes)
    assert "Functioneel: ~2" in notes
    assert "Niet-functioneel: ~" not in notes


def test_prose_mention_does_not_create_split() -> None:
    # Body prose mentioning "functionele/niet-functionele" must NOT classify a
    # block — only headings and per-row ids do. Without those, it stays "other".
    block: BlockDict = {
        "block_id": "b",
        "page_no": 1,
        "block_type": "paragraph",
        "text": (
            "1. De website moet zowel functionele als niet-functionele eisen "
            "ondersteunen volgens het plan."
        ),
        "heading_path": ["Requirements"],
        "is_front_matter": False,
        "is_appendix": False,
    }
    notes = "\n".join(check_requirements(REQUIREMENTS, [_hit(block)]).notes)
    assert "Functioneel: ~" not in notes
    assert "Niet-functioneel: ~" not in notes
    assert "niet apart te onderscheiden" in notes


def test_id_type_counts_and_hit_section_helpers() -> None:
    nfr_hit = _combined("b", "NFR", ["MUST HAVE", "SHOULD HAVE"])
    fr_ids, nfr_ids = _id_type_counts(nfr_hit)
    assert fr_ids == set()
    assert nfr_ids == {"NFR01", "NFR02"}
    assert _hit_section(nfr_hit) == "nfr"

    # "FR 01" / "FR-01" / "FR01" normalise to the same id.
    rows: list[list[str | None]] = [["FR 01", "x", "MUST HAVE"], ["FR-01", "y", "MUST HAVE"]]
    dup = _hit(_table_block("b", ["Constraints en (non-) Functionele eisen"], rows))
    fr_ids, _ = _id_type_counts(dup)
    assert fr_ids == {"FR01"}


# ---------------------------------------------------------------------------
# 6. Realistic anonymized-report shape
# ---------------------------------------------------------------------------


def test_realistic_report_keeps_both_types_visible() -> None:
    # English "Functional requirements" + Dutch "Niet-functionele requirements".
    doc = _REPO_ROOT / "data" / "anonymized" / "Voldoende" / "1023e8a9_anonymized.json"
    if not doc.exists():
        pytest.skip("anonymized fixture not available")

    report = json.loads(doc.read_text(encoding="utf-8"))
    hits = retrieve_for_criterion(report["blocks"], REQUIREMENTS, 40)
    result = check_requirements(REQUIREMENTS, hits)
    notes = "\n".join(result.notes)

    # Both functional and non-functional requirements must be detected — the
    # original drift bug pushed everything into a single bucket.
    assert "Functioneel: ~" in notes
    assert "Niet-functioneel: ~" in notes
    # Per-type MoSCoW lines are present for at least one type.
    assert "prioriteiten:" in notes
