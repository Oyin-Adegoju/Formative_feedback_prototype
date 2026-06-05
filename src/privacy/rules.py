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

# Telefoon — kandidaten met +31-prefix of leading 0; daarna achteraf
# valideren op exact 10 (0-prefix) of 11 (+31-prefix) digits.
_PHONE_CANDIDATE_RE = re.compile(
    r"(?<![\w.])"                       # niet midden in een woord/getal
    r"(?:\+\s*31|0)"                    # landcode of binnenlandse 0
    r"(?:[\s\-()]?\d){8,10}"            # 8–10 vervolgcijfers met optionele sep
    r"(?!\w)"
)


# --- Helpers ----------------------------------------------------------------


def _is_valid_dutch_phone(span_text: str) -> bool:
    """Valideer dat de digits in `span_text` een geldig NL-nummer vormen.

    Acceptatie:
      - 10 digits beginnend met 0, of
      - 11 digits beginnend met 31 (afgeleid van +31-vorm).
    """
    digits = re.sub(r"\D", "", span_text)
    if digits.startswith("31") and len(digits) == 11:
        return True
    if digits.startswith("0") and len(digits) == 10:
        return True
    return False


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


def find_phone_numbers(text: str) -> list[RuleMatch]:
    """Geef NL-telefoonnummer-matches terug, na digit-count-validatie."""
    if not text:
        return []
    results: list[RuleMatch] = []
    for m in _PHONE_CANDIDATE_RE.finditer(text):
        span_text = m.group()
        if not _is_valid_dutch_phone(span_text):
            continue
        results.append(
            RuleMatch(
                rule_type="phone_nl",
                start=m.start(),
                end=m.end(),
                text=span_text,
                label=None,
                confidence="high",
            )
        )
    return results
