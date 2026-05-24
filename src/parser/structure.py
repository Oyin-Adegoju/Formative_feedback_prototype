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

