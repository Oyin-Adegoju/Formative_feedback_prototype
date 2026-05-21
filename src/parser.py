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

# --- Per-pagina extractie -----------------------------------------------------


def _extract_page_blocks(
    page,
    page_number: int,
    block_counter: list[int],
    heading_path: list[str],
    warnings: list[str],
) -> list[Block]:
    """Extract heading/paragraph/table_row blocks uit één pagina.

    `block_counter` en `heading_path` worden in-place geüpdatet zodat
    block-IDs en heading-context over pagina's heen consistent blijven.
    """
    blocks: list[Block] = []

    # 1. Tabellen detecteren + bboxes verzamelen.
    try:
        tables = page.find_tables()
    except Exception as e:  
        tables = []
        warnings.append(f"pagina {page_number}: tabel-detectie faalde ({e})")

    table_bboxes: list[tuple[float, float, float, float]] = []
    table_rows_per_table: list[list[str]] = []
    for table in tables:
        try:
            table_bboxes.append(table.bbox)
            rows = table.extract() or []
            formatted = [_format_table_row(r) for r in rows]
            table_rows_per_table.append([r for r in formatted if r])
        except Exception as e:  # noqa: BLE001
            warnings.append(f"pagina {page_number}: tabel-extractie faalde ({e})")

    # 2. Tekst extraheren  objecten in tabel-bboxes overslaan.
    def _buiten_tabellen(obj) -> bool:
        cx = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
        cy = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
        for x0, top, x1, bottom in table_bboxes:
            if x0 <= cx <= x1 and top <= cy <= bottom:
                return False
        return True

    try:
        if table_bboxes:
            page_text = page.filter(_buiten_tabellen).extract_text() or ""
        else:
            page_text = page.extract_text() or ""
    except Exception as e:  
        warnings.append(f"pagina {page_number}: tekst-extractie faalde ({e})")
        page_text = ""

    # 3. Regels groeperen naar heading / paragraaf.
    current_path = list(heading_path)
    paragraph_buffer: list[str] = []

    def _flush_paragraph() -> None:
        if not paragraph_buffer:
            return
        tekst = " ".join(paragraph_buffer).strip()
        paragraph_buffer.clear()
        if not tekst:
            return
        block_counter[0] += 1
        blocks.append(
            Block(
                block_id=f"b{block_counter[0]:04d}",
                type="paragraph",
                page=page_number,
                level=None,
                heading_path=list(current_path),
                text=tekst,
            )
        )

    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            _flush_paragraph()
            continue
        heading = _detect_heading(line)
        if heading is not None:
            _flush_paragraph()
            level, text = heading
            current_path = _update_heading_path(current_path, level, text)
            block_counter[0] += 1
            blocks.append(
                Block(
                    block_id=f"b{block_counter[0]:04d}",
                    type="heading",
                    page=page_number,
                    level=level,
                    heading_path=list(current_path),
                    text=text,
                )
            )
        else:
            paragraph_buffer.append(line)
    _flush_paragraph()

    # 4. Tabelrijen toevoegen (na de tekst van deze pagina, met de
    #    laatst geldige heading-context).
    for table_rows in table_rows_per_table:
        for row_text in table_rows:
            block_counter[0] += 1
            blocks.append(
                Block(
                    block_id=f"b{block_counter[0]:04d}",
                    type="table_row",
                    page=page_number,
                    level=None,
                    heading_path=list(current_path),
                    text=row_text,
                )
            )

    # Heading-path doorgeven aan volgende pagina.
    heading_path[:] = current_path
    return blocks

# --- Hoofdfuncties ------------------------------------------------------------


def parse_pdf(path: str | Path, quality_label: str | None = None) -> ParsedDocument:
    """Parse één PDF naar een ParsedDocument."""
    p = Path(path)
    doc_id = _slugify_doc_id(p.stem)
    warnings: list[str] = []
    blocks: list[Block] = []
    page_count = 0

    try:
        with pdfplumber.open(p) as pdf:
            page_count = len(pdf.pages)
            block_counter = [0]
            heading_path: list[str] = []
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    page_blocks = _extract_page_blocks(
                        page,
                        page_number=i,
                        block_counter=block_counter,
                        heading_path=heading_path,
                        warnings=warnings,
                    )
                    blocks.extend(page_blocks)
                except Exception as e:  # noqa: BLE001 - pagina kan corrupt zijn
                    warnings.append(f"pagina {i}: verwerking gefaald ({e})")
    except Exception as e:  # noqa: BLE001 - PDF openen kan falen
        return ParsedDocument(
            doc_id=doc_id,
            source_path=str(p),
            source_type="pdf",
            quality_label=quality_label,
            page_count=0,
            blocks=[],
            warnings=[f"PDF kon niet geopend worden ({e})"],
            status="error",
        )

    if not blocks:
        warnings.append("geen extraheerbare tekst gevonden (mogelijk geen tekstlaag)")
        status = "empty"
    else:
        status = "success"

    return ParsedDocument(
        doc_id=doc_id,
        source_path=str(p),
        source_type="pdf",
        quality_label=quality_label,
        page_count=page_count,
        blocks=blocks,
        warnings=warnings,
        status=status,
    )


def write_parsed(doc: ParsedDocument, output_dir: str | Path) -> Path:
    """Schrijf één ParsedDocument naar `<output_dir>/<doc_id>.json`."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{doc.doc_id}.json"
    with target.open("w", encoding="utf-8") as f:
        json.dump(asdict(doc), f, ensure_ascii=False, indent=2)
    return target


def _summary_for_report(doc: ParsedDocument) -> dict:
    headings = sum(1 for b in doc.blocks if b.type == "heading")
    rows = sum(1 for b in doc.blocks if b.type == "table_row")
    return {
        "doc_id": doc.doc_id,
        "source_path": doc.source_path,
        "quality_label": doc.quality_label,
        "page_count": doc.page_count,
        "block_count": len(doc.blocks),
        "heading_count": headings,
        "table_row_count": rows,
        "warnings": doc.warnings,
        "status": doc.status,
    }


def parse_directory(root: str | Path, output_dir: str | Path) -> list[dict]:
    """Parse alle PDFs recursief onder `root` en schrijf JSON + parse_report.json."""
    root_p = Path(root)
    out_p = Path(output_dir)
    out_p.mkdir(parents=True, exist_ok=True)

    pdf_paths = sorted(root_p.rglob("*.pdf"))
    reports: list[dict] = []
    for pdf_path in pdf_paths:
        # quality_label = naam van de directe oudermap onder de root.
        try:
            relative = pdf_path.relative_to(root_p)
            quality_label = relative.parts[0] if len(relative.parts) > 1 else None
        except ValueError:
            quality_label = None

        doc = parse_pdf(pdf_path, quality_label=quality_label)
        write_parsed(doc, out_p)
        reports.append(_summary_for_report(doc))

    report_path = out_p / "parse_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    return reports
