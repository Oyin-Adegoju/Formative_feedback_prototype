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

