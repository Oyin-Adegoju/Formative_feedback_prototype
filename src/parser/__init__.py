"""Parser-pakket: openbare entry points voor single-doc en batch parsing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.parser.ingest import RawElement, extract_raw_elements
from src.parser.report import generate_report, hash_document, save_report
from src.parser.structure import Block, build_blocks, pipeline_blocks


__all__ = [
    "Block",
    "RawElement",
    "extract_raw_elements",
    "build_blocks",
    "generate_report",
    "hash_document",
    "save_report",
    "parse_document",
    "parse_batch",
    "pipeline_blocks",
]


def parse_document(
    pad: str,
    output_dir: str | Path = "data/parsed",
    quality_label: str | None = None,
) -> tuple[list[Block], dict]:
    """Parse één PDF en schrijf het rapport naar `<output_dir>/<doc_id>_report.json`.

    Geeft (blocks, report) terug zodat callers de blokken direct kunnen
    doorgeven aan de volgende pipeline-stap zonder de JSON opnieuw te lezen.
    """
    doc_id = hash_document(pad)[:8]
    raw = extract_raw_elements(pad)
    blocks = build_blocks(raw, doc_id=doc_id)
    report = generate_report(blocks, pad, doc_id)
    if quality_label is not None:
        report["quality_label"] = quality_label
    out_path = Path(output_dir) / f"{doc_id}_report.json"
    save_report(report, str(out_path))
    return blocks, report


def parse_batch(
    map_pad: str,
    output_dir: str | Path = "data/parsed",
) -> list[tuple[list[Block], dict]]:
    """Parse alle PDFs recursief in `map_pad`.

    De directe submap (bv. 'Goed'/'Voldoende'/'onvoldoende') wordt als
    `quality_label` in het rapport gezet én als submap onder `output_dir`
    behouden.
    """
    root = Path(map_pad)
    out_root = Path(output_dir)

    resultaten: list[tuple[list[Block], dict]] = []
    pdfs = sorted(root.rglob("*.pdf"))
    for pdf_pad in pdfs:
        try:
            relative = pdf_pad.relative_to(root)
            quality_label = relative.parts[0] if len(relative.parts) > 1 else None
        except ValueError:
            quality_label = None

        # Output gaat naar data/parsed/<submap>/, of direct in data/parsed/
        # als het bestand op de root van map_pad ligt.
        if quality_label is not None:
            sub_out = out_root / quality_label
        else:
            sub_out = out_root

        blocks, report = parse_document(
            str(pdf_pad),
            output_dir=sub_out,
            quality_label=quality_label,
        )
        resultaten.append((blocks, report))
    return resultaten


# --- CLI ---------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(
        description="Parse INFIRST requirements-PDFs naar gestructureerde JSON."
    )
    bron = cli.add_mutually_exclusive_group(required=True)
    bron.add_argument("--file", type=str, help="Parse één PDF-bestand.")
    bron.add_argument("--batch", type=str, help="Parse alle PDFs recursief in een map.")
    cli.add_argument(
        "--out",
        type=str,
        default="data/parsed",
        help="Outputmap voor parse-reports (default: data/parsed).",
    )
    cli.add_argument(
        "--quality-label",
        type=str,
        default=None,
        help="Optioneel kwaliteitslabel bij --file.",
    )
    args = cli.parse_args(argv)

    if args.file:
        blocks, report = parse_document(
            args.file, output_dir=args.out, quality_label=args.quality_label
        )
        print(
            f"doc_id={report['doc_id']}  blocks={report['block_count']}  "
            f"warnings={len(report['warnings'])}  quality={report.get('text_quality')}"
        )
        return 0

    resultaten = parse_batch(args.batch, output_dir=args.out)
    print(f"Verwerkt: {len(resultaten)} PDFs.")
    for _, report in resultaten:
        print(
            f"  {report.get('quality_label', '-'):12} "
            f"{report['doc_id']}  "
            f"blocks={report['block_count']:>4}  "
            f"warnings={len(report['warnings'])}  "
            f"({report['source_name']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(_main())
