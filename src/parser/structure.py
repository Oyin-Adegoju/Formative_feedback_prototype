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


def is_front_matter(element: RawElement, page_no: int) -> bool:
    """Front-matter-detectie zonder doc-context (TOC-titels worden hier
    niet gechecked; dat doen we in `build_blocks`).

    Vangt: TOC-dots, expliciete keywords, korte regels op p1-p2 met
    studentnummer/datum/versie/veldlabel, namenlijsten, module-keywords.
    """
    text = element.tekst.strip()
    if not text:
        return True

    # Verbeterpunt 1 — harde stop: een lopende zin is nooit front_matter,
    # ook niet op pagina 1-2.
    if _is_running_sentence(text):
        return False

    if _TOC_DOTS.search(text):
        return True
    if text.lower() in _TOC_KEYWORDS:
        return True

    if page_no <= 2:
        woorden = text.split()
        # Korte regels op p1-p2 die geen lopende zin lijken.
        kort = len(woorden) < 12
        eindigt_op_punt = text.endswith(".") and len(woorden) > 3

        if _STUDENTNR.search(text):
            return True
        if _DATUM.search(text) and len(woorden) < 8:
            return True
        if _VERSIE.search(text) and len(woorden) < 8:
            return True
        if _VELDLABEL.match(text):
            return True
        if _NAMENLIJST.match(text):
            return True
        if _MODULE_OPLEIDING_KW.search(text) and kort:
            return True
        # Heel korte regels op de titelpagina als fallback.
        if kort and not eindigt_op_punt and len(woorden) < 4:
            return True

    return False


def is_noise(element: RawElement) -> bool:
    """Discard-predicate (paginanummers, eenvoudige decoratie)."""
    text = element.tekst.strip()
    if not text:
        return True
    if _PAGE_NUMBER_ONLY.match(text):
        return True
    if len(text) <= 2 and not text.isalnum():
        return True
    return False


def is_toc_page(page_elements: list[RawElement]) -> bool:
    """True als een pagina ≥ 5 regels heeft die eruitzien als TOC-entries.

    TOC-entry = regel die eindigt op (puntjes + ) paginanummer.
    """
    if not page_elements:
        return False
    matches = 0
    for el in page_elements:
        if el.column_count is not None:
            continue
        if _TOC_LINE.search(el.tekst.strip()):
            matches += 1
            if matches >= 5:
                return True
    return matches >= 5


def is_template(element: RawElement) -> bool:
    """Strict template-/placeholder-detectie (probleem 3 + 8 + verbeterpunt 2)."""
    text = element.tekst.strip()
    if not text or element.column_count is not None:
        return False

    # Verbeterpunt 2: minimale lengte — < 3 tekens is geen template
    # (te kort om als instructie te tellen). Wel mogelijk noise verderop.
    if len(text) < 3:
        return False

    # Verbeterpunt 2: bevat het woord "placeholder" (case-insensitive).
    if "placeholder" in text.lower():
        return True

    # a) Volledig tussen [ ] of < >
    if _TEMPLATE_BRACKETS.match(text):
        return True

    # c) Alleen puntjes / underscores / streepjes (≥ 5 tekens)
    if _TEMPLATE_ONLY_FILL.match(text):
        return True

    # Verbeterpunt 2: ALL-CAPS-woorden met dubbele punt, zonder inhoud.
    if _TEMPLATE_ALLCAPS_COLON.match(text):
        return True

    #  "Label : ___" / "Label : ..." met alleen filler.
    if _TEMPLATE_LABEL_FILLER.match(text):
        return True

    # Expliciete tags (probleem 8)
    if _TEMPLATE_TAG.match(text):
        return True

    # b + d) Instructiezinnen met expliciete placeholderwoorden
    if _TEMPLATE_PHRASES.search(text):
        return True

    #korte instructie- of placeholderregels herkennen zodat de parser het als template behandelt
    if _IMPERATIVE_VERBS.match(text):
        woorden = text.split()
        if len(woorden) <= 8:
            rest = " ".join(woorden[1:])
            second_verb = bool(_VERB_HINTS.search(rest))
            if not second_verb:
                return True

    # Korte labelregel zoals TODO:, NB: of Voorbeeld:
    if _TEMPLATE_LABEL_PREFIX.match(text):
        if len(text.split()) <= 6:
            return True

    return False

# Tabellen samenvoegen 


def merge_table_rows(elements: list[RawElement]) -> list[RawElement]:
    """Voeg opeenvolgende tabelrijen samen die bij dezelfde tabel horen."""
    if not elements:
        return []

    result: list[RawElement] = []
    huidige_groep: list[RawElement] = []

    def _flush() -> None:
        if not huidige_groep:
            return
        if len(huidige_groep) == 1:
            result.append(huidige_groep[0])
        else:
            samengevoegd = "\n".join(e.tekst for e in huidige_groep)
            first = huidige_groep[0]
            last = huidige_groep[-1]
            result.append(
                RawElement(
                    tekst=samengevoegd,
                    pagina=first.pagina,
                    x0=min(e.x0 for e in huidige_groep),
                    y0=first.y0,
                    x1=max(e.x1 for e in huidige_groep),
                    y1=last.y1,
                    lettergrootte=None,
                    vet=None,
                    column_count=first.column_count,
                )
            )
        huidige_groep.clear()

    for el in elements:
        if el.column_count is None:
            _flush()
            result.append(el)
            continue
        if not huidige_groep:
            huidige_groep.append(el)
            continue

        vorige = huidige_groep[-1]
        rijhoogte = max(1.0, vorige.y1 - vorige.y0)
        gap = el.y0 - vorige.y1
        if (
            vorige.pagina == el.pagina
            and vorige.column_count == el.column_count
            and gap < 1.8 * rijhoogte
        ):
            huidige_groep.append(el)
        else:
            _flush()
            huidige_groep.append(el)
    _flush()
    return result

# Heading-path 


def build_heading_path(
    active_headings: list[str],
    new_heading: str,
    level: int,
) -> list[str]:
    """Pure helper: truncate naar level-1, push new_heading."""
    new_path = active_headings[: max(0, level - 1)]
    new_path.append(new_heading)
    return new_path


class _LevelStack:
    """Expliciete (level, tekst)-stack voor heading_path (probleem 4)."""

    def __init__(self) -> None:
        self._entries: list[tuple[int, str]] = []

    def push(self, level: int | None, text: str) -> list[str]:
        if level is None:
            level = self._entries[-1][0] if self._entries else 1
        while self._entries and self._entries[-1][0] >= level:
            self._entries.pop()
        self._entries.append((level, text))
        return self.path()

    def path(self) -> list[str]:
        return [t for _, t in self._entries]


