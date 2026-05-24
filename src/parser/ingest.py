"""Ingest-laag: opent een PDF en levert ruwe RawElement-objecten.

Doel: alleen extractie. Geen classificatie, geen ruisfiltering,
geen heading-detectie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import pdfplumber


# --- Datamodel 


@dataclass
class RawElement:
    tekst: str
    pagina: int
    x0: float
    y0: float
    x1: float
    y1: float
    lettergrootte: float | None = None
    vet: bool | None = None
    # Pragmatische uitbreiding: aantal kolommen bij tabel-rijen (None = geen tabel).
    column_count: int | None = None


# --- PDF openen 


def open_pdf(pad: str):
    """Wrapper rond pdfplumber.open; returnt het pdfplumber.PDF-object.

    De caller is verantwoordelijk voor sluiten (with-statement).
    """
    return pdfplumber.open(pad)


# --- Words en tabellen


def extract_words(page) -> list[dict]:
    """Geef de woorden van een pagina terug, inclusief font-attributen."""
    try:
        return page.extract_words(extra_attrs=["size", "fontname"]) or []
    except Exception:
        # Fallback zonder extra attrs als pdfplumber faalt op specifieke fonts.
        return page.extract_words() or []


# Strengere tabel-detectie: alleen tabellen waar daadwerkelijk lijnen zichtbaar
# zijn. De pdfplumber-default valt ook terug op whitespace-strategieën en
# detecteert dan willekeurige tekstkolommen als 'tabel' (false positives op
# grafisch-zware PDFs zoals Canva-exports).
_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
}


def extract_tables(page) -> list[list[list[str]]]:
    """Geef gevonden tabellen terug als list[rows[cells]]."""
    try:
        return page.extract_tables(table_settings=_TABLE_SETTINGS) or []
    except Exception:
        return []


# --- Helpers 


def _is_bold(fontname: str | None) -> bool | None:
    if not fontname:
        return None
    return "bold" in fontname.lower()


def _group_words_to_lines(
    words: list[dict],
    y_tolerantie: float = 3.0,
) -> list[list[dict]]:
    """Groepeer woorden naar regels op basis van de y-positie."""
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    current: list[dict] = [sorted_words[0]]
    for w in sorted_words[1:]:
        if abs(w["top"] - current[0]["top"]) <= y_tolerantie:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
    lines.append(sorted(current, key=lambda x: x["x0"]))
    return lines


def _word_in_bbox(
    w: dict,
    bbox: tuple[float, float, float, float],
    tolerance: float = 5.0,
) -> bool:
    """True als het centrum van het woord binnen `bbox` (met marge) valt.

    De marge voorkomt dat tekst direct grenzend aan een tabelrand dubbel als
    paragraph én table-rij in de output verschijnt.
    """
    x0, top, x1, bottom = bbox
    cx = (w.get("x0", 0) + w.get("x1", 0)) / 2
    cy = (w.get("top", 0) + w.get("bottom", 0)) / 2
    return (
        (x0 - tolerance) <= cx <= (x1 + tolerance)
        and (top - tolerance) <= cy <= (bottom + tolerance)
    )


def _line_to_raw_element(line_words: list[dict], page_number: int) -> RawElement | None:
    text = " ".join(w["text"] for w in line_words).strip()
    if not text:
        return None
    x0 = min(w["x0"] for w in line_words)
    x1 = max(w["x1"] for w in line_words)
    y0 = min(w["top"] for w in line_words)
    y1 = max(w["bottom"] for w in line_words)

    sizes = [w.get("size") for w in line_words if w.get("size") is not None]
    lettergrootte = median(sizes) if sizes else None

    fontnames = [w.get("fontname") for w in line_words if w.get("fontname")]
    if fontnames:
        bold_hits = sum(1 for fn in fontnames if "bold" in fn.lower())
        vet = bold_hits >= max(1, len(fontnames) // 2)
    else:
        vet = None

    return RawElement(
        tekst=text,
        pagina=page_number,
        x0=float(x0),
        y0=float(y0),
        x1=float(x1),
        y1=float(y1),
        lettergrootte=lettergrootte,
        vet=vet,
    )


def _is_real_table(table) -> bool:
    """True als de tabel er echt tabulair uitziet, geen styled tekstbox.

    Eisen:
      - minstens 2 rijen
      - minstens 2 kolommen in op zijn minst één rij
      - gemiddelde cel-lengte onder ~200 chars (anders is het een prose-blok)
    """
    try:
        rows = table.extract() or []
    except Exception:
        return False
    if len(rows) < 2:
        return False
    max_cols = max(
        (sum(1 for c in row if c and c.strip()) for row in rows),
        default=0,
    )
    if max_cols < 2:
        return False
    all_cells = [c for row in rows for c in row if c and c.strip()]
    if not all_cells:
        return False
    avg_len = sum(len(c) for c in all_cells) / len(all_cells)
    if avg_len > 200:
        return False
    return True


def _format_cell(cell: str | None) -> str:
    """Maak één cel single-line en collapse whitespace.

    Cell-internal linebreaks zouden anders niet te onderscheiden zijn van
    de row-separator `\\n` die we later in structure.py gebruiken.
    """
    if not cell:
        return ""
    return re.sub(r"\s+", " ", cell).strip()


def _drop_empty_columns(rows: list[list[str]]) -> list[list[str]]:
    """Verwijder kolommen waar elke rij leeg is."""
    if not rows:
        return rows
    max_cols = max(len(r) for r in rows)
    padded = [r + [""] * (max_cols - len(r)) for r in rows]
    keep = [
        i for i in range(max_cols)
        if any(row[i].strip() for row in padded)
    ]
    return [[row[i] for i in keep] for row in padded]


def _table_rows_to_raw_elements(
    table,
    page_number: int,
) -> list[RawElement]:
    """Zet een pdfplumber-Table om naar één RawElement per rij.

    Voert tegelijk drie schoonmaakstappen uit:
      - cell-internal whitespace collapsen (geen `\\n` meer binnen één cel)
      - lege rijen overslaan
      - kolommen waar elke rij leeg is verwijderen
    """
    try:
        raw_rows = table.extract() or []
    except Exception:
        return []
    if not raw_rows:
        return []

    cleaned = [[_format_cell(c) for c in row] for row in raw_rows]
    cleaned = _drop_empty_columns(cleaned)

    x0, top, x1, bottom = table.bbox
    n_rijen = max(1, len(cleaned))
    rij_hoogte = (bottom - top) / n_rijen
    elements: list[RawElement] = []
    for i, row in enumerate(cleaned):
        non_empty = [c for c in row if c]
        if not non_empty:
            continue
        text = " | ".join(non_empty)
        y_top = top + i * rij_hoogte
        y_bot = y_top + rij_hoogte
        elements.append(
            RawElement(
                tekst=text,
                pagina=page_number,
                x0=float(x0),
                y0=float(y_top),
                x1=float(x1),
                y1=float(y_bot),
                lettergrootte=None,
                vet=None,
                column_count=len(row),
            )
        )
    return elements


# --- Hoofdfunctie 


def extract_raw_elements(pad: str) -> list[RawElement]:
    """Lees een PDF in en lever een platte lijst van RawElements op.

    Volgorde per pagina:
      1. Tabellen vinden + bboxes verzamelen.
      2. Words extraheren, woorden in tabel-bbox uitfilteren.
      3. Overige woorden groeperen naar regels → RawElement per regel.
      4. Tabellen toevoegen: één RawElement per rij (met column_count).
    """
    elements: list[RawElement] = []
    p = Path(pad)
    if not p.exists():
        raise FileNotFoundError(f"PDF niet gevonden: {pad}")

    with open_pdf(str(p)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            # Tabellen detecteren via find_tables (geeft bbox + extract).
            try:
                tables = page.find_tables(table_settings=_TABLE_SETTINGS)
            except Exception:
                tables = []
            # Twee sanity-checks: (1) tabellen die > 85% van de pagina bedekken
            # zijn vrijwel zeker false positives, (2) styled tekstboxen die als
            # tabel gedetecteerd worden, weren via _is_real_table.
            page_area = max(1.0, float(page.width) * float(page.height))
            tables = [
                t for t in tables
                if ((t.bbox[2] - t.bbox[0]) * (t.bbox[3] - t.bbox[1])) / page_area < 0.85
                and _is_real_table(t)
            ]
            table_bboxes = [t.bbox for t in tables]

            # Woorden ophalen en woorden binnen tabel-regio's uitsluiten.
            words = extract_words(page)
            if table_bboxes:
                words = [
                    w for w in words
                    if not any(_word_in_bbox(w, b) for b in table_bboxes)
                ]

            # Regels opbouwen uit overige woorden.
            for line_words in _group_words_to_lines(words):
                el = _line_to_raw_element(line_words, page_number=i)
                if el is not None:
                    elements.append(el)

            # Tabellen omzetten naar rij-RawElements.
            for table in tables:
                elements.extend(_table_rows_to_raw_elements(table, page_number=i))

    return elements
