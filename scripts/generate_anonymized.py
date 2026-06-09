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
 
 
def to_public_report(report: dict) -> dict:
    """Geef een kopie van `report` zonder zichtbare mapping terug.
 
    `mapping` wordt leeggemaakt; `mapping_count` en `blocks` blijven
    ongewijzigd (blocks zijn dezelfde objecten -> identiek in beide
    outputs). Het origineel wordt NIET gemuteerd.
    """
    public = dict(report)
    public["mapping"] = {}
    return public
 
 
def _write_json(obj: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        
 
 
def generate_one(
    pdf_path: Path,
    quality_label: str | None,
    catalog,
    public_dir: Path,
    internal_dir: Path,
) -> tuple[str, int]:
    """Parse + anonimiseer één PDF en schrijf public + internal output."""
    doc_id = hash_document(str(pdf_path))[:8]
    raw = extract_raw_elements(str(pdf_path))
    blocks = build_blocks(raw, doc_id=doc_id)
    new_blocks, mapping = anonymize_blocks(blocks, catalog)
 
    meta = {
        "doc_id": doc_id,
        "source_path": str(pdf_path),
        "source_name": pdf_path.name,
        "quality_label": quality_label,
    }
    report = build_anonymized_report(meta, new_blocks, mapping)
 
    sub = quality_label or ""
    _write_json(report, internal_dir / sub / f"{doc_id}_anonymized.json")
    _write_json(to_public_report(report), public_dir / sub / f"{doc_id}_anonymized.json")
    return doc_id, len(mapping)
 
 
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help=f"Map met bron-PDF's (default: {DEFAULT_SOURCE}).")
    parser.add_argument("--public-dir", type=Path, default=DEFAULT_PUBLIC_DIR,
                        help=f"Public output (default: {DEFAULT_PUBLIC_DIR}).")
    parser.add_argument("--internal-dir", type=Path, default=DEFAULT_INTERNAL_DIR,
                        help=f"Internal/debug output (default: {DEFAULT_INTERNAL_DIR}).")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG,
                        help=f"People-catalogus (default: {DEFAULT_CATALOG}).")
    args = parser.parse_args(argv)
 
    catalog = load_catalog(str(args.catalog))
    n = 0
    for pdf in sorted(args.source.rglob("*.pdf")):
        rel = pdf.relative_to(args.source)
        label = rel.parts[0] if len(rel.parts) > 1 else None
        if label not in _LABELS:
            continue
        doc_id, mc = generate_one(pdf, label, catalog, args.public_dir, args.internal_dir)
        print(f"  {label:12s} {doc_id}  mapping={mc:2d}  <- {pdf.name}")
        n += 1
 
    print(f"Gegenereerd: {n} documenten")
    print(f"  public  : {args.public_dir}")
    print(f"  internal: {args.internal_dir}")
    return 0
 
 
if __name__ == "__main__":
    raise SystemExit(main())