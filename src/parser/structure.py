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


# TOC-titels en fuzzy match 


def _extract_toc_titles(elements: list[RawElement]) -> list[str]:
    """Strip dots/puntjes + paginanummer aan einde; geef opgeschoonde titels."""
    titels: list[str] = []
    for el in elements:
        if el.column_count is not None:
            continue
        text = el.tekst.strip()
        # Verwijder trailing dots + getal.
        opgeschoond = re.sub(r"[\.\s]*\.{2,}\s*\d{1,4}\s*$", "", text).strip()
        opgeschoond = re.sub(r"\s+\d{1,4}\s*$", "", opgeschoond).strip()
        opgeschoond = re.sub(r"\s+", " ", opgeschoond)
        if opgeschoond:
            titels.append(opgeschoond.lower())
    return titels


def _is_in_toc(text: str, toc_titles: list[str], threshold: float = 0.85) -> bool:
    if not toc_titles:
        return False
    target = re.sub(r"\s+", " ", text.strip().lower())
    for t in toc_titles:
        if SequenceMatcher(None, target, t).ratio() >= threshold:
            return True
    return False


# Token-schatting 


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


#  kandidaatregels samenvoegen 


def _merge_candidate_lines(
    elements: list[RawElement],
    y_tol: float = 2.0,
) -> list[RawElement]:
    if not elements:
        return []

    merged: list[RawElement] = []
    i = 0
    while i < len(elements):
        cur = elements[i]
        if cur.column_count is not None:
            merged.append(cur)
            i += 1
            continue
        groep = [cur]
        j = i + 1
        while j < len(elements):
            other = elements[j]
            if other.pagina != cur.pagina or other.column_count is not None:
                break
            if abs(other.y0 - cur.y0) > y_tol:
                break
            groep.append(other)
            j += 1
        if len(groep) == 1:
            merged.append(cur)
        else:
            ordered = sorted(groep, key=lambda e: e.x0)
            text = " ".join(e.tekst for e in ordered)
            sizes = [e.lettergrootte for e in ordered if e.lettergrootte is not None]
            vetten = [e.vet for e in ordered if e.vet is not None]
            merged.append(
                RawElement(
                    tekst=text,
                    pagina=cur.pagina,
                    x0=min(e.x0 for e in ordered),
                    y0=min(e.y0 for e in ordered),
                    x1=max(e.x1 for e in ordered),
                    y1=max(e.y1 for e in ordered),
                    lettergrootte=(sum(sizes) / len(sizes)) if sizes else None,
                    vet=any(vetten) if vetten else None,
                )
            )
        i = j
    return merged

#  Probleem : herhalende header/footer-noise 

def _normalize_for_compare(text: str) -> str:
    s = re.sub(r"\d+", "", text)
    return re.sub(r"\s+", " ", s).strip().lower()


def _detect_repeated_noise(
    elements: list[RawElement],
    band: float = 0.07,
    edit_distance_threshold: float = 0.8,
    minimaal_andere_paginas: int = 2,
) -> set[int]:
    """Mark elementen als noise wanneer ze in een header/footer-positie
    staan én op ≥ 2 andere pagina's met vergelijkbare tekst voorkomen
    (probleem 1)."""
    if not elements:
        return set()

    per_pagina: dict[int, list[tuple[int, RawElement]]] = defaultdict(list)
    for i, el in enumerate(elements):
        if el.column_count is not None:
            continue
        per_pagina[el.pagina].append((i, el))

    if len(per_pagina) < 3:
        return set()

    page_heights: dict[int, float] = {
        p: max(el.y1 for _, el in els) for p, els in per_pagina.items()
    }

    candidates_per_page: dict[int, list[tuple[int, RawElement]]] = {}
    for p, els in per_pagina.items():
        h = page_heights[p]
        top_grens = h * band
        bot_grens = h * (1 - band)
        candidates_per_page[p] = [
            (i, el) for i, el in els
            if el.y1 <= top_grens or el.y0 >= bot_grens
        ]

    noise: set[int] = set()
    pagina_lijst = sorted(per_pagina.keys())

    for p in pagina_lijst:
        for cand_i, cand_el in candidates_per_page.get(p, []):
            cand_norm = _normalize_for_compare(cand_el.tekst)
            if not cand_norm:
                continue
            other_matches = 0
            for other_p in pagina_lijst:
                if other_p == p:
                    continue
                gevonden = False
                for _, other_el in candidates_per_page.get(other_p, []):
                    other_norm = _normalize_for_compare(other_el.tekst)
                    if not other_norm:
                        continue
                    if SequenceMatcher(None, cand_norm, other_norm).ratio() >= edit_distance_threshold:
                        gevonden = True
                        break
                if gevonden:
                    other_matches += 1
                    if other_matches >= minimaal_andere_paginas:
                        break
            if other_matches >= minimaal_andere_paginas:
                noise.add(cand_i)
    return noise


#  Probleem : pagina-niveau front_matter (50% rule) 


def _detect_body_pages(
    elements: list[RawElement],
    toc_titles: list[str],
) -> set[int]:
    """Verbeterpunt 1: een pagina is een body-pagina als er een element op
    staat dat (fuzzy) matcht met een TOC-entry. Op zulke pagina's mag de
    pagina-drempel-uitbreiding geen extra front_matter labelen."""
    body: set[int] = set()
    if not toc_titles:
        return body
    for el in elements:
        if el.column_count is not None:
            continue
        text = el.tekst.strip()
        if not text:
            continue
        if _is_in_toc(text, toc_titles, threshold=0.85):
            body.add(el.pagina)
    return body


def _expand_front_matter_by_page(
    elements: list[RawElement],
    fm_flags: list[bool],
    toc_pages: set[int] | None = None,
    body_pages: set[int] | None = None,
    drempel: float = 0.5,
    korte_regel_woorden: int = 12,
    max_paginas: int = 2,
) -> list[bool]:
    """Verbeterpunt 1: uitbreiden mag op pagina 1-2 met guards.

    - Skip TOC-pagina's (die zijn al volledig fm via een ander pad).
    - Skip body-pages (echte content; niet platslaan).
    - Lopende zinnen worden in elk geval niet bijgelabeld.
    """
    if not elements:
        return fm_flags
    toc_pages = toc_pages or set()
    body_pages = body_pages or set()

    per_pagina: dict[int, list[int]] = defaultdict(list)
    for i, el in enumerate(elements):
        per_pagina[el.pagina].append(i)

    new_flags = list(fm_flags)
    for p, idxs in per_pagina.items():
        if p > max_paginas:
            continue
        if p in toc_pages or p in body_pages:
            continue
        if not idxs:
            continue
        fm_count = sum(1 for i in idxs if new_flags[i])
        if fm_count / len(idxs) <= drempel:
            continue
        for i in idxs:
            if new_flags[i]:
                continue
            tekst = elements[i].tekst.strip()
            if _is_running_sentence(tekst):
                continue
            woorden = tekst.split()
            if len(woorden) < korte_regel_woorden:
                new_flags[i] = True
    return new_flags


# Probleem : tabel-header detectie 


_FUNCTIE_WOORDEN = {
    "de", "het", "een", "die", "deze", "dit", "deze", "voor", "naar",
    "om", "en", "of", "maar", "want", "als", "dat", "dan", "ook",
    "the", "a", "an", "of", "for", "to", "and", "or", "but",
}


def _looks_like_header(cells: list[str]) -> bool:
    if not cells:
        return False
    if not all(c.strip() for c in cells):
        return False
    # Geen cell met alleen getallen.
    for c in cells:
        stripped = re.sub(r"[\s.,]", "", c)
        if stripped and stripped.isdigit():
            return False
    # Alle cellen < 5 woorden.
    if not all(len(c.split()) < 5 for c in cells):
        return False
    # Geen cel mag beginnen met een lowercase letter of typisch functiewoord
    for c in cells:
        first_word = c.strip().split()[0] if c.strip() else ""
        if first_word and first_word[0].islower():
            return False
        if first_word.lower() in _FUNCTIE_WOORDEN:
            return False
    return True


# Onzichtbare karakters die de LLM downstream verwarren.
_INVISIBLE_CHARS = re.compile(r"[-‍⁠﻿­]")


def _normalize_cell(text: str) -> str:
    """Schoonmaakroutine per tabelcel:
      - vervangt zero-width / soft-hyphen tekens door niets
      - vervangt interne newlines en tabs door spaties
      - collapse meerdere spaties tot één
      - strip
    """
    if not text:
        return ""
    cleaned = _INVISIBLE_CHARS.sub("", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _is_index_cell(cell: str | None) -> bool:
    """True als de cel alleen een paginanummer / volgnummer bevat (puur getal)."""
    if not cell:
        return False
    stripped = cell.strip()
    return bool(stripped) and stripped.replace(".", "").isdigit()


def _build_table_from_text(
    text: str,
    column_count: int | None,
) -> tuple[list[list[str | None]], list[list[int]], str, int]:
    """Parse + normaliseer het tabelblok-text.

    Geeft (cells, index_cells, canonical_text, kept_row_count) terug.
    """
    rijen: list[list[str | None]] = []
    for row_text in text.split("\n"):
        if not row_text.strip():
            continue
        ruwe_cellen = [c.strip() for c in row_text.split("|")]
        cells_norm: list[str | None] = []
        for c in ruwe_cellen:
            c_clean = _normalize_cell(c)
            cells_norm.append(c_clean if c_clean else None)
        if all(c is None for c in cells_norm):
            continue
        rijen.append(cells_norm)

    index_cells: list[list[int]] = []
    for r_idx, row in enumerate(rijen):
        for c_idx, c in enumerate(row):
            if _is_index_cell(c):
                index_cells.append([r_idx, c_idx])

    canonical = "\n".join(
        " | ".join("" if c is None else c for c in row)
        for row in rijen
    )
    return rijen, index_cells, canonical, len(rijen)


def _table_meta_from_text(text: str, column_count: int | None) -> tuple[dict, str]:
    """Genereer table_meta + canonical text op basis van het ruwe tabelblok-tekst.

    Returnt (meta, canonical_text). De caller schrijft `canonical_text` terug naar
    het Block.text-veld zodat tabel-formatting consistent is.
    """
    cells, index_cells, canonical_text, row_count = _build_table_from_text(text, column_count)
    header_row: str | None = None
    if cells:
        eerste = [c if c is not None else "" for c in cells[0]]
        if _looks_like_header(eerste):
            header_row = " | ".join(eerste)
    return (
        {
            "row_count": row_count,
            "column_count": column_count,
            "header_row": header_row,
            "cells": cells,
            "index_cells": index_cells,
        },
        canonical_text,
    )


#  Classificatie helpers 


def _classify_block_type(
    element: RawElement,
    in_appendix: bool,
    in_front_matter: bool,
    is_template_flag: bool,
    is_noise_flag: bool,
) -> BlockType:
    text = element.tekst.strip()
    if is_noise_flag:
        return "noise"
    if element.column_count is not None:
        return "table"
    if in_front_matter:
        return "front_matter"
    if is_template_flag:
        return "template"
    if _CAPTION_PREFIX.match(text):
        return "caption"
    if _BULLET_PREFIX.match(text):
        return "bullet"
    return "paragraph"


#  Block-bouw 


def build_blocks(raw_elements: list[RawElement], doc_id: str) -> list[Block]:
    """Volgorde:
       1. is_noise (discard) → triviale ruis weg
       2. _merge_candidate_lines → gesplitste regels samenvoegen
       3. _detect_repeated_noise → herhalende header/footer als noise
       4. is_toc_page detecteren → hele pagina front_matter
       5. is_front_matter per element + 50% pagina-expansie
       6. is_template (strict)
       7. tabel-rijen samenvoegen (met behoud van flags)
       8. classificeren + heading_path via level-stack, met TOC/FM-guard
    """
    
    schoon = [el for el in raw_elements if not is_noise(el)]

   
    schoon = _merge_candidate_lines(schoon)

   
    noise_indices = _detect_repeated_noise(schoon)

   
    per_pagina: dict[int, list[int]] = defaultdict(list)
    for i, el in enumerate(schoon):
        per_pagina[el.pagina].append(i)

    toc_pages: set[int] = set()
    for p, idxs in per_pagina.items():
        page_els = [schoon[i] for i in idxs]
        if is_toc_page(page_els):
            toc_pages.add(p)

    # TOC-titels extraheren voor latere fuzzy-match in heading-check.
    toc_elements = [schoon[i] for p in toc_pages for i in per_pagina[p]]
    toc_titles = _extract_toc_titles(toc_elements)

    # Paginagemiddelde lettergrootte (voor strict heading-check).
    page_avg: dict[int, float | None] = {}
    for p, idxs in per_pagina.items():
        sizes = [
            schoon[i].lettergrootte for i in idxs
            if schoon[i].lettergrootte is not None and schoon[i].column_count is None
        ]
        page_avg[p] = (sum(sizes) / len(sizes)) if sizes else None

    # 5. Front_matter per element. Elementen op TOC-pagina's zijn altijd fm.
    fm_flags: list[bool] = []
    for i, el in enumerate(schoon):
        if el.pagina in toc_pages:
            fm_flags.append(True)
        else:
            fm_flags.append(is_front_matter(el, el.pagina))

    # Verbeterpunt : identificeer body-pages (pagina's met een TOC-heading).
    body_pages = _detect_body_pages(schoon, toc_titles)

    fm_flags = _expand_front_matter_by_page(
        schoon,
        fm_flags,
        toc_pages=toc_pages,
        body_pages=body_pages,
        drempel=0.5,
    )

    # 6. Template-flags (na fm, omdat fm wint).
    template_flags = [is_template(el) for el in schoon]

    # 7. Tabel-rijen samenvoegen met behoud van flags.
    samengevoegd: list[RawElement] = []
    sm_fm: list[bool] = []
    sm_tpl: list[bool] = []
    sm_noise: list[bool] = []
    sm_toc: list[bool] = []
    huidige: list[int] = []

    def _flush() -> None:
        if not huidige:
            return
        if len(huidige) == 1:
            i = huidige[0]
            samengevoegd.append(schoon[i])
            sm_fm.append(fm_flags[i])
            sm_tpl.append(template_flags[i])
            sm_noise.append(i in noise_indices)
            sm_toc.append(schoon[i].pagina in toc_pages)
        else:
            groep_els = [schoon[i] for i in huidige]
            tekst = "\n".join(e.tekst for e in groep_els)
            first = groep_els[0]
            last = groep_els[-1]
            samengevoegd.append(
                RawElement(
                    tekst=tekst,
                    pagina=first.pagina,
                    x0=min(e.x0 for e in groep_els),
                    y0=first.y0,
                    x1=max(e.x1 for e in groep_els),
                    y1=last.y1,
                    lettergrootte=None,
                    vet=None,
                    column_count=first.column_count,
                )
            )
            sm_fm.append(fm_flags[huidige[0]])
            sm_tpl.append(template_flags[huidige[0]])
            sm_noise.append(huidige[0] in noise_indices)
            sm_toc.append(first.pagina in toc_pages)
        huidige.clear()

    for i, el in enumerate(schoon):
        if el.column_count is None:
            _flush()
            samengevoegd.append(el)
            sm_fm.append(fm_flags[i])
            sm_tpl.append(template_flags[i])
            sm_noise.append(i in noise_indices)
            sm_toc.append(el.pagina in toc_pages)
            continue
        if not huidige:
            huidige.append(i)
            continue
        vorige_i = huidige[-1]
        vorige_el = schoon[vorige_i]
        rijhoogte = max(1.0, vorige_el.y1 - vorige_el.y0)
        gap = el.y0 - vorige_el.y1
        if (
            vorige_el.pagina == el.pagina
            and vorige_el.column_count == el.column_count
            and gap < 1.8 * rijhoogte
        ):
            huidige.append(i)
        else:
            _flush()
            huidige.append(i)
    _flush()

    