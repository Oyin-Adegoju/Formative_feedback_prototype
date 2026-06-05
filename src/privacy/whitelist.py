"""Whitelist-laag voor de anonymizer: expliciet toegestane waarden.
 
De anonymizer raadpleegt deze whitelist voordat een placeholder wordt
toegewezen. Waarden op de whitelist (bv. opdrachtgever-namen, bedrijven)
blijven dus ongemoeid in de geanonimiseerde tekst.
 
`suggest_whitelist_candidates` levert mogelijke kandidaten op basis van
gelabelde sensitive-fields en unieke catalogusmatches — bedoeld als
input voor latere docentbevestiging, NIET als auto-whitelist.
 
Doet expliciet NIET:
  - Streamlit UI
  - schrijven naar disk
  - fuzzy matching
  - blocks muteren
  - placeholder-generatie
"""
 
from __future__ import annotations
 
import re
from dataclasses import dataclass
from typing import Iterable
 
from src.parser.structure import Block
from src.privacy.catalog import PeopleCatalog, normalize_lookup_text
from src.privacy.rules import find_labeled_sensitive_fields
 
 
# Stakeholder-labels die in interviews/projecten vaak een naam bevatten
# die de docent expliciet wil bewaren (niet anonimiseren).
_STAKEHOLDER_LABELS: frozenset[str] = frozenset({
    "opdrachtgever",
    "respondent",
    "docent",
    "geïnterviewde",
    "geinterviewde",
    "interviewee",
})
 
_WHITESPACE_RE = re.compile(r"\s+")
 
 
# --- Datamodel --------------------------------------------------------------
 
 
@dataclass(frozen=True)
class WhitelistSuggestion:
    """Een mogelijke whitelist-kandidaat, nog niet bevestigd door de docent."""
    value: str
    source: str   # "block_text" | "heading_path"
    reason: str   # mensleesbare uitleg
 
 
# --- Normalisatie -----------------------------------------------------------
 
 
def normalize_whitelist_value(text: str) -> str:
    """Normaliseer een whitelist-waarde voor exact-match-vergelijking.
 
    Dezelfde logica als `catalog.normalize_lookup_text`: lowercase,
    strip, collapse interne whitespace. Wordt hier herhaald als een
    aparte naam zodat callers semantisch onderscheid kunnen maken.
    """
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()
 
 
# --- Whitelist --------------------------------------------------------------
 
 
class Whitelist:
    """Set-achtige container met exacte, genormaliseerde lookup."""
 
    def __init__(self, allowed_values: Iterable[str]) -> None:
        # genormaliseerde-key → eerst-geziene originele waarde (voor display).
        self._norm_to_original: dict[str, str] = {}
        for v in allowed_values:
            if v is None:
                continue
            norm = normalize_whitelist_value(v)
            if not norm:
                continue
            if norm not in self._norm_to_original:
                self._norm_to_original[norm] = v.strip()
 
    def is_allowed(self, value: str) -> bool:
        """Staat `value` (na normalisatie) op de whitelist?"""
        norm = normalize_whitelist_value(value)
        if not norm:
            return False
        return norm in self._norm_to_original
 
    def get_all(self) -> list[str]:
        """Originele whitelist-waarden, alfabetisch gesorteerd."""
        return sorted(self._norm_to_original.values(), key=lambda s: s.lower())
 
    def count(self) -> int:
        return len(self._norm_to_original)
 
 
def build_whitelist(values: list[str]) -> Whitelist:
    """Factory voor `Whitelist`."""
    return Whitelist(values)
 
 
# --- Suggestie-pijplijn -----------------------------------------------------
 
 
_ALIAS_REGEX_CACHE: dict[str, re.Pattern[str]] = {}
 
 
def _alias_to_regex(alias: str) -> re.Pattern[str] | None:
    if not alias:
        return None
    cached = _ALIAS_REGEX_CACHE.get(alias)
    if cached is not None:
        return cached
    escaped = re.escape(alias).replace(r"\ ", r"\s+")
    prefix = r"\b" if alias[0].isalnum() else r"(?<!\w)"
    suffix = r"\b" if alias[-1].isalnum() else r"(?!\w)"
    pattern = re.compile(prefix + escaped + suffix, re.IGNORECASE)
    _ALIAS_REGEX_CACHE[alias] = pattern
    return pattern
 
 
def _scan_catalog_matches(
    text: str,
    catalog: PeopleCatalog,
) -> list[tuple[str, str]]:
    """Geef unieke (matched_text, canonical_name)-paren terug.
 
    Alleen aliassen die exact één persoon teruggeven worden meegenomen —
    ambigue matches zijn te onzeker voor een suggestie.
    """
    if not text:
        return []
    aliases_seen: set[str] = set()
    aliases: list[str] = []
    for p in catalog.get_all_people():
        for alias in p.aliassen:
            n = normalize_lookup_text(alias)
            if not n or n in aliases_seen:
                continue
            aliases_seen.add(n)
            aliases.append(alias)
    # Langer eerst zodat "Sara Denno" voor "Sara" gevonden wordt.
    aliases.sort(key=lambda a: (-len(a), a))
 
    found: dict[str, str] = {}  # canonical → eerst-gevonden matched_text
    for alias in aliases:
        pattern = _alias_to_regex(alias)
        if pattern is None:
            continue
        for m in pattern.finditer(text):
            persons = catalog.find_by_alias(m.group())
            if len(persons) != 1:
                continue
            p = persons[0]
            parts = [p.voornaam]
            if p.tussenvoegsel:
                parts.append(p.tussenvoegsel)
            parts.append(p.achternaam)
            canonical = " ".join(parts)
            if canonical not in found:
                found[canonical] = m.group()
    return [(matched, canonical) for canonical, matched in found.items()]
 
 
def _iter_fragments(block: Block) -> Iterable[tuple[str, str]]:
    """Yield (fragment_text, source_name) voor block.text + heading_path-items."""
    if block.text:
        yield block.text, "block_text"
    for item in block.heading_path:
        if item:
            yield item, "heading_path"
 
 
