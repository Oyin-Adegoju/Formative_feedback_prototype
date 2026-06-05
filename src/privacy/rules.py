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

# Studentnummer — losse s-prefix (bv. "s1146131"), 6 tot 8 cijfers.
_STUDENTNR_S_PREFIX_RE = re.compile(r"\b[sS]\d{6,8}\b")

# Studentnummer met label, bv. "Studentnummer: 1146131" of
# "studnr 1146131" of "S-nummer: s1146131".
_STUDENTNR_LABEL_NAMES = (
    r"student[\-\s]?nummer|studnr|studienummer|s[\-\s]?nummer"
)
_STUDENTNR_LABEL_RE = re.compile(
    r"\b(" + _STUDENTNR_LABEL_NAMES + r")\b"
    r"\s*[:=\-]?\s*"
    r"([sS]?\d{6,8})\b",
    re.IGNORECASE,
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


def _normalize_label(label: str) -> str:
    """Lowercase + collapsed whitespace, voor de `label`-metadata."""
    return re.sub(r"\s+", " ", label).strip().lower()


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


def find_student_numbers(text: str) -> list[RuleMatch]:
    """Geef studentnummer-matches terug.

    Twee niveaus:
      - met label ("Studentnummer: 1146131") → confidence='high'
      - losse s-prefix ("s1146131") → confidence='low'

    Bare 6-8-cijfer-getallen zonder label of s-prefix worden bewust
    NIET gedetecteerd (zou te veel false positives geven op tabel-
    cellen, pagina-codes en willekeurige IDs).
    """
    if not text:
        return []

    results: list[RuleMatch] = []
    labeled_spans: set[tuple[int, int]] = set()

    for m in _STUDENTNR_LABEL_RE.finditer(text):
        label = _normalize_label(m.group(1))
        nummer_start = m.start(2)
        nummer_end = m.end(2)
        labeled_spans.add((nummer_start, nummer_end))
        results.append(
            RuleMatch(
                rule_type="student_number",
                start=nummer_start,
                end=nummer_end,
                text=m.group(2),
                label=label,
                confidence="high",
            )
        )

    for m in _STUDENTNR_S_PREFIX_RE.finditer(text):
        span = (m.start(), m.end())
        if span in labeled_spans:
            continue  # al gevonden via label-route
        results.append(
            RuleMatch(
                rule_type="student_number",
                start=m.start(),
                end=m.end(),
                text=m.group(),
                label=None,
                confidence="low",
            )
        )

    results.sort(key=lambda r: (r.start, r.end, r.confidence))
    return results
