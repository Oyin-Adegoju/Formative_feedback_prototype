"""Unit tests for src/caps/checks.py.

Tests cover all five deterministic criterion check functions.
No scoring, retrieval, model calls, or file I/O.
Each test number corresponds to the task specification (1–30).
"""
from __future__ import annotations

import pytest

from src.caps.checks import (
    check_beperking,
    check_requirements,
    check_security,
    check_stakeholders,
    check_taalkeuze,
)
from src.caps.criterion_specs import CRITERIA_BY_KEY
from src.caps.models import BlockDict, TableMetaDict
from src.caps.retrieval import RetrievalHit


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def make_block(
    text: str,
    block_type: str = "paragraph",
    *,
    block_id: str = "b001",
    page_no: int = 1,
    heading_path: list[str] | None = None,
    is_front_matter: bool = False,
    is_appendix: bool = False,
    table_meta: TableMetaDict | None = None,
) -> BlockDict:
    return {
        "block_id": block_id,
        "page_no": page_no,
        "block_type": block_type,
        "text": text,
        "heading_path": heading_path or [],
        "is_front_matter": is_front_matter,
        "is_appendix": is_appendix,
        "table_meta": table_meta,
    }


def make_table_block(
    cells: list[list[str | None]],
    *,
    block_id: str = "b001",
    page_no: int = 1,
    heading_path: list[str] | None = None,
    header_row: str | None = None,
) -> BlockDict:
    text = "\n".join(" ".join(c for c in row if c) for row in cells)
    tm: TableMetaDict = {
        "row_count": len(cells),
        "column_count": max((len(r) for r in cells), default=0),
        "header_row": header_row,
        "cells": cells,
        "index_cells": [],
    }
    return make_block(
        text=text,
        block_type="table",
        block_id=block_id,
        page_no=page_no,
        heading_path=heading_path or [],
        table_meta=tm,
    )


def make_hit(block: BlockDict, score: float = 1.0) -> RetrievalHit:
    return RetrievalHit(block=block, score=score)


# ---------------------------------------------------------------------------
# Shared specs
# ---------------------------------------------------------------------------

_SPEC_BEP = CRITERIA_BY_KEY["beperking"]
_SPEC_STK = CRITERIA_BY_KEY["stakeholders"]
_SPEC_REQ = CRITERIA_BY_KEY["requirements"]
_SPEC_TAL = CRITERIA_BY_KEY["taalkeuze"]
_SPEC_SEC = CRITERIA_BY_KEY["security"]


# ===========================================================================
# Criterion: beperking
# ===========================================================================


# 1 — missing when neither limitation nor research signal is present
def test_beperking_missing_no_signals():
    hits = [make_hit(make_block("De webshop verkoopt sportartikelen online.", block_id="b1"))]
    result = check_beperking(_SPEC_BEP, hits)
    assert result.status == "missing"
    assert result.criterion_key == "beperking"
    assert not result.manual_review


# 2 — partial when only limitation is present (no research)
def test_beperking_partial_only_limitation():
    hits = [
        make_hit(make_block(
            "Wij richten ons op de doelgroep met visuele beperking.",
            block_id="b1",
        ))
    ]
    result = check_beperking(_SPEC_BEP, hits)
    assert result.status == "partial"
    assert result.evidence


# 3 — partial when only research is present (no limitation)
def test_beperking_partial_only_research():
    hits = [
        make_hit(make_block(
            "Deskresearch toonde aan dat er meerdere vergelijkbare systemen bestaan.",
            block_id="b1",
        ))
    ]
    result = check_beperking(_SPEC_BEP, hits)
    assert result.status == "partial"
    assert result.evidence


# 4 — sufficient when both limitation and research are present
def test_beperking_sufficient_both_signals():
    lim = make_hit(make_block(
        "Wij richten ons op de doelgroep met visuele beperking.",
        block_id="b1",
    ))
    # Substantive body research hit: paragraph, >= 8 words
    res = make_hit(make_block(
        "Deskresearch toonde aan dat gebruikers met een beperking specifieke eisen stellen.",
        block_id="b2",
    ))
    result = check_beperking(_SPEC_BEP, [lim, res])
    assert result.status == "sufficient"
    assert result.evidence


# 5 — strong when both are present across multiple substantive hits
def test_beperking_strong_multiple_substantive_hits():
    lim1 = make_hit(make_block(
        "De gekozen doelgroep heeft een auditieve beperking.",
        block_id="b1",
    ))
    lim2 = make_hit(make_block(
        "De gekozen doelgroep bestaat uit mensen met een cognitieve beperking.",
        block_id="b2",
    ))
    # Both research hits: paragraph type, >= 8 words, no negation
    res1 = make_hit(make_block(
        "Deskresearch toonde aan dat er bestaande hulpmiddelen zijn voor slechthorenden.",
        block_id="b3",
    ))
    res2 = make_hit(make_block(
        "Literatuuronderzoek wijst op het belang van toegankelijke ondertiteling voor deze doelgroep.",
        block_id="b4",
    ))
    result = check_beperking(_SPEC_BEP, [lim1, lim2, res1, res2])
    assert result.status == "strong"
    assert result.criterion_key == "beperking"
    assert not result.manual_review
    assert result.evidence


# 6 — manual_review for thin research evidence (heading-only style)
def test_beperking_manual_review_heading_only_research():
    lim = make_hit(make_block(
        "Wij richten ons op de doelgroep met visuele beperking.",
        block_id="b1",
    ))
    # Heading-only research signal: filtered from research_body_hits
    res_heading = make_hit(make_block(
        "Deskresearch en bronvermelding",
        block_type="heading",
        block_id="b2",
    ))
    result = check_beperking(_SPEC_BEP, [lim, res_heading])
    assert result.status == "sufficient"
    assert result.manual_review


# ===========================================================================
# Criterion: stakeholders
# ===========================================================================


# 7 — missing when no stakeholders are found
def test_stakeholders_missing_no_roles():
    hits = [
        make_hit(make_block(
            "Dit document beschrijft de projectaanpak en planning.",
            block_id="b1",
        ))
    ]
    result = check_stakeholders(_SPEC_STK, hits)
    assert result.status == "missing"
    assert result.criterion_key == "stakeholders"
    assert result.count == 0


# 8 — partial when count is below minimum (4)
def test_stakeholders_partial_count_below_minimum():
    table = make_table_block(
        cells=[
            ["Stakeholder", "Belang", "Invloed"],
            ["Opdrachtgever", "Hoog", "Hoog"],
            ["Eindgebruiker", "Middel", "Hoog"],
        ],
        block_id="b1",
    )
    result = check_stakeholders(_SPEC_STK, [make_hit(table)])
    assert result.status == "partial"
    assert result.count == 2


# 9 — sufficient when minimum count is met and belang + invloed are present
def test_stakeholders_sufficient_minimum_with_bi():
    # 5 roles → count=5: above minimum(4), below strong_from(6), no manual_review edge
    table = make_table_block(
        cells=[
            ["Stakeholder", "Belang", "Invloed"],
            ["Opdrachtgever", "Hoog", "Hoog"],
            ["Eindgebruiker", "Middel", "Hoog"],
            ["Projectmanager", "Hoog", "Middel"],
            ["Developer", "Laag", "Middel"],
            ["Klant", "Middel", "Laag"],
        ],
        block_id="b1",
    )
    result = check_stakeholders(_SPEC_STK, [make_hit(table)])
    assert result.status == "sufficient"
    assert result.count == 5
    assert result.criterion_key == "stakeholders"
    assert not result.manual_review


# 10 — strong when strong threshold is met with belang + invloed
def test_stakeholders_strong_above_threshold_with_bi():
    # 7 roles → count=7 > strong_from(6)
    table = make_table_block(
        cells=[
            ["Stakeholder", "Belang", "Invloed"],
            ["Opdrachtgever", "Hoog", "Hoog"],
            ["Eindgebruiker", "Middel", "Hoog"],
            ["Projectmanager", "Hoog", "Middel"],
            ["Developer", "Laag", "Middel"],
            ["Klant", "Middel", "Laag"],
            ["Investeerder", "Hoog", "Laag"],
            ["Beheerder", "Laag", "Hoog"],
        ],
        block_id="b1",
    )
    result = check_stakeholders(_SPEC_STK, [make_hit(table)])
    assert result.status == "strong"
    assert result.count == 7
    assert not result.manual_review


# 11 — table-first extraction is preferred over keyword fallback
def test_stakeholders_table_roles_preferred_over_keyword_fallback():
    # Table supplies 2 roles; keyword block mentions many role words.
    # The check must use table roles (count=2), ignoring keyword inflation.
    table = make_table_block(
        cells=[
            ["Opdrachtgever", "Hoog", "Hoog"],
            ["Eindgebruiker", "Middel", "Hoog"],
        ],
        block_id="b1",
    )
    # Keyword block mentions opdrachtgever/klant/eindgebruiker/manager/directeur/developer
    kw_block = make_block(
        "De opdrachtgever, klant, eindgebruiker, developer, manager en directeur zijn betrokken.",
        block_type="paragraph",
        block_id="b2",
    )
    result = check_stakeholders(_SPEC_STK, [make_hit(table), make_hit(kw_block)])
    # Table roles win → count=2, not the inflated keyword count
    assert result.count == 2
    assert result.status == "partial"


# 12 — manual_review at threshold edge cases
def test_stakeholders_manual_review_at_exact_minimum():
    # count=4 (exactly minimum) with bi → sufficient but manual_review=True
    table = make_table_block(
        cells=[
            ["Stakeholder", "Belang", "Invloed"],
            ["Opdrachtgever", "Hoog", "Hoog"],
            ["Eindgebruiker", "Middel", "Hoog"],
            ["Projectmanager", "Hoog", "Middel"],
            ["Developer", "Laag", "Middel"],
        ],
        block_id="b1",
    )
    result = check_stakeholders(_SPEC_STK, [make_hit(table)])
    assert result.status == "sufficient"
    assert result.count == 4
    assert result.manual_review


def test_stakeholders_manual_review_at_exact_strong_threshold():
    # count=6 (exactly strong_from) with bi → strong but manual_review=True
    table = make_table_block(
        cells=[
            ["Stakeholder", "Belang", "Invloed"],
            ["Opdrachtgever", "Hoog", "Hoog"],
            ["Eindgebruiker", "Middel", "Hoog"],
            ["Projectmanager", "Hoog", "Middel"],
            ["Developer", "Laag", "Middel"],
            ["Klant", "Middel", "Laag"],
            ["Investeerder", "Hoog", "Laag"],
        ],
        block_id="b1",
    )
    result = check_stakeholders(_SPEC_STK, [make_hit(table)])
    assert result.status == "strong"
    assert result.count == 6
    assert result.manual_review


# ===========================================================================
# Criterion: requirements
# ===========================================================================


# 13 — missing when no requirement evidence is found
def test_requirements_missing_no_evidence():
    hits = [make_hit(make_block(
        "Dit document geeft een overzicht van het project en de planning.",
        block_id="b1",
    ))]
    result = check_requirements(_SPEC_REQ, hits)
    assert result.status == "missing"
    assert result.criterion_key == "requirements"
    assert result.count == 0


# 14 — partial when requirement count is below threshold (minimum=15)
def test_requirements_partial_count_below_minimum():
    # 5 explicit req IDs → total=5 < 15
    text = "FR-01: login. FR-02: registratie. NFR-01: performance. UC-01: bestelling. USC-01: profiel."
    hits = [make_hit(make_block(text, block_id="b1"))]
    result = check_requirements(_SPEC_REQ, hits)
    assert result.status == "partial"
    assert result.count == 5


# 15 — sufficient when requirement count reaches minimum (15)
def test_requirements_sufficient_at_minimum():
    # 20 explicit req IDs: above minimum(15), below strong_from(25), no manual_review edge
    text = " ".join(f"FR-{i:02d}: de website biedt functie {i}." for i in range(1, 21))
    hits = [make_hit(make_block(text, block_id="b1"))]
    result = check_requirements(_SPEC_REQ, hits)
    assert result.status == "sufficient"
    assert result.count == 20


# 16 — strong when requirement count reaches strong threshold (25)
def test_requirements_strong_at_strong_threshold():
    # 25 explicit req IDs → total=25 >= strong_from(25)
    text = " ".join(f"FR-{i:02d}: de website biedt functie {i}." for i in range(1, 26))
    hits = [make_hit(make_block(text, block_id="b1"))]
    result = check_requirements(_SPEC_REQ, hits)
    assert result.status == "strong"
    assert result.count == 25


# 17 — explicit requirement IDs are counted correctly
def test_requirements_explicit_ids_counted():
    text = "FR-01: inlogfunctie. FR-02: registratie. NFR-01: laadtijd. UC-01: bestelling plaatsen."
    hits = [make_hit(make_block(text, block_id="b1"))]
    result = check_requirements(_SPEC_REQ, hits)
    assert result.count == 4


# 18 — numbered bullet fallback works
def test_requirements_numbered_bullet_fallback():
    # No req IDs, no MoSCoW — only numbered bullets
    text = (
        "1. De website moet de gebruiker authenticeren.\n"
        "2. De website moet productoverzichten kunnen tonen.\n"
        "3. De gebruiker moet een account kunnen aanmaken.\n"
        "4. De admin moet bestellingen kunnen beheren.\n"
        "5. Het systeem moet HTTPS gebruiken voor alle verbindingen."
    )
    hits = [make_hit(make_block(text, block_id="b1"))]
    result = check_requirements(_SPEC_REQ, hits)
    assert result.count == 5


# 19 — narrative requirement fallback works conservatively
def test_requirements_narrative_fallback_conservative():
    # No IDs, no numbered bullets, no MoSCoW → narrative sentences counted
    text = (
        "De website moet de gebruiker authenticeren via een loginformulier.\n"
        "Het systeem moet persoonlijke gegevens beveiligen conform AVG-richtlijnen.\n"
        "De applicatie moet beschikbaar zijn op alle gangbare mobiele apparaten.\n"
        "De gebruiker moet kunnen zoeken naar producten via een zoekfunctie."
    )
    hits = [make_hit(make_block(text, block_id="b1"))]
    result = check_requirements(_SPEC_REQ, hits)
    # Narrative match gives a conservative estimate below the minimum
    assert 1 <= result.count < 15
    assert result.status == "partial"


# 20 — use case supplementation only helps at very low primary count
def test_requirements_use_case_added_only_at_low_primary_count():
    # primary: 2 req IDs (total=2 < 5 → use case addition applies)
    primary = make_hit(make_block("FR-01: login. FR-02: registratie.", block_id="b1"))
    uc = make_hit(make_block(
        "In dit use case beschrijven gebruikers hoe zij producten toevoegen aan hun winkelwagen.",
        block_type="paragraph",
        block_id="b2",
    ))
    result_low = check_requirements(_SPEC_REQ, [primary, uc])
    assert result_low.count > 2  # use case block was supplemented

    # primary: 6 req IDs (total=6 >= 5 → use case addition skipped)
    primary_high = make_hit(make_block(
        " ".join(f"FR-{i:02d}: functie" for i in range(1, 7)),
        block_id="b3",
    ))
    uc2 = make_hit(make_block(
        "In dit use case beschrijven gebruikers hoe zij producten toevoegen aan hun winkelwagen.",
        block_type="paragraph",
        block_id="b4",
    ))
    result_high = check_requirements(_SPEC_REQ, [primary_high, uc2])
    assert result_high.count == 6  # use case block was NOT added


# ===========================================================================
# Criterion: taalkeuze
# ===========================================================================


# 21 — missing when neither language nor consequences are present
def test_taalkeuze_missing_no_signals():
    hits = [make_hit(make_block(
        "De webshop richt zich op een breed publiek van sportliefhebbers.",
        block_id="b1",
    ))]
    result = check_taalkeuze(_SPEC_TAL, hits)
    assert result.status == "missing"
    assert result.criterion_key == "taalkeuze"
    assert not result.manual_review


# 22 — partial when only language is present
def test_taalkeuze_partial_only_language():
    hits = [make_hit(make_block(
        "De webshop wordt in het Nederlands aangeboden.",
        block_id="b1",
    ))]
    result = check_taalkeuze(_SPEC_TAL, hits)
    assert result.status == "partial"
    assert result.manual_review
    assert result.evidence


# 23 — partial when only consequence is present
def test_taalkeuze_partial_only_consequence():
    hits = [make_hit(make_block(
        "Dit vergroot het bereik en verbetert de begrijpelijkheid voor alle bezoekers.",
        block_id="b1",
    ))]
    result = check_taalkeuze(_SPEC_TAL, hits)
    assert result.status == "partial"
    assert result.manual_review
    assert result.evidence


# 24 — sufficient when both are present
def test_taalkeuze_sufficient_both_signals():
    hits = [make_hit(make_block(
        "De webshop wordt in het Nederlands aangeboden. Dit vergroot het bereik van de doelgroep.",
        block_id="b1",
    ))]
    result = check_taalkeuze(_SPEC_TAL, hits)
    assert result.status == "sufficient"
    assert result.criterion_key == "taalkeuze"
    assert result.evidence


# 25 — strong when both present and either research support or multiple consequences
def test_taalkeuze_strong_with_research():
    hits = [make_hit(make_block(
        "De webshop wordt in het Nederlands aangeboden. "
        "Deskresearch toonde aan dat dit het bereik vergroot.",
        block_id="b1",
    ))]
    result = check_taalkeuze(_SPEC_TAL, hits)
    assert result.status == "strong"


def test_taalkeuze_strong_with_multiple_consequences():
    # >= 3 consequence signals → strong without research
    hits = [make_hit(make_block(
        "De webshop wordt in het Nederlands aangeboden. "
        "Dit vergroot het bereik, verlaagt vertaalkosten en bevordert lokalisatie.",
        block_id="b1",
    ))]
    result = check_taalkeuze(_SPEC_TAL, hits)
    assert result.status == "strong"


# ===========================================================================
# Criterion: security
# ===========================================================================


# 26 — missing when no security signals exist
def test_security_missing_no_signals():
    hits = [make_hit(make_block(
        "De webshop biedt een ruim assortiment aan sportartikelen.",
        block_id="b1",
    ))]
    result = check_security(_SPEC_SEC, hits)
    assert result.status == "missing"
    assert result.criterion_key == "security"
    assert result.count == 0


# 27 — partial when only generic security language exists
def test_security_partial_generic_only():
    hits = [make_hit(make_block(
        "De webshop moet veilig zijn. Beveiliging is een belangrijk aandachtspunt.",
        block_id="b1",
    ))]
    result = check_security(_SPEC_SEC, hits)
    assert result.status == "partial"
    assert result.count == 0
    assert result.manual_review
    assert result.evidence


# 28 — sufficient when at least one concrete mechanism exists
def test_security_sufficient_one_concrete_mechanism():
    hits = [make_hit(make_block(
        "De webshop maakt gebruik van authenticatie zodat gebruikers veilig kunnen inloggen.",
        block_id="b1",
    ))]
    result = check_security(_SPEC_SEC, hits)
    assert result.status == "sufficient"
    assert result.count == 1
    assert result.evidence


# 29 — strong when multiple concrete mechanisms exist
def test_security_strong_multiple_concrete_mechanisms():
    hits = [make_hit(make_block(
        "De webshop past authenticatie, encryptie via TLS en voldoet aan de AVG.",
        block_id="b1",
    ))]
    result = check_security(_SPEC_SEC, hits)
    assert result.status == "strong"
    assert result.count >= 3


# 30 — manual_review on generic-only or exact-minimum edge cases
def test_security_manual_review_generic_only():
    # generic-only → partial + manual_review
    hits = [make_hit(make_block(
        "Privacy en veiligheid zijn geborgd.",
        block_id="b1",
    ))]
    result = check_security(_SPEC_SEC, hits)
    assert result.status == "partial"
    assert result.manual_review


def test_security_manual_review_exactly_one_concrete_mechanism():
    # count == minimum (1) → sufficient + manual_review
    hits = [make_hit(make_block(
        "De webshop maakt gebruik van authenticatie voor veilig inloggen.",
        block_id="b1",
    ))]
    result = check_security(_SPEC_SEC, hits)
    assert result.status == "sufficient"
    assert result.count == 1
    assert result.manual_review
