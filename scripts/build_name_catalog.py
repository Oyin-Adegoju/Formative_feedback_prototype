"""
Geen runtime dependency van de anonymizer. De anonymizer leest alleen
het resulterende JSON-bestand, nooit de originele CSV.

Run:
    python scripts/build_name_catalog.py
    python scripts/build_name_catalog.py --input <csv> --output <json>
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import unicodedata
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("data/reference/people_software_advanced_2026.csv")
DEFAULT_OUTPUT = Path("data/reference/people_catalog.json")

# Nederlandse + Arabische-romeinse tussenvoegsels die we vooraan een
# achternaam kunnen tegenkomen. Gebruikt voor het splitsen "van der Berg"
# naar (tussenvoegsel='van der', achternaam='Berg').
_TUSSENVOEGSELS: frozenset[str] = frozenset({
    "van", "van der", "van de", "van den", "van het", "van 't",
    "de", "den", "der", "des", "du",
    "te", "ten", "ter",
    "in", "in 't", "op", "op 't", "aan",
    "het", "'t",
    "von", "zu", "zur",
    "le", "la", "el", "al", "abu", "ibn", "ben",
    "of",
})

logger = logging.getLogger(__name__)


# Normalisatie 


def _strip_diacritics(s: str) -> str:
    """Verwijder combining diacritics (Daniël → Daniel, Böhre → Bohre)."""
    if not s:
        return s
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _title_case(s: str) -> str:
    """. Bewaart diacritics."""
    if not s:
        return s
    return s.strip().title()


def _split_tussenvoegsel(achternaam: str) -> tuple[str | None, str]:
    """Splits achternaam in (tussenvoegsel, kern-achternaam).

    Probeert eerst de langst mogelijke prefix (tot 3 woorden) te matchen
    tegen `_TUSSENVOEGSELS`. Returnt (None, achternaam) als er geen
    tussenvoegsel herkend wordt.
    """
    if not achternaam:
        return None, achternaam
    parts = achternaam.split()
    if len(parts) < 2:
        return None, achternaam
    max_prefix = min(len(parts) - 1, 3)
    for n in range(max_prefix, 0, -1):
        prefix = " ".join(parts[:n]).lower()
        if prefix in _TUSSENVOEGSELS:
            return prefix, " ".join(parts[n:])
    return None, achternaam


