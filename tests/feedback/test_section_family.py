"""Tests for the section-family classifier and the section-first evidence gating.

Two layers:
  1. Unit tests for section_family.families_of / negation (pure, corpus-derived).
  2. Integration tests over the 9 real anonymized documents asserting the
     section-first guarantees the packet builder must now uphold:
       - no foreign-section block is admitted WITHOUT an explicit rescue warning
       - the known contamination blocks are gone from the wrong criteria
       - negated research/limitation signals are never surfaced as positive
       - representative requirements coverage is preserved
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from src.caps.caps import run_caps_with_artifacts
from src.feedback.packet_builder import build_evidence_packets
from src.feedback.section_family import (
    SECTION_FAMILIES,
    contains_negation,
    families_of,
    is_foreign,
    is_in_family,
    primary_families,
)

_DATA_DIR = pathlib.Path(__file__).parents[2] / "data" / "anonymized"


# ---------------------------------------------------------------------------
# Unit: families_of
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("heading_path,expected", [
    (["H1 Introductie", "H2 Stakeholders analyse"], {"stakeholders"}),
    # Ambiguous belang/concern sub-headings are deliberately REJECTED now —
    # they overlap requirements prioritisation. Documents that use them still
    # carry a real "Stakeholder analyse" heading that is admitted.
    (["Belangen en zorgen"], set()),
    (["Categoriseren en prioriteren"], set()),
    (["Wensen, Eisen, Prioriteiten", "Niet-functionele requirements"], {"requirements"}),
    # Use cases are no longer a requirements family. The parent doelgroep
    # heading still makes this block beperking-only.
    (["Onderzoek naar web gebruikers", "Use Case beschrijvingen"], {"beperking"}),
    (["H1 Introductie", "Security"], {"security"}),
    (["Securityelement: Data Encryptie"], {"security"}),
    (["AVG-naleving:"], {"security"}),
    (["Onderzoek naar web gebruikers"], {"beperking"}),
    (["H6 Literatuurlijst"], {"beperking"}),
    (["3.2 Webshopgebruiker met beperking"], {"beperking"}),
    (["Taalkeuze"], {"taalkeuze"}),
    (["H1 Introductie"], set()),          # neutral
    (["Inleiding"], set()),               # neutral
    ([], set()),                          # neutral
    (["H5 Ethische aandachtspunten"], set()),  # neutral (ethics is not a family)
])
def test_families_of(heading_path, expected):
    assert families_of(heading_path) == frozenset(expected)


def test_multifamily_membership_serves_all_relevant_criteria():
    # The combined doelgroep+requirements heading belongs to both — so it is
    # in-family (never foreign) for either criterion.
    fams = families_of(
        ["Stakeholder quadrant", "H3 Onderzoek naar web gebruikers", "Functionele requirements"]
    )
    assert fams == frozenset({"stakeholders", "beperking", "requirements"})
    for crit in ("stakeholders", "beperking", "requirements"):
        assert is_in_family(crit, fams)
        assert not is_foreign(crit, fams)
    # ...and foreign for the criteria it does not belong to.
    assert is_foreign("security", fams)
    assert is_foreign("taalkeuze", fams)


def test_every_criterion_has_a_family_pattern_set():
    assert set(SECTION_FAMILIES) == {
        "beperking", "stakeholders", "requirements", "taalkeuze", "security"
    }


@pytest.mark.parametrize("text", [
    "Verder heb ik geen bronnen gebruikt.",
    "Ik heb geen echt onderzoek gedaan naar webgebruikers.",
    "Dit profiel is niet gebaseerd op interviews of bronnen, maar klinkt logisch.",
    "NFR03 Taal is Nederlands/Engels. Geen keuze gemaakt.",
])
def test_contains_negation_true(text):
    assert contains_negation(text)


@pytest.mark.parametrize("text", [
    "Uit deskresearch onderzoek blijkt dat de doelgroep slechtziend is.",
    "De webshop wordt in het Nederlands aangeboden vanwege het bereik.",
])
def test_contains_negation_false(text):
    assert not contains_negation(text)


# ---------------------------------------------------------------------------
# Integration: section-first guarantees on the real corpus
# ---------------------------------------------------------------------------

_REAL_DOCS = [
    ("Goed", "8e5ee17e"), ("Goed", "c90afb25"),
    ("Voldoende", "1023e8a9"), ("Voldoende", "104812d2"), ("Voldoende", "2f4d9d91"),
    ("onvoldoende", "23276484"), ("onvoldoende", "35baf7d3"), ("onvoldoende", "f14254dd"),
]


def _load(subdir: str, doc_id: str) -> dict[str, Any]:
    raw = json.loads((_DATA_DIR / subdir / f"{doc_id}_anonymized.json").read_text("utf-8"))
    if "page_count" not in raw or raw["page_count"] is None:
        raw["page_count"] = max(b["page_no"] for b in raw["blocks"])
    return raw


def _packets(subdir: str, doc_id: str):
    artifacts = run_caps_with_artifacts(_load(subdir, doc_id), input_source="anonymized")
    return build_evidence_packets(artifacts)


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_no_foreign_blocks_admitted(subdir, doc_id):
    """Heading-only invariant: a block is admitted for a criterion ONLY when its
    DEEPEST heading segment belongs to that criterion (primary_families). There
    is no rescue path and no ancestor/stale-root carryover — so e.g. requirements
    tables under a stale "Stakeholder quadrant" root never reach stakeholders.

    taalkeuze is the single, deliberate exception: language choice has no
    dedicated heading in the corpus, so a block carrying an explicit
    language-CHOICE phrase may be admitted from any section — but only with an
    honest 'buiten een aparte taalsectie' context_warning."""
    packets = _packets(subdir, doc_id)
    for crit, pkt in packets.items():
        for it in pkt.evidence_items:
            prim = primary_families(it.heading_path)
            if is_in_family(crit, prim):
                continue
            assert crit == "taalkeuze" and "buiten een aparte taalsectie" in (
                it.context_warning or ""
            ), (
                f"{doc_id}/{crit}/{it.block_id}: admitted block whose deepest "
                f"section is {set(prim)}, not {crit} — gating violated"
            )


def test_known_contamination_blocks_are_gone():
    """The specific blocks that previously leaked across criteria must not appear
    in the wrong criterion anymore."""
    def block_ids(packets, crit):
        return {it.block_id for it in packets[crit].evidence_items}

    p_35 = _packets("onvoldoende", "35baf7d3")
    assert "35baf7d3_0033" not in block_ids(p_35, "taalkeuze")  # stakeholder table
    assert "35baf7d3_0033" not in block_ids(p_35, "security")

    p_8e = _packets("Goed", "8e5ee17e")
    assert "8e5ee17e_0084" not in block_ids(p_8e, "security")   # stakeholder table, "AVGregels"

    p_10 = _packets("Voldoende", "104812d2")
    sec = block_ids(p_10, "security")
    assert "104812d2_0054" not in sec  # doelgroep paragraph, "ssl" inside a word
    assert "104812d2_0076" not in sec

    p_10 = _packets("Voldoende", "1023e8a9")
    assert "1023e8a9_0028" not in block_ids(p_10, "taalkeuze")  # stakeholder table, consequence-only


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_negated_signals_never_positive(subdir, doc_id):
    """A block whose own text negates its evidence signal must not be surfaced as
    a positive item for beperking or taalkeuze."""
    packets = _packets(subdir, doc_id)
    for crit in ("beperking", "taalkeuze"):
        for it in packets[crit].evidence_items:
            if contains_negation(it.excerpt) and it.signal_class == "positive":
                # Allowed only when a genuine (non-negated) signal co-occurs is
                # out of scope here; the builder marks these weak — assert that.
                pytest.fail(
                    f"{doc_id}/{crit}/{it.block_id}: negated block surfaced as positive"
                )


def test_avg_does_not_match_inside_words():
    """Word-boundary matching: 'AVGregels' must not register as the 'avg' token,
    so a stakeholder table mentioning it is not rescued into security."""
    p = _packets("Goed", "8e5ee17e")
    for it in p["security"].evidence_items:
        assert it.block_id != "8e5ee17e_0084"


@pytest.mark.parametrize("subdir,doc_id", [
    ("Goed", "8e5ee17e"), ("Goed", "c90afb25"),
    ("Voldoende", "2f4d9d91"),
])
def test_requirements_coverage_is_representative(subdir, doc_id):
    """Requirements handoff preserves a broad section sample (many blocks, at
    least one structured subtype) on docs with a rich requirements section.
    Use cases are excluded, so the structured subtypes are FR / NFR / constraint."""
    packets = _packets(subdir, doc_id)
    items = packets["requirements"].evidence_items
    assert len(items) >= 5, f"{doc_id}: only {len(items)} requirements items"
    subtypes = {it.criterion_subtype for it in items if it.criterion_subtype}
    assert subtypes & {"functional", "non_functional", "constraint"}, (
        f"{doc_id}: no structured requirement subtype surfaced ({subtypes})"
    )
    # Use cases must never be counted as requirements anymore.
    assert "use_case" not in subtypes, f"{doc_id}: use_case leaked into requirements"


def test_requirements_multi_subtype_spread_when_available():
    """When a doc genuinely mixes FR / NFR / constraints, the sample shows it."""
    items = _packets("Voldoende", "2f4d9d91")["requirements"].evidence_items
    subtypes = {it.criterion_subtype for it in items if it.criterion_subtype}
    assert len(subtypes & {"functional", "non_functional", "constraint"}) >= 2, subtypes


@pytest.mark.parametrize("subdir,doc_id", _REAL_DOCS)
def test_excerpts_are_broader_than_a_fragment(subdir, doc_id):
    """Paragraph/bullet excerpts should carry coherent local context, not a tiny
    snippet — the median paragraph excerpt should be reasonably sized."""
    packets = _packets(subdir, doc_id)
    para_lens = [
        len(it.excerpt)
        for pkt in packets.values()
        for it in pkt.evidence_items
        if it.block_type in ("paragraph", "bullet") and it.signal_class != "absent_marker"
    ]
    if para_lens:
        para_lens.sort()
        median = para_lens[len(para_lens) // 2]
        # Lower bound is modest: strict heading-only selection can legitimately
        # pick a short, isolated in-section paragraph that has no same-heading
        # neighbours to widen into (e.g. a one-line requirements caption).
        assert median >= 40, f"{doc_id}: paragraph excerpts too small (median {median})"
