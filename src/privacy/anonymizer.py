from __future__ import annotations


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
