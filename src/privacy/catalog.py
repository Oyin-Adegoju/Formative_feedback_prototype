"""Runtime-laag bovenop data/reference/people_catalog.json.

Levert exacte aliasmatching voor de anonymizer. Doet expliciet **niet**:
  - fuzzy matching
  - NER / Presidio / spaCy
  - regex op studentnummers
  - whitelistlogica
  - placeholder-generatie

Verantwoordelijkheden: alleen laden, valideren, indexeren en exact zoeken.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_REQUIRED_FIELDS: tuple[str, ...] = (
    "voornaam",
    "achternaam",
    "tussenvoegsel",
    "rol",
    "aliassen",
)

_WHITESPACE_RE = re.compile(r"\s+")


# --- Datamodel --------------------------------------------------------------


@dataclass(frozen=True)
class PersonRecord:
    """Immutabel persoonsrecord zoals geladen uit people_catalog.json."""
    voornaam: str
    achternaam: str
    tussenvoegsel: str | None
    rol: tuple[str, ...]
    aliassen: tuple[str, ...]


# --- Helper: normalisatie ---------------------------------------------------


def normalize_lookup_text(text: str) -> str:
    """Normaliseer een tekstfragment voor exacte aliasmatching.

    - lowercase
    - strip leading/trailing whitespace
    - collapse interne whitespace (incl. tabs/newlines) tot één spatie

    Wordt ook door anonymizer.py hergebruikt zodat input én catalogus
    op dezelfde manier worden vergeleken.
    """
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


# --- Catalogus --------------------------------------------------------------


class PeopleCatalog:
    """Index over PersonRecords met exact-aliasmatching."""

    def __init__(self, personen: Iterable[PersonRecord]) -> None:
        self._personen: list[PersonRecord] = list(personen)
        self._alias_index: dict[str, list[PersonRecord]] = defaultdict(list)

        for p in self._personen:
            # Dedupe binnen één persoon zodat dezelfde persoon nooit
            # meerdere keren in dezelfde alias-bucket terechtkomt.
            gezien: set[str] = set()
            for alias in p.aliassen:
                key = normalize_lookup_text(alias)
                if not key or key in gezien:
                    continue
                gezien.add(key)
                self._alias_index[key].append(p)

    # --- Lookup ---

    def get_all_people(self) -> list[PersonRecord]:
        """Lijst van alle personen in JSON-volgorde."""
        return list(self._personen)

    def find_by_alias(self, alias: str) -> list[PersonRecord]:
        """Geef ALLE personen die deze (genormaliseerde) alias bezitten.

        Exacte match na normalisatie — geen substring, geen fuzzy. Lege
        lijst als niets matcht of als de input na normalisatie leeg is.
        """
        key = normalize_lookup_text(alias)
        if not key:
            return []
        return list(self._alias_index.get(key, []))

    def has_alias(self, alias: str) -> bool:
        key = normalize_lookup_text(alias)
        if not key:
            return False
        return key in self._alias_index

    # --- Tellers ---

    def alias_count(self) -> int:
        """Aantal unieke alias-keys in de index."""
        return len(self._alias_index)

    def person_count(self) -> int:
        return len(self._personen)


# --- Laden + validatie ------------------------------------------------------


def _validate_person_dict(idx: int, raw: Any) -> PersonRecord:
    if not isinstance(raw, dict):
        raise ValueError(
            f"Persoon op index {idx}: verwacht object, kreeg {type(raw).__name__}"
        )
    for veld in _REQUIRED_FIELDS:
        if veld not in raw:
            raise ValueError(
                f"Persoon op index {idx}: verplicht veld '{veld}' ontbreekt"
            )

    aliassen = raw["aliassen"]
    if not isinstance(aliassen, list):
        raise ValueError(
            f"Persoon op index {idx}: 'aliassen' moet een lijst zijn, "
            f"kreeg {type(aliassen).__name__}"
        )
    rol = raw["rol"]
    if not isinstance(rol, list):
        raise ValueError(
            f"Persoon op index {idx}: 'rol' moet een lijst zijn, "
            f"kreeg {type(rol).__name__}"
        )

    tussen = raw["tussenvoegsel"]
    if tussen is not None and not isinstance(tussen, str):
        raise ValueError(
            f"Persoon op index {idx}: 'tussenvoegsel' moet str of null zijn, "
            f"kreeg {type(tussen).__name__}"
        )

    return PersonRecord(
        voornaam=str(raw["voornaam"]),
        achternaam=str(raw["achternaam"]),
        tussenvoegsel=tussen,
        rol=tuple(str(r) for r in rol),
        aliassen=tuple(str(a) for a in aliassen),
    )


def load_catalog(path: str | Path) -> PeopleCatalog:
    """Laad people_catalog.json en bouw een PeopleCatalog.

    Raises:
        FileNotFoundError: als het pad niet bestaat.
        ValueError: bij ongeldige JSON of ontbrekende verplichte velden.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"people_catalog.json niet gevonden op: {p}"
        )

    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Ongeldige JSON in {p}: {e}") from e

    if not isinstance(data, list):
        raise ValueError(
            f"people_catalog.json moet een lijst van personen zijn op top-level, "
            f"kreeg {type(data).__name__}"
        )

    personen = [_validate_person_dict(idx, raw) for idx, raw in enumerate(data)]
    return PeopleCatalog(personen)
