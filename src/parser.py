"""Parser voor INFIRST requirements-PDFs.

Leest een PDF in en zet de inhoud om naar een uniforme blokstructuur
(heading / paragraph / table_row) met heading-context. Geen rubric- of
beoordelingslogica: alleen structureren zodat een latere LLM-stap
gericht kan selecteren.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pdfplumber


# --- Datamodel ----------------------------------------------------------------


@dataclass
class Block:
    block_id: str
    type: str  # "heading" | "paragraph" | "table_row"
    page: int
    level: int | None
    heading_path: list[str]
    text: str


@dataclass
class ParsedDocument:
    doc_id: str
    source_path: str
    source_type: str
    quality_label: str | None
    page_count: int
    blocks: list[Block]
    warnings: list[str]
    status: str  # "success" | "empty" | "error"

# --- Heading-detectie ---------------------------------------------------------


# 1) Expliciete schoolconventie: regel begint met H1..H4
_H_PREFIX = re.compile(r"^\s*H([1-4])\b[\s:.\-]*(.+?)\s*$")
# 2) Nummering vooraan: "1", "1.1", "1.1.1", "1.1.1.1"
_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s+(\S.*?)\s*$")
# Leestekens die wijzen op een normale zin (dus geen heading)
_ZIN_EINDE = ".,;:!?"


def _detect_heading(line: str) -> tuple[int, str] | None:
    """Geeft (level, tekst) terug als de regel een heading lijkt te zijn."""
    stripped = line.strip()
    if not stripped:
        return None

    # Pattern 1: expliciete H1..H4 prefix
    m = _H_PREFIX.match(stripped)
    if m:
        return int(m.group(1)), stripped

    # Pattern 2: genummerde heading
    m = _NUMBERED.match(stripped)
    if m:
        number = m.group(1)
        rest = m.group(2).strip()
        # Sla zinnen over die toevallig met een nummer beginnen.
        if not rest or rest[-1] in _ZIN_EINDE:
            return None
        level = number.count(".") + 1
        if level <= 4 and len(stripped) <= 80:
            return level, stripped

    return None


def _update_heading_path(path: list[str], level: int, heading_text: str) -> list[str]:
    """Truncate path tot level-1, voeg dan deze heading toe."""
    new_path = path[: max(0, level - 1)]
    new_path.append(heading_text)
    return new_path

# --- Tabellen -----------------------------------------------------------------


def _format_table_row(row: list[str | None]) -> str:
    cells = [(c or "").strip() for c in row]
    cells = [c for c in cells if c]
    return " | ".join(cells)


# --- Diversen -----------------------------------------------------------------


def _slugify_doc_id(stem: str) -> str:
    s = re.sub(r"[^\w\-.]+", "_", stem)
    return s.strip("_") or "document"

