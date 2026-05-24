"""Structure-laag: classificeer RawElements, filter ruis, voeg tabelrijen
samen, bouw heading_path en produceer Block-objecten.

Geen rubric-logica, geen stakeholder- of requirement-detectie. Alleen
structurele heuristieken.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from src.parser.ingest import RawElement


# --- Datamodel ----------------------------------------------------------------


BlockType = Literal[
    "heading",
    "paragraph",
    "bullet",
    "table",
    "caption",
    "front_matter",
    "appendix",
    "noise",
    "template",
]


@dataclass
class Block:
    doc_id: str
    block_id: str
    page_no: int
    block_type: BlockType
    heading_path: list[str]
    text: str
    token_estimate: int
    is_front_matter: bool = False
    is_appendix: bool = False
    table_meta: dict | None = None


# --- Patronen -----------------------------------------------------------------

# Headings
_H_PREFIX = re.compile(r"^\s*H([1-4])\b[\s:.\-]*(.+?)\s*$")
_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+(\S.*?)\s*$")
_ZIN_EINDE = ".,;:!?"

# Front-matter / inhoudsopgave 
_TOC_DOTS = re.compile(r"\.{4,}")
_PAGE_NUMBER_ONLY = re.compile(r"^\s*\d{1,4}\s*$")
_TOC_KEYWORDS = ("inhoudsopgave", "table of contents", "contents", "inhoud")
# Regel die eindigt op een paginanummer
_TOC_LINE = re.compile(r"(?:\.{3,}\s*|\s+)\d{1,4}\s*$")

# Extra front-matter signalen (probleem 2)
_STUDENTNR = re.compile(r"\b[sS]?\d{7}\b") 
_DATUM = re.compile(r"\b\d{1,2}[-/\.]\d{1,2}[-/\.]\d{2,4}\b")
_VERSIE = re.compile(r"\b(v\d+(?:\.\d+)?|versie\s+\d+)\b", re.IGNORECASE)
_VELDLABEL = re.compile(
    r"^\s*[A-Za-zÀ-ÿ][\wÀ-ÿ\- ]*\s*[:=]\s+\S",
)
_MODULE_OPLEIDING_KW = re.compile(
    r"\b(hbo-?ict|informatica|infirst|infifs|semester|periode|leerjaar|"
    r"module|challenge\s+week|first\s+full\s+stack|opleiding)\b",
    re.IGNORECASE,
)
# Komma-gescheiden namenlijst: minstens 2 komma's en mostly Capitalized woorden.
_NAMENLIJST = re.compile(
    r"^\s*([A-Z][a-zÀ-ÿ\-']+(\s+[A-Z][a-zÀ-ÿ\-']+){0,3})"
    r"(\s*,\s*[A-Z][a-zÀ-ÿ\-']+(\s+[A-Z][a-zÀ-ÿ\-']+){0,3}){1,}\s*$"
)

# Bullets
_BULLET_PREFIX = re.compile(
    r"^\s*([•○●■□▪◦·\-\*–—]|[a-zA-Z]\)|\d+\))\s+\S"
)

# Captions
_CAPTION_PREFIX = re.compile(
    r"^\s*(figuur|figure|afbeelding|tabel|table)\s+\d+\b",
    flags=re.IGNORECASE,
)

# Appendix-trigger
_APPENDIX_RE = re.compile(r"^\s*(bijlage|appendix)\b", flags=re.IGNORECASE)

