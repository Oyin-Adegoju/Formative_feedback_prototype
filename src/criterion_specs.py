"""criterion_specs.py — officieel rubric-contract voor INFIRFS requirements-deel.

Definitie van de 5 beoordelingscriteria: synoniemen, retrieval-hints,
drempelwaarden en scoringsmetadata.

Geen modelcalls. Geen PDF-parsing. Alleen metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

# ---------------------------------------------------------------------------
# Rubricstatussen + score-mapping
# ---------------------------------------------------------------------------

RubricStatus = Literal["missing", "partial", "sufficient", "strong"]

SCORE_MAP: Final[dict[str, int]] = {
    "missing": 0,
    "partial": 1,
    "sufficient": 2,
    "strong": 3,
}

# ---------------------------------------------------------------------------
# Datastructuren
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThresholdSpec:
    """Drempelwaarden voor teltbare criteria."""

    minimum: int        # >= minimum → "sufficient"
    strong_minimum: int  # >= strong_minimum → "strong"


@dataclass(frozen=True)
class CriterionSpec:
    """Metadata voor één beoordelingscriterium.

    Contract tussen criterion_specs.py, checks.py en caps.py.
    Geen logica: alleen wat er beoordeeld wordt en hoe het gevonden kan worden.
    """

    key: str                        # unieke sleutel, bijv. "stakeholders"
    display_name: str               # leesbare naam voor output/rapportage
    description: str                # wat wordt beoordeeld
    synonyms: tuple[str, ...]       # zoektermen voor tekst-matching
    heading_hints: tuple[str, ...]  # patronen om te zoeken in heading_path
    text_hints: tuple[str, ...]     # patronen om te zoeken in block.text
    threshold: ThresholdSpec | None  # None = geen harde teldrempel
    hard_check: str                 # naam van check-functie in checks.py
    is_blocker: bool                # True → ontbrekend criterium geeft rood


# ---------------------------------------------------------------------------
# De 5 criteria
# ---------------------------------------------------------------------------

SCOPE_AND_RESEARCH = CriterionSpec(
    key="scope_and_research",
    display_name="Beperking & deskresearch",
    description=(
        "Het document beschrijft een gekozen beperking van het project "
        "én onderbouwt die keuze op basis van deskresearch "
        "(literatuur, bestaande systemen of vergelijkbare oplossingen)."
    ),
    synonyms=(
        "beperking",
        "gekozen beperking",
        "deskresearch",
        "literatuuronderzoek",
        "onderbouwing",
        "bronnen",
        "verkenning",
    ),
    heading_hints=(
        "beperking",
        "deskresearch",
        "literatuur",
        "onderzoek",
        "onderbouwing",
        "bronnen",
        "verkenning",
    ),
    text_hints=(
        "beperking",
        "gekozen beperking",
        "deskresearch",
        "literatuuronderzoek",
        "onderbouwd",
        "bronnen",
        "niet in scope",
        "buiten scope",
    ),
    threshold=None,
    hard_check="check_scope_and_research",
    is_blocker=True,
)

STAKEHOLDERS = CriterionSpec(
    key="stakeholders",
    display_name="Stakeholders",
    description=(
        "Het document identificeert minstens 4 stakeholders, "
        "elk met een beschreven belang én invloed."
    ),
    synonyms=(
        "stakeholder",
        "belanghebbende",
        "betrokkene",
        "opdrachtgever",
        "gebruiker",
        "actor",
    ),
    heading_hints=(
        "stakeholder",
        "belanghebbende",
        "betrokkenen",
        "actoren",
        "gebruikers",
    ),
    text_hints=(
        "stakeholder",
        "belanghebbende",
        "belang",
        "invloed",
        "betrokken",
        "opdrachtgever",
        "eindgebruiker",
    ),
    threshold=ThresholdSpec(minimum=4, strong_minimum=6),
    hard_check="count_stakeholders",
    is_blocker=True,
)

REQUIREMENTS = CriterionSpec(
    key="requirements",
    display_name="Requirements",
    description=(
        "Het document bevat minstens 15 aantoonbare requirements "
        "(functioneel en/of niet-functioneel)."
    ),
    synonyms=(
        "requirement",
        "eis",
        "functionele eis",
        "niet-functionele eis",
        "FR",
        "NFR",
        "use case",
        "UC",
        "wens",
        "must have",
        "should have",
        "could have",
        "moscow",
        "constraints",
    ),
    heading_hints=(
        "requirement",
        "eis",
        "functionele",
        "niet-functionele",
        "moscow",
        "wensen",
        "eisen",
        "use case",
        "constraints",
    ),
    text_hints=(
        "requirement",
        "eis",
        "FR",
        "NFR",
        "use case",
        "UC",
        "moet",
        "must",
        "should",
        "could",
        "functioneel",
        "niet-functioneel",
        "systeem moet",
        "constraints",
    ),
    threshold=ThresholdSpec(minimum=15, strong_minimum=25),
    hard_check="count_requirements",
    is_blocker=True,
)

LANGUAGE_CHOICE = CriterionSpec(
    key="language_choice",
    display_name="Taalkeuze & consequenties",
    description=(
        "Het document beschrijft de taalkeuze van de webshop "
        "(bijv. Nederlands, Engels of meertalig), onderbouwt die keuze "
        "vanuit de opdracht en deskresearch, én benoemt de consequenties "
        "van die keuze."
    ),
    synonyms=(
        "taalkeuze",
        "taal",
        "meertalig",
        "Nederlands",
        "Engels",
        "internationaal",
        "lokalisatie",
        "consequentie",
        "vertaling",
    ),
    heading_hints=(
        "taalkeuze",
        "taal",
        "meertaligheid",
        "internationalisatie",
        "lokalisatie",
        "keuze",
    ),
    text_hints=(
        "taalkeuze",
        "taal",
        "meertalig",
        "Nederlands",
        "Engels",
        "internationaal",
        "lokalisatie",
        "consequentie",
        "vertaling",
        "taalversie",
    ),
    threshold=None,
    hard_check="check_language_choice",
    is_blocker=True,
)

SECURITY = CriterionSpec(
    key="security",
    display_name="Security",
    description=(
        "Het document bevat een sectie over beveiliging én beschrijft "
        "minstens 1 concreet security-element "
        "(bijv. authenticatie, autorisatie, encryptie, AVG/privacy, OWASP)."
    ),
    synonyms=(
        "security",
        "beveiliging",
        "veiligheid",
        "authenticatie",
        "autorisatie",
        "encryptie",
        "AVG",
        "privacy",
        "OWASP",
        "risicoanalyse",
        "https",
        "ssl",
        "tls",
        "wachtwoord",
        "toegangscontrole",
    ),
    heading_hints=(
        "security",
        "beveiliging",
        "veiligheid",
        "privacy",
        "AVG",
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
        "AVG",
        "privacy",
        "OWASP",
        "risicoanalyse",
        "https",
        "ssl",
        "tls",
        "wachtwoord",
        "toegangscontrole",
        "veilig",
    ),
    threshold=ThresholdSpec(minimum=1, strong_minimum=3),
    hard_check="check_security_elements",
    is_blocker=True,
)


# ---------------------------------------------------------------------------
# Registry — importeer dit in checks.py en caps.py
# ---------------------------------------------------------------------------

ALL_CRITERIA: Final[list[CriterionSpec]] = [
    SCOPE_AND_RESEARCH,
    STAKEHOLDERS,
    REQUIREMENTS,
    LANGUAGE_CHOICE,
    SECURITY,
]

CRITERIA_BY_KEY: Final[dict[str, CriterionSpec]] = {
    c.key: c for c in ALL_CRITERIA
}
