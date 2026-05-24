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

# Template-patronen (probleem 3 + 8): strikt
_TEMPLATE_BRACKETS = re.compile(r"^\s*[\[<].*[\]>]\s*$")
_TEMPLATE_ONLY_FILL = re.compile(r"^\s*[\._\-]{5,}\s*$")
_TEMPLATE_PHRASES = re.compile(
    r"("
    r"vul\s+(hier\s+)?in|"
    r"beschrijf\s+hier|"
    r"geef\s+hier\s+aan|"
    r"voeg\s+hier\s+toe|"
    r"noteer\s+hier|"
    r"vermeld\s+hier|"
    r"hier\s+komt|"
    r"dit\s+is\s+een\s+voorbeeld"
    r")",
    re.IGNORECASE,
)
_TEMPLATE_LABEL_PREFIX = re.compile(
    r"^\s*(voorbeeld|toelichting|opmerking|nb|todo|fixme|bijv|e\.g)\s*[:.\-]",
    re.IGNORECASE,
)
_TEMPLATE_TAG = re.compile(r"^\s*\[(todo|fixme|concept|placeholder)\]\s*$", re.IGNORECASE)
_TEMPLATE_TRAILING_ELLIPSIS = re.compile(r"\.{3}\s*$")

# Verbeterpunt 2: extra template-signalen.
# ALL CAPS-woorden met dubbele punt, zonder verdere inhoud: "NAAM:" 
_TEMPLATE_ALLCAPS_COLON = re.compile(r"^\s*[A-Z]{2,}(\s+[A-Z]{2,})*\s*:\s*$")
# "Label : ___" of "Label : ..." waarbij na de label alleen filler staat.
_TEMPLATE_LABEL_FILLER = re.compile(
    r"^\s*[A-Za-z][A-Za-z\s\-]{0,40}\s*[:=]\s*[\._\-]{3,}\s*$"
)
# Imperatief: eerste woord is gebiedende wijs, korte regel zonder vorm-onderwerp.
_IMPERATIVE_VERBS = re.compile(
    r"^\s*(beschrijf|vul|geef|voeg|noteer|vermeld|plaats|zet|schrijf|maak|bedenk)\b",
    re.IGNORECASE,
)

# Verbeterpunt 1: werkwoord-hint voor de "lopende-zin"-detectie.
_VERB_HINTS = re.compile(
    r"\b("
    r"is|zijn|ben|bent|was|waren|wordt|werd|worden|"
    r"heb|hebt|heeft|hebben|had|hadden|"
    r"kan|kun|kunt|kunnen|kon|konden|"
    r"moet|moeten|moest|moesten|"
    r"mag|mogen|mocht|mochten|"
    r"zal|zullen|zou|zouden|"
    r"wil|willen|wilde|wilden|wou|"
    r"doe|doet|doen|deed|deden|"
    r"ga|gaat|gaan|ging|gingen|"
    r"komt|kom|komen|kwam|kwamen|"
    r"maakt|maak|maken|maakte|maakten|"
    r"streeft|streven|"
    r"bevat|bevatten|"
    r"are|is|was|were|be|been|being|"
    r"have|has|had|having|"
    r"do|does|did|done|"
    r"will|would|shall|should|"
    r"can|could|may|might|must"
    r")\b",
    re.IGNORECASE,
)


def _is_running_sentence(text: str) -> bool:
    """Een lopende zin: > 10 woorden EN bevat een werkwoord-hint."""
    woorden = text.split()
    if len(woorden) <= 10:
        return False
    return bool(_VERB_HINTS.search(text))

# Classificatie


def is_heading(element: RawElement) -> bool:
    """Heading-detectie zonder document-context.

    Wordt door tests en losse calls gebruikt. In `build_blocks` gebruiken we
    `_is_heading_in_ctx` met paginagemiddeldes en TOC-titels voor de
    strikte numbered-heading-check (probleem 5).
    """
    text = element.tekst.strip()
    if not text or element.column_count is not None:
        return False

    if _H_PREFIX.match(text):
        return True

    m = _NUMBERED_HEADING.match(text)
    if m:
        return _passes_numbered_heading(element, m, page_avg=None, in_toc=False)

    if element.lettergrootte is not None and len(text) <= 80:
        if element.lettergrootte >= 14:
            return True
        if element.lettergrootte >= 12 and element.vet:
            return True

    woorden = text.split()
    if (
        text.isupper()
        and 2 <= len(woorden) <= 8
        and len(text) <= 60
        and text[-1] not in _ZIN_EINDE
    ):
        return True

    return False


def _passes_numbered_heading(
    element: RawElement,
    match: re.Match,
    page_avg: float | None,
    in_toc: bool,
) -> bool:
    """Strikte test voor genummerde regels als heading (probleem 5)."""
    text = element.tekst.strip()
    number = match.group(1)
    woorden = text.split()

    # Hard exclusies.
    if text[-1] in ".,;":
        return False
    if len(woorden) < 2 and not in_toc:
        return False

 # Hiërarchische nummering zoals "1.1" of "1.1.1" is meestal een sectiekop,
 # daarom accepteren we die direct tenzij een hard-exclude geldt.
    if number.count(".") >= 1:
        return True

    # Single-level genummerd ("1. Foo"): TOC-match is voldoende.
    if in_toc:
        return True

    # Zonder TOC-match: lettergrootte boven paginagemiddelde is noodzakelijk
    font_larger = (
        page_avg is not None
        and element.lettergrootte is not None
        and element.lettergrootte > page_avg + 0.5
    )
    if not font_larger:
        return False

    # En minimaal één ondersteunende eigenschap (vet of geen zinseinde).
    extras = 0
    if element.vet:
        extras += 1
    if text[-1] not in _ZIN_EINDE:
        extras += 1
    return extras >= 1

def _is_heading_in_ctx(
    element: RawElement,
    page_avg: float | None,
    toc_titles: list[str],
) -> bool:
    """Heading-detectie met document-context (paginagemiddelde, TOC-titels)."""
    text = element.tekst.strip()
    if not text or element.column_count is not None:
        return False

    if _H_PREFIX.match(text):
        return True

    in_toc = _is_in_toc(text, toc_titles)

    m = _NUMBERED_HEADING.match(text)
    if m:
        return _passes_numbered_heading(element, m, page_avg, in_toc)

    # Font-gebaseerd voor niet-numbered: lettergrootte boven gemiddelde.
    if element.lettergrootte is not None and len(text) <= 80:
        if page_avg is not None and element.lettergrootte > page_avg + 1.5:
            return True
        if element.lettergrootte >= 14:
            return True
        if element.lettergrootte >= 12 and element.vet:
            return True

    woorden = text.split()
    if (
        text.isupper()
        and 2 <= len(woorden) <= 8
        and len(text) <= 60
        and text[-1] not in _ZIN_EINDE
    ):
        return True

    return in_toc and len(woorden) >= 2


def _heading_level(element: RawElement) -> int | None:
    text = element.tekst.strip()
    m = _H_PREFIX.match(text)
    if m:
        return int(m.group(1))
    m = _NUMBERED_HEADING.match(text)
    if m:
        return min(4, m.group(1).count(".") + 1)
    if element.lettergrootte is not None:
        if element.lettergrootte >= 18:
            return 1
        if element.lettergrootte >= 15:
            return 2
        if element.lettergrootte >= 13:
            return 3
    return None


