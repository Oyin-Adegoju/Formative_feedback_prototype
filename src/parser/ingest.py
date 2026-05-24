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


