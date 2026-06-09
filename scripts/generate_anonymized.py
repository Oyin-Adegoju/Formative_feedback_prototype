"""Genereer geanonimiseerde outputs vanaf de bron-PDF's, met sidecar-split.
 
Per document worden TWEE artefacten weggeschreven met identieke (al
geanonimiseerde) blocks:
 
  - internal/debug  (mét `mapping`)   -> data/anonymized_internal/<label>/
  - public/export   (mapping leeg)    -> data/anonymized/<label>/
 
De `mapping` bevat de originele gevoelige waarden (echte namen) en hoort
daarom NIET in de public/export-output. `mapping_count` blijft wel staan —
dat is enkel een getal, geen PII.
 
Run:
    python scripts/generate_anonymized.py
    python scripts/generate_anonymized.py --source INFIRST_requirements
"""
 
from __future__ import annotations
 
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
 
# Toestaan vanaf de projectroot zonder install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
 
from src.parser import build_blocks, extract_raw_elements
from src.parser.report import hash_document
from src.privacy.anonymizer import anonymize_blocks
from src.privacy.catalog import load_catalog
 
DEFAULT_SOURCE = Path("INFIRST_requirements")
DEFAULT_PUBLIC_DIR = Path("data/anonymized")
DEFAULT_INTERNAL_DIR = Path("data/anonymized_internal")
DEFAULT_CATALOG = Path("data/reference/people_catalog.json")
 
# Alleen deze submappen zijn student-documenten (sla bv. 'rubrik/' over).
_LABELS = {"Goed", "Voldoende", "onvoldoende"}
 
 
def build_anonymized_report(
    meta: dict,
    new_blocks: list,
    mapping: dict,
) -> dict:
    """Bouw de (interne) anonymized-report dict — het huidige schema."""
    return {
        "doc_id": meta["doc_id"],
        "source_path": meta["source_path"],
        "source_name": meta["source_name"],
        "quality_label": meta["quality_label"],
        "block_count": len(new_blocks),
        "mapping_count": len(mapping),
        "mapping": mapping,
        "blocks": [asdict(b) for b in new_blocks],
    }