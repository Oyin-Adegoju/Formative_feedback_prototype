"""
Geen runtime dependency van de anonymizer. De anonymizer leest alleen
het resulterende JSON-bestand, nooit de originele CSV.

Run:
    python scripts/build_name_catalog.py
    python scripts/build_name_catalog.py --input <csv> --output <json>
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import unicodedata
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("data/reference/people_software_advanced_2026.csv")
DEFAULT_OUTPUT = Path("data/reference/people_catalog.json")

# Nederlandse + Arabische-romeinse tussenvoegsels die we vooraan een
# achternaam kunnen tegenkomen. Gebruikt voor het splitsen "van der Berg"
# naar (tussenvoegsel='van der', achternaam='Berg').
_TUSSENVOEGSELS: frozenset[str] = frozenset({
    "van", "van der", "van de", "van den", "van het", "van 't",
    "de", "den", "der", "des", "du",
    "te", "ten", "ter",
    "in", "in 't", "op", "op 't", "aan",
    "het", "'t",
    "von", "zu", "zur",
    "le", "la", "el", "al", "abu", "ibn", "ben",
    "of",
})

logger = logging.getLogger(__name__)


# Normalisatie 


def _strip_diacritics(s: str) -> str:
    """Verwijder combining diacritics (Daniël → Daniel, Böhre → Bohre)."""
    if not s:
        return s
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _title_case(s: str) -> str:
    """. Bewaart diacritics."""
    if not s:
        return s
    return s.strip().title()


def _split_tussenvoegsel(achternaam: str) -> tuple[str | None, str]:
    """Splits achternaam in (tussenvoegsel, kern-achternaam).

    Probeert eerst de langst mogelijke prefix (tot 3 woorden) te matchen
    tegen `_TUSSENVOEGSELS`. Returnt (None, achternaam) als er geen
    tussenvoegsel herkend wordt.
    """
    if not achternaam:
        return None, achternaam
    parts = achternaam.split()
    if len(parts) < 2:
        return None, achternaam
    max_prefix = min(len(parts) - 1, 3)
    for n in range(max_prefix, 0, -1):
        prefix = " ".join(parts[:n]).lower()
        if prefix in _TUSSENVOEGSELS:
            return prefix, " ".join(parts[n:])
    return None, achternaam


#  Alias-generatie 


def _alias_set_voor_naam(
    voornaam: str,
    achternaam: str,
    tussenvoegsel: str | None,
) -> set[str]:
    """Bouw aliassen voor één concrete naamvariant (lowercase).

    De caller roept deze functie twee keer aan voor namen met diacritics:
    één keer met origineel, één keer met ASCII-gevouwen versie.
    """
    out: set[str] = set()
    if not voornaam or not achternaam:
        return out

    voor = voornaam.lower()
    achter = achternaam.lower()
    voor_init = f"{voor[0]}."
    achter_init = f"{achter[0]}."

    if tussenvoegsel:
        tussen = tussenvoegsel.lower()
        full_achter = f"{tussen} {achter}"
        # Met tussenvoegsel.
        out.add(f"{voor} {full_achter}")
        out.add(f"{full_achter} {voor}")
        out.add(f"{full_achter}, {voor}")
        out.add(f"{voor_init} {full_achter}")
        # Zonder tussenvoegsel.
        out.add(f"{voor} {achter}")
        out.add(f"{achter} {voor}")
        out.add(f"{achter}, {voor}")
        out.add(f"{voor_init} {achter}")
    else:
        out.add(f"{voor} {achter}")
        out.add(f"{achter} {voor}")
        out.add(f"{achter}, {voor}")
        out.add(f"{voor_init} {achter}")

    # Voor namen met tussenvoegsel gebruiken we de initiaal van de
    # kern-achternaam (Berg → "b."), niet van de tussenvoegsel-prefix.
    out.add(f"{voor} {achter_init}")

    return out


def _make_aliases(
    voornaam: str,
    achternaam: str,
    tussenvoegsel: str | None,
) -> list[str]:
    """Geef alle aliassen (sorted, deduplicated) voor één persoon terug.

    Voegt automatisch een ASCII-variant toe als de naam diacritics bevat.
    """
    aliassen = _alias_set_voor_naam(voornaam, achternaam, tussenvoegsel)

    ascii_voor = _strip_diacritics(voornaam)
    ascii_achter = _strip_diacritics(achternaam)
    ascii_tussen = _strip_diacritics(tussenvoegsel) if tussenvoegsel else None
    if (ascii_voor, ascii_achter, ascii_tussen) != (voornaam, achternaam, tussenvoegsel):
        aliassen |= _alias_set_voor_naam(ascii_voor, ascii_achter, ascii_tussen)

    return sorted(aliassen)


#  Rol-normalisatie 


def _normalize_role(rol: str) -> tuple[str | None, bool]:
    """Zet rolwaarden om naar een vaste vorm en markeer onbekende rollen
    """
    r = rol.strip()
    if not r:
        return None, False
    low = r.lower()
    if low in {"lerende", "lerend", "student"}:
        return "Lerende", True
    if low in {"docent", "leerkracht"}:
        return "Docent", True
    return r, False


# Hoofdroutine 


def load_roster(csv_path: Path) -> list[dict[str, Any]]:
    """Lees de roster-CSV en geef een lijst van genormaliseerde personen terug.

    Verwachte kolommen (case-insensitive): achternaam, voornaam, rol.
    Onvolledige rijen worden gelogd en overgeslagen. Volledig lege rijen
    worden silent overgeslagen.
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Roster-CSV niet gevonden op verwachte pad: {csv_path}"
        )

    personen: dict[str, dict[str, Any]] = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for line_no, row in enumerate(reader, start=2):
            # Case-insensitive kolomnamen, strip alles.
            row_norm: dict[str, str] = {}
            for k, v in row.items():
                if not k:
                    continue
                row_norm[k.lower().strip()] = (v or "").strip()

            achternaam_raw = row_norm.get("achternaam", "")
            voornaam_raw = row_norm.get("voornaam", "")
            rol_raw = row_norm.get("rol", "")

            # Volledig lege rij: silent skip.
            if not any([achternaam_raw, voornaam_raw, rol_raw]):
                continue

            if not voornaam_raw or not achternaam_raw:
                logger.warning(
                    "regel %d: onvolledige rij overgeslagen (achternaam=%r, "
                    "voornaam=%r, rol=%r)",
                    line_no, achternaam_raw, voornaam_raw, rol_raw,
                )
                continue

            rol, rol_herkend = _normalize_role(rol_raw)
            if not rol:
                logger.warning("regel %d: lege rol overgeslagen: %r", line_no, row_norm)
                continue
            if not rol_herkend:
                logger.warning(
                    "regel %d: onbekende rol %r — behouden als-is in output",
                    line_no, rol,
                )

            # Tussenvoegsel uitsplitsen.
            tussen_raw, achter_core_raw = _split_tussenvoegsel(achternaam_raw)
            # Titelcase voor naam-velden; tussenvoegsel blijft lowercase.
            voornaam = _title_case(voornaam_raw)
            achternaam = _title_case(achter_core_raw)
            tussenvoegsel = tussen_raw.lower() if tussen_raw else None

            key = "|".join([
                voornaam.lower(),
                tussenvoegsel or "",
                achternaam.lower(),
            ])

            if key in personen:
                # Dezelfde persoon, mogelijk andere rol.
                existing = personen[key]
                if rol not in existing["rol"]:
                    existing["rol"] = sorted({*existing["rol"], rol})
            else:
                personen[key] = {
                    "voornaam": voornaam,
                    "achternaam": achternaam,
                    "tussenvoegsel": tussenvoegsel,
                    "rol": [rol],
                    "aliassen": _make_aliases(voornaam, achternaam, tussenvoegsel),
                }

    # Deterministische volgorde: achternaam → tussenvoegsel → voornaam.
    return sorted(
        personen.values(),
        key=lambda p: (p["achternaam"], p["tussenvoegsel"] or "", p["voornaam"]),
    )


