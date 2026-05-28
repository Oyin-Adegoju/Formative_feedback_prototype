"""criterion_specs.py â rubric contract for CAPS: INFIRFS requirements assignment.

Defines the 5 assessment criteria as pure metadata: keys, labels, descriptions,
retrieval hints, count thresholds, and guidance notes.

No model calls. No PDF parsing. No scoring logic. No counting. Metadata only.

Architecture position:
  parser output â [anonymizer] â CAPS retrieval â CAPS checks â CAPS scoring
  This file is consumed by retrieval, checks, and scoring â not by the parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Criterion spec dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionSpec:
    """Metadata for one rubric criterion.

    Consumed by retrieval, checks, and scoring layers.
    Contains no logic â only what to look for and what thresholds apply.

    Fields without defaults are always required.
    Fields with defaults are optional and criterion-specific.
    """

    key: str
    """Stable machine key â matches CriterionResult.criterion_key in models.py."""

    label: str
    """Human-readable display name for output and feedback reports."""

    description: str
    """Wat dit criterium beoordeelt, in heldere taal voor docent en student."""

    is_blocker: bool
    """True â een ontbrekend/gedeeltelijk verdict activeert een rood stoplicht op documentniveau."""

    relevant_block_types: frozenset[str]
    """Parser block_type values that may carry evidence for this criterion.

    Uses str rather than BlockType Literal to stay decoupled from parser internals.
    Anonymized output preserves block_type, so this stays valid after anonymization.
    """

    heading_hints: tuple[str, ...]
    """Lowercase substrings to match against each entry in block.heading_path.

    Broad by design â retrieval narrows by scored relevance, not hard matching.
    """

    text_hints: tuple[str, ...]
    """Lowercase substrings to match against block.text.

    These are content signals, not definitive indicators. Retrieval uses them
    to score blocks; check functions do the actual judgement.
    """

    minimum_count: int | None = None
    """Minimaal aantal items voor een 'voldoende' verdict. None = aanwezigheidscriterium."""

    strong_from: int | None = None
    """Minimaal aantal items voor een 'goed' verdict. None = niet van toepassing."""

    notes: str = ""
    """Toelichting voor check- en retrieval-lagen: subtiliteiten, co-voorkomensregels,
    veelvoorkomende patronen en aandachtspunten buiten sleutelwoordmatching."""

    manual_review_trigger: str = ""
    """Condities die dit criterium markeren voor handmatige verificatie.
    Lege string betekent geen automatische trigger buiten de normale logica."""

# ---------------------------------------------------------------------------
# Shared block-type set for content-bearing blocks
# ---------------------------------------------------------------------------

_CONTENT_BLOCKS: frozenset[str] = frozenset(
    {"heading", "paragraph", "bullet", "table"}
)
"""Substantive parser block types that carry document content.

Excludes: front_matter, noise, template, caption, appendix.
Captions and appendices are not primary retrieval sources,
but may serve as secondary or supporting evidence for check functions
and manual review â not excluded entirely.
"""
# ---------------------------------------------------------------------------
# Criterion 1: Beperking & deskresearch
# ---------------------------------------------------------------------------

BEPERKING = CriterionSpec(
    key="beperking",
    label="Beperking & deskresearch",
    description=(
        "Het document beschrijft een gekozen beperking â "
        "bijvoorbeeld een doelgroep met een beperking, een specifieke niche, "
        "of een expliciete afbakening â "
        "en onderbouwt die keuze met deskresearch: "
        "verwijzingen naar literatuur, bestaande systemen of vergelijkbare oplossingen."
    ),
    is_blocker=True,
    relevant_block_types=_CONTENT_BLOCKS,
    heading_hints=(
        "beperking",
        "afbakening",
        "deskresearch",
        "onderzoek",
        "doelgroep",
        "keuze",
        "inleiding",
        "bron",
        "literatuur",
        "verkenning",
    ),
    text_hints=(
        "beperking",
        "gekozen beperking",
        "deskresearch",
        "desk research",
        "afbakening",
        "doelgroep met beperking",
        "niet in scope",
        "buiten scope",
        "bron",
        "literatuuronderzoek",
        "onderbouwd",
        "onderzoek",
        "verkenning",
    ),
    minimum_count=None,
    strong_from=None,
    notes=(
        "Twee signalen moeten samen aanwezig zijn voor een voldoende verdict: "
        "(1) een expliciete gekozen beperking of doelgroepkeuze is beschreven â "
        "bijv. een doelgroep met beperking, een niche of een afbakening â "
        "en (2) minstens Ã©Ã©n onderbouwde bron of deskresearch-sectie is aanwezig "
        "(bronvermelding, literatuurverwijzing of deskresearch-sectie). "
        "Ontbreekt Ã©Ã©n van beide, dan is een gedeeltelijk verdict het maximale. "
        "De sectie 'Onderzoek naar web gebruikers' is de meest voorkomende locatie. "
        "Een Literatuurlijst-heading is ondersteunend bewijs voor signaal (2). "
        "Documenten kunnen de beperking impliciet beschrijven via een specifieke doelgroep "
        "zonder het woord 'beperking' zelf te gebruiken."
    ),
    manual_review_trigger=(
        "Activeer wanneer de beperking is beschreven maar het onderzoeksbewijs "
        "dun of onduidelijk is (bijv. Ã©Ã©n vage verwijzing, geen URL, geen genoemde bron)."
    ),
)
# ---------------------------------------------------------------------------
# Criterion 2: Stakeholders
# ---------------------------------------------------------------------------

STAKEHOLDERS = CriterionSpec(
    key="stakeholders",
    label="Stakeholders",
    description=(
        "Het document benoemt minimaal 4 stakeholders. "
        "Elke stakeholder heeft een beschreven belang en invloed op het project."
    ),
    is_blocker=True,
    relevant_block_types=_CONTENT_BLOCKS,
    heading_hints=(
        "stakeholder",
        "belanghebbende",
        "betrokkenen",
        "actoren",
        "gebruikers",
        "stakeholder analyse",
        "stakeholder quadrant",
        "categorisatie",
    ),
    text_hints=(
        "stakeholder",
        "belang",
        "invloed",
        "concern",
        "categorisatie",
        "matrix",
        "opdrachtgever",
        "eindgebruiker",
        "betrokken",
        "actor",
    ),
    minimum_count=4,
    strong_from=6,
    notes=(
        "Tel afzonderlijke benoemde stakeholderrollen, niet het aantal keer dat het woord voorkomt. "
        "Een stakeholdervermelding zonder zowel belang als invloed telt als gedeeltelijk bewijs. "
        "Stakeholdertabellen en kwadrantdiagrammen (block_type='table') zijn de primaire bron â "
        "controleer tabelcellen op rolnamen en bijbehorende belang/invloed-kolommen. "
        "minimum_count=4 voor voldoende; strong_from=6 voor goed."
    ),
    manual_review_trigger=(
        "Activeer wanneer het aantal stakeholders exact op de grenswaarde ligt (4 of 6), "
        "of wanneer belang/invloed-beschrijvingen aanwezig maar erg beknopt zijn."
    ),
)

# ---------------------------------------------------------------------------
# Criterion 3: Requirements
# ---------------------------------------------------------------------------

REQUIREMENTS = CriterionSpec(
    key="requirements",
    label="Requirements",
    description=(
        "Het document bevat minimaal 15 verifieerbare requirements "
        "(functioneel en/of niet-functioneel). "
        "Prioritering (bijv. MoSCoW) dient aanwezig te zijn."
    ),
    is_blocker=True,
    relevant_block_types=_CONTENT_BLOCKS,
    heading_hints=(
        "requirement",
        "eis",
        "wensen",
        "eisen",
        "prioriteiten",
        "functionele",
        "niet-functionele",
        "non-functional",
        "non functioneel",
        "moscow",
        "constraints",
        "use case",
    ),
    text_hints=(
        "requirement",
        "eis",
        "must have",
        "should have",
        "could have",
        "moscow",
        "FR",
        "NFR",
        "functionele",
        "niet-functioneel",
        "constraints",
        "moet",
        "wens",
        "use case",
        "prioriteit",
        "systeem moet",
    ),
    minimum_count=15,
    strong_from=25,
    notes=(
        "Requirements komen voor in meerdere vormen: "
        "genummerde lijsten (FR-01, NFR-01, USC-01), bullet points, tabelrijen "
        "of use-case-omschrijvingen. Tel individuele requirement-items, niet secties. "
        "MoSCoW-labels (must/should/could) in bullets of tabelcellen zijn sterke indicatoren. "
        "Use-case-omschrijvingen tellen als requirement als ze systeemgedrag beschrijven. "
        "Aanwezigheid van prioritering werkt als upgrade van gedeeltelijk naar voldoende bij grensgevallen. "
        "minimum_count=15 voor voldoende; strong_from=25 voor goed."
    ),
    manual_review_trigger=(
        "Activeer wanneer het aantal requirements tussen 12 en 17 ligt (Â±2 van de grenswaarde), "
        "of wanneer requirements verspreid staan over secties zonder duidelijke structuur."
    ),
)

# ---------------------------------------------------------------------------
# Criterion 4: Taalkeuze
# ---------------------------------------------------------------------------

TAALKEUZE = CriterionSpec(
    key="taalkeuze",
    label="Taalkeuze & consequenties",
    description=(
        "Het document vermeldt expliciet de taalkeuze voor de webshop "
        "(bijv. Nederlands, Engels of meertalig), "
        "en benoemt de gevolgen van die keuze "
        "(bijv. bereik, vertaalkosten, toegankelijkheid, aansluiting bij de doelgroep)."
    ),
    is_blocker=True,
    relevant_block_types=_CONTENT_BLOCKS,
    heading_hints=(
        "taalkeuze",
        "taal",
        "meertaligheid",
        "internationalisatie",
        "lokalisatie",
        "keuze",
        "taalversie",
    ),
    text_hints=(
        "taalkeuze",
        "taal",
        "meertalig",
        "nederlands",
        "engels",
        "internationaal",
        "lokalisatie",
        "consequentie",
        "vertaling",
        "begrijpelijk",
        "taalversie",
    ),
    minimum_count=None,
    strong_from=None,
    notes=(
        "Twee signalen moeten samen aanwezig zijn voor een voldoende verdict: "
        "(1) een expliciete taalkeuze is vermeld (bijv. 'de webshop wordt in het Nederlands'), "
        "en (2) de gevolgen van die keuze zijn besproken "
        "(bijv. effect op bereik, vertaalkosten, toegankelijkheid of begrijpelijkheid). "
        "Dit criterium is regelmatig afwezig of slechts gedeeltelijk uitgewerkt. "
        "Een aparte heading wordt niet verwacht â inhoud staat doorgaans in "
        "inleidende secties, afbakening of niet-functionele requirements. "
        "Onderbouwing met een bron of deskresearch-verwijzing verhoogt naar goed."
    ),
    manual_review_trigger=(
        "Activeer wanneer een taal is vermeld maar gevolgen ontbreken of triviaal zijn. "
        "Activeer wanneer 'meertalig' staat maar de specifieke talen niet zijn benoemd."
    ),
)


# ---------------------------------------------------------------------------
# Criterion 5: Security
# ---------------------------------------------------------------------------

SECURITY = CriterionSpec(
    key="security",
    label="Security",
    description=(
        "Het document bevat een sectie over security en beschrijft "
        "minimaal 1 concreet beveiligingselement "
        "(bijv. authenticatie, autorisatie, encryptie, AVG/privacy, OWASP)."
    ),
    is_blocker=True,
    relevant_block_types=_CONTENT_BLOCKS,
    heading_hints=(
        "security",
        "beveiliging",
        "veiligheid",
        "privacy",
        "avg",
        "non-functional",
        "niet-functioneel",
        "risico",
    ),
    text_hints=(
        "security",
        "beveiliging",
        "authenticatie",
        "autorisatie",
        "encryptie",
        "avg",
        "privacy",
        "owasp",
        "risicoanalyse",
        "https",
        "ssl",
        "tls",
        "wachtwoord",
        "toegangscontrole",
        "veilig",
    ),
    minimum_count=1,
    strong_from=3,
    notes=(
        "Een heading 'Security' of 'Beveiliging' alleen is onvoldoende â "
        "minimaal 1 concreet mechanisme moet worden benoemd in inhoudsblokken eronder. "
        "Tel afzonderlijke beveiligingsmechanismen, niet het aantal keer dat een sleutelwoord voorkomt. "
        "Security kan voorkomen binnen een niet-functionele requirements-sectie "
        "in plaats van als aparte heading. "
        "minimum_count=1 voor voldoende; strong_from=3 voor goed."
    ),
    manual_review_trigger=(
        "Activeer wanneer een security-sectie aanwezig is maar alle inhoud generiek blijft "
        "(bijv. 'de webshop moet veilig zijn') zonder een concreet mechanisme te noemen."
    ),
)


# ---------------------------------------------------------------------------
# Registry â ordered, stable
# ---------------------------------------------------------------------------

INFIRFS_REQUIREMENTS_CRITERIA: Final[tuple[CriterionSpec, ...]] = (
    BEPERKING,
    STAKEHOLDERS,
    REQUIREMENTS,
    TAALKEUZE,
    SECURITY,
)
"""Ordered tuple of all 5 CAPS criteria for the INFIRFS requirements assignment.

Order matches the typical document reading order and the logical dependency order
for feedback generation. Stable across runs â do not reorder.
"""


# ---------------------------------------------------------------------------
# Convenience lookups
# ---------------------------------------------------------------------------

CRITERIA_BY_KEY: Final[dict[str, CriterionSpec]] = {
    c.key: c for c in INFIRFS_REQUIREMENTS_CRITERIA
}
"""Key â CriterionSpec mapping. Keys match CriterionResult.criterion_key in models.py."""

CRITERIA_KEYS: Final[tuple[str, ...]] = tuple(
    c.key for c in INFIRFS_REQUIREMENTS_CRITERIA
)
"""Ordered criterion keys. Iteration order is guaranteed stable."""

BLOCKER_KEYS: Final[frozenset[str]] = frozenset(
    c.key for c in INFIRFS_REQUIREMENTS_CRITERIA if c.is_blocker
)
"""Keys of all blocker criteria â O(1) membership test."""

COUNTABLE_KEYS: Final[frozenset[str]] = frozenset(
    c.key for c in INFIRFS_REQUIREMENTS_CRITERIA if c.minimum_count is not None
)
"""Keys of criteria that use a numeric count threshold (stakeholders, requirements, security)."""

