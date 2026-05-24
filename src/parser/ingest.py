"""Ingest-laag: opent een PDF en levert ruwe RawElement-objecten.

Doel: alleen extractie. Geen classificatie, geen ruisfiltering,
geen heading-detectie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import pdfplumber


# --- Datamodel 


@dataclass
class RawElement:
    tekst: str
    pagina: int
    x0: float
    y0: float
    x1: float
    y1: float
    lettergrootte: float | None = None
    vet: bool | None = None
    # Pragmatische uitbreiding: aantal kolommen bij tabel-rijen (None = geen tabel).
    column_count: int | None = None


# --- PDF openen 


def open_pdf(pad: str):
    """Wrapper rond pdfplumber.open; returnt het pdfplumber.PDF-object.

    De caller is verantwoordelijk voor sluiten (with-statement).
    """
    return pdfplumber.open(pad)


# --- Words en tabellen


def extract_words(page) -> list[dict]:
    """Geef de woorden van een pagina terug, inclusief font-attributen."""
    try:
        return page.extract_words(extra_attrs=["size", "fontname"]) or []
    except Exception:
        # Fallback zonder extra attrs als pdfplumber faalt op specifieke fonts.
        return page.extract_words() or []


# Strengere tabel-detectie: alleen tabellen waar daadwerkelijk lijnen zichtbaar
# zijn. De pdfplumber-default valt ook terug op whitespace-strategieën en
# detecteert dan willekeurige tekstkolommen als 'tabel' (false positives op
# grafisch-zware PDFs zoals Canva-exports).
_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
}


def extract_tables(page) -> list[list[list[str]]]:
    """Geef gevonden tabellen terug als list[rows[cells]]."""
    try:
        return page.extract_tables(table_settings=_TABLE_SETTINGS) or []
    except Exception:
        return []


# --- Helpers 


def _is_bold(fontname: str | None) -> bool | None:
    if not fontname:
        return None
    return "bold" in fontname.lower()


def _group_words_to_lines(
    words: list[dict],
    y_tolerantie: float = 3.0,
) -> list[list[dict]]:
    """Groepeer woorden naar regels op basis van de y-positie."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    current: list[dict] = [sorted_words[0]]
    for w in sorted_words[1:]:
        if abs(w["top"] - current[0]["top"]) <= y_tolerantie:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
    lines.append(sorted(current, key=lambda x: x["x0"]))
    return lines


