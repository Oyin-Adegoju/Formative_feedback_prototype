"""checks.py — CAPS criterion-level deterministic check functions.

Converts retrieval hits (RetrievalHit objects from retrieval.py) into
per-criterion CriterionResult objects (models.py).

No scoring, no blocker aggregation, no document-level stoplight,
no feedback generation, no model calls, no parser or anonymizer changes.

Architecture position:
    parser output → [anonymizer] → CAPS retrieval → CAPS checks → CAPS scoring

All check functions are deterministic: same hits → same result.
Checks operate only on the RetrievalHit objects passed in.
They do not read any file, call any model, or inspect any global state.

Future anonymized input: because anonymized blocks preserve block_id,
page_no, block_type, heading_path, text, and table_meta structure (only
text content changes), these checks require no redesign for that path.
"""

from __future__ import annotations

import re
from typing import Final

from src.caps.criterion_specs import CRITERIA_BY_KEY, CriterionSpec
from src.caps.models import CriterionResult, CriterionStatus, EvidenceRef, StoplightLabel
from src.caps.retrieval import RetrievalHit


# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------

# Signals that a chosen limitation / doelgroep has been described.
# Broad but reasonable for this assignment context.
_LIMITATION_TERMS: Final[frozenset[str]] = frozenset({
    "beperking", "slechtziend", "visuele beperking", "visueel beperkt",
    "blind", "doof", "dyslexie", "motorische beperking", "cognitieve beperking",
    "auditieve beperking", "afbakening", "doelgroep met beperking",
    "niet in scope", "buiten scope", "richt me op", "focus op",
    "gekozen doelgroep", "specifieke doelgroep", "niche",
})

# Signals that deskresearch / literature support is present.
_RESEARCH_TERMS: Final[frozenset[str]] = frozenset({
    "deskresearch", "desk research", "literatuuronderzoek", "literatuurlijst",
    "literatuur", "bronvermelding", "bronnenlijst", "bronnen", "bron",
    "referentie", "onderzoek", "wetenschappelijk", "studie", "publicatie",
    "verkenning", "marktonderzoek", "user research",
})

# Phrases that explicitly negate a research signal within the same block.
# Only matched at block level — cannot detect cross-block negation.
_NEGATION_RESEARCH: Final[frozenset[str]] = frozenset({
    "geen bronnen", "geen onderzoek", "geen deskresearch",
    "niet onderzocht", "niet gebaseerd op bronnen",
    "niet gebaseerd op interviews",
    "geen literatuur", "geen bronnenlijst",
    "geen echt onderzoek", "geen echte bronnen",
    "geen bronvermelding", "klinkt logisch",
})

# Column-header keywords that indicate a stakeholder table.
# Used to skip header rows in table cell extraction.
_STAKEHOLDER_HEADER_KW: Final[frozenset[str]] = frozenset({
    "stakeholder", "naam", "rol", "functie", "belang", "invloed",
    "prioritering", "concern", "actor", "betrokkene", "beschrijving",
})

# Role-like keywords for supplementary paragraph/bullet scanning.
_STAKEHOLDER_ROLE_KW: Final[frozenset[str]] = frozenset({
    "opdrachtgever", "klant", "eindgebruiker", "gebruiker",
    "ontwikkelaar", "developer", "projectmanager", "manager",
    "directeur", "ceo", "cto", "designer", "ux designer",
    "admin", "beheerder", "eigenaar", "leverancier",
    "docent", "begeleider", "coach", "student", "medewerker",
    "investeerder", "aandeelhouder", "afdelingshoofd",
})

# Concrete security mechanisms — not just generic security language.
_CONCRETE_SECURITY: Final[frozenset[str]] = frozenset({
    "authenticatie", "autorisatie", "encryptie", "avg", "gdpr",
    "owasp", "https", "ssl", "tls", "wachtwoord", "toegangscontrole",
    "risicoanalyse", "two-factor", "2fa", "jwt", "xss", "csrf",
    "sql injection", "penetratietest", "beveiligingsaudit", "firewall",
    "hashing", "bcrypt", "privacyverklaring",
})

# Generic security language — necessary but not sufficient for "sufficient".
_GENERIC_SECURITY: Final[frozenset[str]] = frozenset({
    "security", "beveiliging", "veiligheid", "veilig", "privacy",
})

# Explicit language choice terms (for taalkeuze check).
_LANGUAGE_TERMS: Final[frozenset[str]] = frozenset({
    "taalkeuze", "taalversie", "meertalig",
    "in het nederlands", "in het engels", "in de nederlandse",
    "taal van de website", "webshop in het",
    "nederlands", "engels", "french", "duits",
})

# Consequence / effect terms that follow a language choice (for taalkeuze check).
# These should appear in language-related context (already filtered by retrieval).
_CONSEQUENCE_TERMS: Final[frozenset[str]] = frozenset({
    "bereik", "vertaalkosten", "lokalisatie", "internationalisatie",
    "internationaal", "consequentie", "beïnvloed", "beïnvloeden",
    "begrijpelijk", "begrijpelijkheid", "taalbarrière", "taalbarrier",
    "vertaling", "vertalen", "meertaligheid", "doelgroepbereik",
    "taalgebied", "gebruikersvriendelijkheid",
})

# Compound MoSCoW priority labels — the most reliable requirement-count signal.
# Bare "Must" / "Should" / "Could" are intentionally excluded (too noisy).
_MOSCOW_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(must[\s\-]?have|should[\s\-]?have|could[\s\-]?have|won'?t[\s\-]?have)\b",
    re.IGNORECASE,
)

# Requirement ID patterns: FR-01, NFR-01, UC-01, USC-01, REQ-01, CONSTR-01, etc.
_REQ_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(FR|NFR|USC|UC|US|REQ|CONSTR|CON|F|NF)[-\s]?\d{1,3}\b",
    re.IGNORECASE,
)

# Narrative requirement sentence detection — used only as a last-resort fallback
# in non-table blocks that have neither explicit IDs nor numbered bullet lines.
_NARRATIVE_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(website|systeem|webshop|applicatie|gebruiker|admin|beheerder"
    r"|klant|platform|pagina|server|database)\b",
    re.IGNORECASE,
)
_NARRATIVE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(moet|moeten|dient|dienen|zal|mag|kan|should|must)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared internal helpers
# ---------------------------------------------------------------------------


def _make_ref(hit: RetrievalHit) -> EvidenceRef:
    """Create a lightweight EvidenceRef from one retrieval hit."""
    b = hit.block
    return EvidenceRef(
        block_id=b["block_id"],
        page_no=b["page_no"],
        block_type=b["block_type"],
        text_snippet=b["text"][:120],
    )


def _norm(text: str) -> str:
    return text.lower().strip()


def _text_of(hit: RetrievalHit) -> str:
    return _norm(hit.block["text"])


def _all_text(hits: list[RetrievalHit]) -> str:
    return " ".join(_text_of(h) for h in hits)


def _found_terms(terms: frozenset[str], text: str) -> list[str]:
    """Return all terms from the set that appear in text (already normalized)."""
    return [t for t in terms if t in text]


def _status_to_stoplight(status: CriterionStatus) -> StoplightLabel:
    if status == "strong":
        return "green"
    if status == "sufficient":
        return "yellow"
    return "red"  # missing or partial


def _iter_cells(hit: RetrievalHit):
    """Yield all non-None cell strings from a table block's table_meta."""
    tm = hit.block.get("table_meta")
    if not tm:
        return
    for row in tm.get("cells") or []:
        for cell in row:
            if cell:
                yield cell
