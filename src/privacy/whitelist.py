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
 
 
