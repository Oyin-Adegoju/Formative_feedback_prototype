"""packet_builder.py — builds per-criterion evidence packets from CAPS artifacts.

Takes a CapsPipelineArtifacts object (retrieval candidates + per-criterion
results + final run result) and produces one EvidencePacket per criterion.

Architecture position:
    CapsPipelineArtifacts → build_evidence_packets → dict[str, EvidencePacket]

CAPS is the source of truth for every verdict (status, count, stoplight,
manual_review).  This module only SELECTS and SHAPES evidence — it never
re-judges, re-scores, or overrides any CAPS decision.

Selection limits (evidence items per criterion):
    beperking      3   — limitation block + research block + one more
    stakeholders   2   — stakeholder table preferred; paragraphs as fallback
    requirements   3   — FR / NFR / UC tables preferred in document order
    taalkeuze      3   — blocks with both choice AND consequence signals first
    security       3   — blocks with concrete mechanisms preferred
"""

from __future__ import annotations

import re
from typing import Final

from src.caps.caps import CapsPipelineArtifacts
from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS, CriterionSpec
from src.caps.models import CriterionResult
from src.caps.retrieval import RetrievalHit
from src.feedback.evidence import EvidenceItem, EvidencePacket, SignalClass

# ---------------------------------------------------------------------------
# Per-criterion item limits
# ---------------------------------------------------------------------------

_MAX_ITEMS: Final[dict[str, int]] = {
    "beperking":    3,
    "stakeholders": 2,
    "requirements": 3,
    "taalkeuze":    3,
    "security":     3,
}

# ---------------------------------------------------------------------------
# Lightweight signal term sets
# (selection / labelling only — CAPS judgement is authoritative)
# ---------------------------------------------------------------------------

_BEP_LIMITATION: Final[frozenset[str]] = frozenset({
    "beperking", "slechtziend", "blind", "doof", "dyslexie",
    "visuele beperking", "motorische", "auditieve", "cognitieve",
    "afbakening", "doelgroep met beperking", "specifieke doelgroep",
    "niche", "focus op", "gekozen doelgroep",
})

_BEP_RESEARCH: Final[frozenset[str]] = frozenset({
    "deskresearch", "desk research", "literatuuronderzoek", "literatuur",
    "literatuurlijst", "bronvermelding", "bronnenlijst", "bronnen", "bron",
    "onderzoek", "verkenning", "marktonderzoek", "user research",
    "wetenschappelijk",
})

_TAAL_CHOICE: Final[frozenset[str]] = frozenset({
    "taalkeuze", "taalversie", "meertalig", "in het nederlands",
    "in het engels", "nederlandstalig", "dutch", "english",
    "webshop in het", "taal van de",
    # Bare language names — aligned with checks.py _LANGUAGE_TERMS
    "nederlands", "engels", "duits", "french",
})

_TAAL_CONSEQUENCE: Final[frozenset[str]] = frozenset({
    "bereik", "vertaalkosten", "lokalisatie", "consequentie",
    "begrijpelijk", "taalbarrière", "taalbarrier", "vertaling",
    "meertaligheid", "internationaal", "beïnvloeden", "beïnvloed",
    "doelgroepbereik",
})

_SEC_CONCRETE: Final[frozenset[str]] = frozenset({
    "authenticatie", "autorisatie", "encryptie", "avg", "gdpr",
    "owasp", "https", "ssl", "tls", "jwt", "2fa", "two-factor",
    "xss", "csrf", "firewall", "bcrypt", "hashing", "wachtwoord",
    "toegangscontrole", "risicoanalyse", "privacyverklaring",
    "beveiligingsaudit",
})

_SEC_GENERIC: Final[frozenset[str]] = frozenset({
    "security", "beveiliging", "veiligheid", "veilig", "privacy",
})

# Requirement ID pattern — reused from checks.py logic, kept local to avoid
# coupling to checks.py internals.
_REQ_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(FR|NFR|USC|UC|US|REQ|CONSTR|CON|F|NF)[-\s]?\d{1,3}\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Excerpt helpers
# ---------------------------------------------------------------------------


def _excerpt_paragraph(text: str, max_len: int = 200) -> str:
    """Normalise whitespace and trim to max_len characters."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _excerpt_table(block: dict, max_data_rows: int = 4) -> str:
    """Format a table as 'header | header\\nrow | row…' for compact display.

    Includes header_row (if present) then up to max_data_rows data rows.
    Falls back to _excerpt_paragraph when table_meta is absent.
    """
    tm = block.get("table_meta")
    if not tm:
        return _excerpt_paragraph(block["text"])

    lines: list[str] = []
    header = tm.get("header_row")
    if header:
        lines.append(header)

    cells = tm.get("cells") or []
    for row in cells[:max_data_rows]:
        non_null = [c for c in row if c is not None]
        if non_null:
            lines.append(" | ".join(non_null))

    remaining = len(cells) - max_data_rows
    if remaining > 0:
        lines.append(f"… ({remaining} meer rijen)")

    return "\n".join(lines) if lines else _excerpt_paragraph(block["text"])


def _excerpt_for(hit: RetrievalHit) -> str:
    """Dispatch to the appropriate excerpt shaper for a retrieval hit."""
    b = hit.block
    if b["block_type"] == "table":
        return _excerpt_table(b)
    return _excerpt_paragraph(b["text"])

