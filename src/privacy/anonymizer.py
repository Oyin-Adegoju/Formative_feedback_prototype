

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace as _dc_replace

from src.parser.structure import Block
from src.privacy.catalog import (
    PeopleCatalog,
    PersonRecord,
    normalize_lookup_text,
)
from src.privacy.rules import find_all_rule_matches
from src.privacy.whitelist import Whitelist


# --- Placeholder-config -----------------------------------------------------


_PLACEHOLDER_PREFIXES: dict[str, str] = {
    "person": "PERSOON",
    "email": "EMAIL",
    "phone": "TEL",
    "student_number": "STUDENTNR",
    "labeled_sensitive": "GEVOELIG",
}

# Mapping van rule_type (uit rules.RuleMatch) naar onze interne ptype.
_RULE_TYPE_TO_PTYPE: dict[str, str] = {
    "email": "email",
    "phone_nl": "phone",
    "student_number": "student_number",
    "labeled_sensitive_field": "labeled_sensitive",
}

# Prioriteit bij gelijke span: lager = belangrijker. Person wint van
# labeled_sensitive zodat "Naam: Sara Denno" als persoon wordt gemarkeerd
# en niet als generic GEVOELIG.
_PTYPE_PRIORITY: dict[str, int] = {
    "person": 0,
    "email": 1,
    "phone": 2,
    "student_number": 3,
    "labeled_sensitive": 4,
}


# --- Datamodel


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    text: str
    ptype: str
    # Sleutel die alle aliassen van dezelfde persoon onder één placeholder
    # groepeert. Voor rule-matches is dit None → de genormaliseerde tekst
    # wordt dan als sleutel gebruikt.
    canonical_key: str | None = None


@dataclass
class AnonymizationState:
    """Houdt mapping en counters bij over de levensduur van één document."""
    _key_to_placeholder: dict[str, str] = field(default_factory=dict)
    _entries: dict[str, dict[str, str]] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def get_or_create(
        self,
        original_text: str,
        ptype: str,
        canonical_key: str | None = None,
    ) -> str:
        """Geef de placeholder voor `original_text` (eerste keer aanmaken)."""
        key = canonical_key if canonical_key is not None else normalize_lookup_text(original_text)
        if not key:
            return original_text  # safety net, geen kapot ID-aanmaak

        if key in self._key_to_placeholder:
            return self._key_to_placeholder[key]

        prefix = _PLACEHOLDER_PREFIXES.get(ptype, ptype.upper())
        self._counters[ptype] = self._counters.get(ptype, 0) + 1
        placeholder = f"[{prefix}_{self._counters[ptype]:02d}]"
        self._key_to_placeholder[key] = placeholder
        self._entries[key] = {
            "original": original_text,
            "placeholder": placeholder,
            "type": ptype,
        }
        return placeholder

    def to_mapping(self) -> dict[str, dict[str, str]]:
        """Mapping van eerst-geziene originele tekst naar {placeholder, type}."""
        return {
            entry["original"]: {
                "placeholder": entry["placeholder"],
                "type": entry["type"],
            }
            for entry in self._entries.values()
        }


# --- Catalogus-matching


def _alias_to_regex(alias: str) -> re.Pattern[str] | None:
    """Bouw een case-insensitive regex met flexibele inter-woord whitespace
    en defensieve woord-/lookahead-grenzen aan beide kanten."""
    if not alias:
        return None
    escaped = re.escape(alias).replace(r"\ ", r"\s+")
    # `\b` werkt niet aan de rand van niet-woord-tekens . In dat geval gebruiken we een negatieve
    # lookaround om alsnog op een echte woordgrens te eindigen.
    prefix = r"\b" if alias[0].isalnum() else r"(?<!\w)"
    suffix = r"\b" if alias[-1].isalnum() else r"(?!\w)"
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def _person_canonical_key(p: PersonRecord) -> str:
    """Genormaliseerde 'identiteit' van een persoon — gebruikt om alle
    aliassen van dezelfde persoon onder dezelfde placeholder te groeperen."""
    parts: list[str] = [p.voornaam]
    if p.tussenvoegsel:
        parts.append(p.tussenvoegsel)
    parts.append(p.achternaam)
    return normalize_lookup_text(" ".join(parts))


def _find_catalog_spans(text: str, catalog: PeopleCatalog) -> list[_Span]:
    """Scan de tekst op alle aliassen uit de catalogus.

    PoC-aanpak: iteratie over alle aliassen, gesorteerd op lengte aflopend,
    elk vertaald naar een regex. Aliassen die door meerdere personen
    gedeeld worden (ambigu) worden bewust **niet** vervangen — dat zou
    onmogelijk te attribueren namen aan willekeurige placeholders koppelen.
    """
    if not text:
        return []

    aliases_seen: set[str] = set()
    aliases: list[str] = []
    for p in catalog.get_all_people():
        for alias in p.aliassen:
            norm = normalize_lookup_text(alias)
            if not norm or norm in aliases_seen:
                continue
            aliases_seen.add(norm)
            aliases.append(alias)
    # Langer eerst → minder kans dat een korte alias binnen een langere wordt
    # gematcht voordat de langere de kans krijgt.
    aliases.sort(key=lambda a: (-len(a), a))

    spans: list[_Span] = []
    for alias in aliases:
        pattern = _alias_to_regex(alias)
        if pattern is None:
            continue
        for m in pattern.finditer(text):
            persons = catalog.find_by_alias(m.group())
            if len(persons) != 1:
                # Ambigu (meerdere personen) of geen match (kan in theorie
                # niet voorkomen) → veiligheidshalve overslaan.
                continue
            spans.append(
                _Span(
                    start=m.start(),
                    end=m.end(),
                    text=m.group(),
                    ptype="person",
                    canonical_key=_person_canonical_key(persons[0]),
                )
            )
    return spans


# --- Overlapping spans ------------------------------------------------------


def _greedy_nonoverlap(spans: list[_Span]) -> list[_Span]:
    """Greedy non-overlap binnen één tier: langer wint, daarna prioriteit."""
    sorted_spans = sorted(
        spans,
        key=lambda s: (
            -(s.end - s.start),
            _PTYPE_PRIORITY.get(s.ptype, 99),
            s.start,
            s.ptype,
        ),
    )
    kept: list[_Span] = []
    for s in sorted_spans:
        if any(s.start < k.end and k.start < s.end for k in kept):
            continue
        kept.append(s)
    return kept


def _resolve_overlaps(spans: list[_Span]) -> list[_Span]:
    """Twee-tier resolutie:

    1. Specifieke types (person/email/phone/student_number) krijgen
       voorrang — onderling lost de langste het op.
    2. Het generieke `labeled_sensitive` wordt alleen toegevoegd wanneer
       het géén overlap heeft met een al gekozen specifieke span.

    Dit voorkomt dat een te-greedy labeled_sensitive-span (bv. "1146131
    hier.") een specifieke student_number-detectie overschaduwt.
    """
    specific = [s for s in spans if s.ptype != "labeled_sensitive"]
    generic = [s for s in spans if s.ptype == "labeled_sensitive"]

    kept_specific = _greedy_nonoverlap(specific)
    kept_generic: list[_Span] = []

    generic_sorted = sorted(
        generic,
        key=lambda s: (-(s.end - s.start), s.start, s.ptype),
    )
    for g in generic_sorted:
        if any(g.start < k.end and k.start < g.end for k in kept_specific):
            continue
        if any(g.start < k.end and k.start < g.end for k in kept_generic):
            continue
        kept_generic.append(g)

    return sorted(kept_specific + kept_generic, key=lambda s: s.start)


def _collect_spans(text: str, catalog: PeopleCatalog) -> list[_Span]:
    if not text:
        return []
    spans: list[_Span] = list(_find_catalog_spans(text, catalog))
    for rm in find_all_rule_matches(text):
        ptype = _RULE_TYPE_TO_PTYPE.get(rm.rule_type)
        if ptype is None:
            continue
        spans.append(
            _Span(
                start=rm.start,
                end=rm.end,
                text=rm.text,
                ptype=ptype,
                canonical_key=None,  # rule-matches groeperen we op tekst
            )
        )
    return _resolve_overlaps(spans)


def _filter_whitelisted_spans(
    spans: list[_Span],
    whitelist: Whitelist | None,
) -> list[_Span]:
    """Verwijder spans waarvan de matched text op de whitelist staat.


    Whitelist-filtering gebeurt vóór placeholder-toewijzing, zodat
    whitelisted waarden ook NIET in de mapping terechtkomen.
    """
    if whitelist is None:
        return spans
    return [s for s in spans if not whitelist.is_allowed(s.text)]


# --- Public API 


def anonymize_text(
    text: str,
    catalog: PeopleCatalog,
    state: AnonymizationState,
    whitelist: Whitelist | None = None,
) -> str:
    """Vervang gevoelige spans in `text` door placeholders.

    Volgorde:
      1. spans verzamelen (catalog + rules)
      2. whitelist-filtering — spans op de whitelist worden weggelaten
      3. placeholders toewijzen (links-naar-rechts voor nummervolgorde)
      4. tekst vervangen (rechts-naar-links zodat indices niet schuiven)
    """
    if not text:
        return text
    spans = _collect_spans(text, catalog)
    spans = _filter_whitelisted_spans(spans, whitelist)
    if not spans:
        return text

    placeholders = [
        state.get_or_create(s.text, s.ptype, canonical_key=s.canonical_key)
        for s in spans
    ]
    result = text
    for s, p in reversed(list(zip(spans, placeholders))):
        result = result[: s.start] + p + result[s.end :]
    return result


def anonymize_heading_path(
    path: list[str],
    catalog: PeopleCatalog,
    state: AnonymizationState,
    whitelist: Whitelist | None = None,
) -> list[str]:
    """Anonymiseer elk item in een heading_path los, return een nieuwe lijst."""
    if not path:
        return []
    return [anonymize_text(item, catalog, state, whitelist) for item in path]


def anonymize_blocks(
    blocks: list[Block],
    catalog: PeopleCatalog,
    whitelist: Whitelist | None = None,
) -> tuple[list[Block], dict[str, dict[str, str]]]:
    """Hoofdentry: anonymize elke block + heading_path, return nieuwe blocks.

    De originele Block-objecten worden NIET gemuteerd; via
    `dataclasses.replace` worden nieuwe instances aangemaakt met aangepaste
    `text` en `heading_path`. Andere velden blijven gelijk.

    Met een `whitelist`-argument worden waarden op die whitelist
    ongemoeid gelaten én zijn ze ook niet aanwezig in de mapping.
    """
    state = AnonymizationState()
    new_blocks: list[Block] = []
    for b in blocks:
        new_text = anonymize_text(b.text, catalog, state, whitelist)
        new_path = anonymize_heading_path(b.heading_path, catalog, state, whitelist)
        new_blocks.append(_dc_replace(b, text=new_text, heading_path=new_path))
    return new_blocks, state.to_mapping()
