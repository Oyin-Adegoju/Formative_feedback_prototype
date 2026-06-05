"""Regex/rule-laag voor privacygevoelige patronen.

Wordt door anonymizer.py gebruikt voor deterministische pattern-based
detectie van emails, telefoonnummers, studentnummers en gelabelde
gevoelige velden.

Doet expliciet NIET:
  - naamcatalogus laden (zie src/privacy/catalog.py)
  - fuzzy matching
  - NER / Presidio / spaCy
  - placeholders maken
  - whitelist toepassen
  - Block-objecten muteren
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# --- Datamodel --------------------------------------------------------------


@dataclass(frozen=True)
class RuleMatch:
    """Immutabele rule-detectie. `start`/`end` zijn karakter-offsets in de
    oorspronkelijke tekst (geschikt voor str.replace-style anonymisering)."""
    rule_type: str
    start: int
    end: int
    text: str
    label: str | None
    confidence: str  # "high" | "low"


# --- Patronen ---------------------------------------------------------------

# Email — pragmatische regex (geen volledig RFC 5322-monster, wel goed
# genoeg voor student-/instellingsmailadressen).
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)


# --- Finders ----------------------------------------------------------------


def find_emails(text: str) -> list[RuleMatch]:
    """Geef emailadres-matches terug, in volgorde van voorkomen."""
    if not text:
        return []
    return [
        RuleMatch(
            rule_type="email",
            start=m.start(),
            end=m.end(),
            text=m.group(),
            label=None,
            confidence="high",
        )
        for m in _EMAIL_RE.finditer(text)
    ]
