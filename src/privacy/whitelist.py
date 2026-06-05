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
 
 
