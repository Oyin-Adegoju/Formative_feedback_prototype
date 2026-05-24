
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


