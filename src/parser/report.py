

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.parser.structure import Block


#  Telling en kwaliteit 


def count_by_type(blocks: list["Block"]) -> dict:
    """Tel het aantal blokken per block_type."""
    counts: dict[str, int] = {}
    for b in blocks:
        counts[b.block_type] = counts.get(b.block_type, 0) + 1
    return counts


def estimate_text_quality(blocks: list["Block"]) -> float:
    """Heuristische score 0..1 voor 'hoeveel tekst extraheerden we netjes'.

    Combineert:
      - verhouding niet-lege blokken
      - gemiddelde tekstlengte (genormaliseerd op ~120 tekens)
      - aanwezigheid van headings
    """
    if not blocks:
        return 0.0

    non_empty = [b for b in blocks if b.text.strip()]
    leeg_ratio = len(non_empty) / len(blocks)

    if non_empty:
        gemiddelde_lengte = mean(len(b.text) for b in non_empty)
        lengte_score = min(1.0, gemiddelde_lengte / 120.0)
    else:
        lengte_score = 0.0

    headings = sum(1 for b in blocks if b.block_type == "heading")
    heading_score = 1.0 if headings >= 3 else (0.5 if headings >= 1 else 0.0)

    return round((leeg_ratio + lengte_score + heading_score) / 3.0, 3)


