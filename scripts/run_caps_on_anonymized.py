"""Build a FULL-CONTENT CAPS handoff for ONE anonymized document.

This deliberately does NOT batch all documents into a single combined
caps_handoff.json anymore. You point it at exactly the document you want and it
writes one per-document handoff:

    data/anonymized/caps_handoff_<doc_id>.json

Strategy (src/feedback/full_content.py): per criterion it includes the FULL,
untrimmed text of every block whose deepest heading belongs to that criterion's
section family — plus, for taalkeuze and security only, a keyword fallback for
documents without a dedicated heading. No budget trimming, no table-row cap.
The output is the standard handoff shape, so it feeds straight into the pipeline:

    python scripts/run_full_pipeline_v2.py --handoff data/anonymized/caps_handoff_<doc_id>.json

Usage:
    py -3 scripts/run_caps_on_anonymized.py --input data/anonymized/Goed/51d405f7_anonymized.json

If --input is omitted, the placeholder constant below is used — edit it, or just
pass --input on the command line.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.caps.criterion_specs import CRITERIA_KEYS
from src.feedback.full_content import build_full_content_handoff

_DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "anonymized"

# Placeholder: edit this, or pass --input on the command line.
_INPUT_PLACEHOLDER = _DATA_DIR / "Goed" / "<zet-hier-je-document>_anonymized.json"


def _normalize_report(raw: dict) -> dict:
    """Inject page_count when the anonymized JSON omits it (CAPS-contract shim)."""
    if "page_count" not in raw or raw["page_count"] is None:
        blocks = raw.get("blocks") or []
        raw["page_count"] = max((b["page_no"] for b in blocks), default=0)
    return raw


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build a full-content CAPS handoff for ONE anonymized document.",
    )
    p.add_argument("--input", metavar="PATH",
                   help="Path to the anonymized JSON document (defaults to the placeholder).")
    p.add_argument("--output-dir", default=str(_DATA_DIR), metavar="DIR",
                   help="Directory to write the per-document handoff (default: data/anonymized).")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    input_path = pathlib.Path(args.input) if args.input else _INPUT_PLACEHOLDER

    if not input_path.exists():
        print(f"[FATAL] Input niet gevonden: {input_path}")
        if not args.input:
            print("        Geef --input <pad> mee, of pas de placeholder bovenin het script aan.")
        return 1

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    report = _normalize_report(raw)

    handoff = build_full_content_handoff(report)
    doc_id = handoff["document_id"] or input_path.stem

    out_path = pathlib.Path(args.output_dir) / f"caps_handoff_{doc_id}.json"
    out_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{'=' * 60}\n  Full-content handoff: {doc_id}\n{'=' * 60}")
    print(f"  bron     : {input_path}")
    for key in CRITERIA_KEYS:
        items = handoff["criteria"][key]["evidence_items"]
        chars = sum(len(it["excerpt"]) for it in items)
        kw = sum(1 for it in items if it.get("classification_source") == "keyword")
        kw_str = f" (waarvan {kw} trefwoord)" if kw else ""
        print(f"  {key:13} blokken={len(items):3}{kw_str}  tekens={chars}")
    print(f"\n[saved] {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
