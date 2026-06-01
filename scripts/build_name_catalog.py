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


