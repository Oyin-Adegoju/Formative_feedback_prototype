"""section_family.py — corpus-derived section-family classifier for evidence gating.

This module answers ONE question, deterministically and explainably:

    "Given a block's heading_path, which criterion section-family/families does
     that block structurally belong to?"

It is the primary admission authority for the CAPS→Qwen evidence handoff. The
packet builder uses it to make evidence selection *section-first*: a block is
admitted for a criterion mainly because its section identity matches that
criterion — signals (matched keywords, row-ids, subtypes) only assist, rank, or
rescue, and never override a clear section mismatch.

Design notes
------------
* Membership is a SET, not a single label. One heading_path can belong to
  several families at once — e.g. "Stakeholder quadrant > H3 Onderzoek naar web
  gebruikers > Functionele requirements" legitimately serves stakeholders,
  beperking AND requirements. Multi-family membership is what prevents the
  gate from over-rejecting genuinely shared sections.
* Patterns are matched against each heading_path SEGMENT (not the joined path),
  so a hint cannot accidentally span two adjacent segments. Word boundaries are
  used where a bare substring would over-match (e.g. ``\\beis(en)?\\b`` so "eis"
  does not fire inside "reis"/"eisenlijst" noise).
* The families are compact and corpus-informed (derived from the 9 anonymized
  documents under data/anonymized/), NOT exhaustive synonym lists.
* No block text is read here — only heading_path. Content signals are the
  packet builder's job. This keeps the section authority and the signal
  assistance cleanly separated.

taalkeuze has, in practice, no dedicated section family in the corpus: language
choice is discussed inside doelgroep / requirements / intro prose. So taalkeuze
deliberately has only a thin pattern set; the packet builder gates it on a real
language-CHOICE token instead of on section identity. That is intentional and
documented in the builder.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Criterion section-family patterns (corpus-derived)
# ---------------------------------------------------------------------------
#
# Each entry is a tuple of compiled, case-insensitive patterns. A heading_path
# belongs to a family when ANY of its segments matches ANY pattern of that
# family. Keep these tight and explainable — broaden only with a corpus reason.

_BEPERKING_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"onderzoek naar web", re.IGNORECASE),   # canonical doelgroep section
    re.compile(r"web ?gebruikers", re.IGNORECASE),
    re.compile(r"doelgroep", re.IGNORECASE),
    re.compile(r"webshopgebruiker", re.IGNORECASE),
    re.compile(r"\bbeperking", re.IGNORECASE),
    re.compile(r"\bafbakening", re.IGNORECASE),
    re.compile(r"literatuur", re.IGNORECASE),            # literatuurlijst — research support
    re.compile(r"\bbron(?:nen|vermelding)?\b", re.IGNORECASE),
    re.compile(r"deskresearch", re.IGNORECASE),
    re.compile(r"\bverkenning\b", re.IGNORECASE),
    re.compile(r"persona", re.IGNORECASE),
    re.compile(r"richt me", re.IGNORECASE),              # "Ik Richt Me Dus Op Deze Doelgroep"
)

_STAKEHOLDERS_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"stakeholder", re.IGNORECASE),
    re.compile(r"belanghebbende", re.IGNORECASE),
    re.compile(r"\bbetrokken", re.IGNORECASE),           # betrokkenheid / betrokkenen
    re.compile(r"\bactoren\b", re.IGNORECASE),
    re.compile(r"belangen en zorgen", re.IGNORECASE),
    re.compile(r"categoris", re.IGNORECASE),             # categoriseren / categorisatie
    re.compile(r"\bquadrant\b", re.IGNORECASE),
    re.compile(r"mendelow", re.IGNORECASE),
)

_REQUIREMENTS_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"requirement", re.IGNORECASE),
    re.compile(r"\beis(?:en)?\b", re.IGNORECASE),
    re.compile(r"wensen", re.IGNORECASE),
    re.compile(r"prioriteiten", re.IGNORECASE),
    re.compile(r"functione(?:le|el|l)\b", re.IGNORECASE),       # functionele / functioneel
    re.compile(r"functional\b", re.IGNORECASE),
    re.compile(r"(?:niet|non)[\s\-]+functione(?:le|el|l)", re.IGNORECASE),
    re.compile(r"(?:niet|non)[\s\-]+functional", re.IGNORECASE),
    re.compile(r"constraint", re.IGNORECASE),
    re.compile(r"use[\s\-]?cases?", re.IGNORECASE),
    re.compile(r"\bus[c]?\d", re.IGNORECASE),                   # USC1 / UC1 headings
    re.compile(r"moscow", re.IGNORECASE),
)

_SECURITY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"security", re.IGNORECASE),
    re.compile(r"beveiliging", re.IGNORECASE),
    re.compile(r"\bveiligheid\b", re.IGNORECASE),
    re.compile(r"privacy", re.IGNORECASE),
    re.compile(r"\bavg\b", re.IGNORECASE),               # "AVG-naleving"
    re.compile(r"owasp", re.IGNORECASE),
    re.compile(r"\brisico", re.IGNORECASE),
)

# taalkeuze: intentionally thin — see module docstring. Real gating is by token.
_TAALKEUZE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"taalkeuze", re.IGNORECASE),
    re.compile(r"taalversie", re.IGNORECASE),
    re.compile(r"meertalig", re.IGNORECASE),
    re.compile(r"internationalisatie", re.IGNORECASE),
    re.compile(r"lokalisatie", re.IGNORECASE),
    re.compile(r"^taal$", re.IGNORECASE),
)

SECTION_FAMILIES: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "beperking":    _BEPERKING_PATTERNS,
    "stakeholders": _STAKEHOLDERS_PATTERNS,
    "requirements": _REQUIREMENTS_PATTERNS,
    "taalkeuze":    _TAALKEUZE_PATTERNS,
    "security":     _SECURITY_PATTERNS,
}
"""criterion_key → compiled heading patterns. Exposed for tests/inspection."""


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------


def families_of(heading_path: list[str]) -> frozenset[str]:
    """Return the set of criterion families a heading_path structurally belongs to.

    A family matches when ANY heading segment matches ANY of its patterns.
    An empty result means the block is *neutral* — it sits under a generic
    heading (Inleiding, an empty path, a page header) that belongs to no
    criterion family. Neutral blocks are admitted by the builder only when they
    carry a real content signal.
    """
    fams: set[str] = set()
    for seg in heading_path:
        s = seg.strip()
        if not s:
            continue
        for key, patterns in SECTION_FAMILIES.items():
            if key in fams:
                continue
            if any(p.search(s) for p in patterns):
                fams.add(key)
    return frozenset(fams)


def is_in_family(criterion_key: str, fams: frozenset[str]) -> bool:
    """True when the block's section families include this criterion."""
    return criterion_key in fams


def is_foreign(criterion_key: str, fams: frozenset[str]) -> bool:
    """True when the block belongs to other families but NOT this criterion.

    Foreign blocks are the contamination risk: they have a clear section
    identity that points elsewhere. The builder rejects them unless a strong,
    criterion-defining token rescues them (and even then with a context_warning).
    """
    return bool(fams) and criterion_key not in fams


def is_neutral(fams: frozenset[str]) -> bool:
    """True when the block belongs to no criterion family (generic context)."""
    return not fams


# ---------------------------------------------------------------------------
# Negation phrases (signal hardening — shared across criteria)
# ---------------------------------------------------------------------------
#
# Block-level negation: when a block explicitly negates its own criterion
# signal, the builder must NOT present it as positive supporting evidence.
# Detected at block level only (cannot see across blocks). Corpus examples:
# "Verder heb ik geen bronnen gebruikt.", "Ik heb geen echt onderzoek gedaan",
# "niet gebaseerd op interviews of bronnen, maar klinkt logisch."

_NEGATION_PHRASES: Final[tuple[str, ...]] = (
    "geen bronnen",
    "geen bron ",
    "geen onderzoek",
    "geen echt onderzoek",
    "geen echte bronnen",
    "geen deskresearch",
    "geen literatuur",
    "geen bronnenlijst",
    "geen bronvermelding",
    "niet onderzocht",
    "niet gebaseerd op bronnen",
    "niet gebaseerd op interviews",
    "geen keuze gemaakt",
    "klinkt logisch",
    "zelf bedacht",
    "zelf verzonnen",
)


def contains_negation(text: str) -> bool:
    """True when the block text explicitly negates its own evidence signal."""
    low = text.lower()
    return any(phrase in low for phrase in _NEGATION_PHRASES)
