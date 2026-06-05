from __future__ import annotations

from dataclasses import dataclass, field

from src.privacy.catalog import normalize_lookup_text


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
