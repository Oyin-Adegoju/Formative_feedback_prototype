"""section_family.py — corpus-derived section-family classifier for evidence gating.

This module answers ONE question, deterministically and explainably:

    "Given a block's heading_path, which criterion section-family/families does
     that block structurally belong to?"

It is the ONLY admission authority for the CAPS→Qwen evidence handoff. The
packet builder is now strictly *heading-only*: a block is admitted for a
criterion ONLY because its section identity matches that criterion. There is no
keyword rescue, no neutral-block promotion, and no cross-family borrowing. A
block that merely contains a matching word (``wachtwoord`` in a use-case table,
``avg`` in an NFR table) but sits under the wrong heading family is excluded.

Design notes
------------
* Membership is a SET, not a single label. One heading_path can belong to
  several families at once — e.g. "Stakeholder quadrant > Onderzoek naar web
  gebruikers > Functionele requirements" legitimately serves stakeholders,
  beperking AND requirements. Multi-family membership prevents the gate from
  over-rejecting genuinely shared sections.
* Patterns are matched against each heading_path SEGMENT (not the joined path),
  so a hint cannot accidentally span two adjacent segments. Word boundaries are
  used where a bare substring would over-match.
* The families are compact and corpus-informed (derived from the anonymized
  documents under data/anonymized/), NOT exhaustive synonym lists. Tempting but
  ambiguous headings are deliberately rejected for precision:
    - requirements: NO use-case / moscow patterns (use cases are not
      requirements).
    - security: NO ``risico`` / ``veiligheid`` (project-risk and generic prose).
    - stakeholders: NO ``actoren`` / ``betrokken`` / bare ``quadrant`` / standalone
      belang/concern sub-headings.
* No block text is read here — only heading_path. Selecting content is the
  packet builder's job. This keeps the section authority cleanly isolated.

taalkeuze has, in practice, NO dedicated section family in this corpus: language
choice — when present at all — is discussed inside doelgroep / requirements /
intro prose. Under the strict heading-only policy, taalkeuze is therefore
typically EMPTY on real documents. That is the intended, accepted outcome
(precision over recall): a missing language-choice heading means little or no
taalkeuze evidence, not a keyword hunt across the document.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Criterion section-family patterns (corpus-derived, strict)
# ---------------------------------------------------------------------------
#
# Each entry is a tuple of compiled, case-insensitive patterns. A heading_path
# belongs to a family when ANY of its segments matches ANY pattern of that
# family. Keep these tight and explainable — broaden only with a corpus reason.

# beperking & deskresearch. The doelgroep/limitation half:
# "Onderzoek naar web gebruikers" is the canonical section (6 docs); the
# deskresearch half lives under a Literatuurlijst / bronvermelding heading
# (4 docs). Both halves are part of the single "Beperking & deskresearch"
# criterion, so both are admitted here.
_BEPERKING_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"onderzoek naar web", re.IGNORECASE),   # canonical doelgroep section
    re.compile(r"web ?gebruikers", re.IGNORECASE),
    re.compile(r"webshopgebruiker", re.IGNORECASE),
    re.compile(r"doelgroep", re.IGNORECASE),
    re.compile(r"\bbeperking", re.IGNORECASE),
    re.compile(r"\bafbakening", re.IGNORECASE),
    re.compile(r"toegankelijkheid", re.IGNORECASE),
    # deskresearch half of the criterion (real, recurring headings):
    re.compile(r"literatuur", re.IGNORECASE),           # "Literatuurlijst" (4 docs)
    re.compile(r"\bbron(?:nen|vermelding)?\b", re.IGNORECASE),
    re.compile(r"deskresearch", re.IGNORECASE),
)

# stakeholders. Only an explicit stakeholder heading. "belanghebbende" is the
# unambiguous Dutch synonym (kept although absent from this corpus). Ambiguous
# belang/concern sub-headings ("Belangen en zorgen", "Categoriseren en
# prioriteren") are deliberately NOT here: they overlap requirements
# prioritisation, and documents that use them still carry a real "Stakeholder
# analyse" heading that this pattern admits.
_STAKEHOLDERS_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"stakeholder", re.IGNORECASE),
    re.compile(r"belanghebbende", re.IGNORECASE),
)

# requirements. NO use-case / moscow patterns: use cases are not requirements,
# and a "use case" subsection nested under a requirements heading is further
# excluded by the builder via _classify_section.
_REQUIREMENTS_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"requirement", re.IGNORECASE),
    re.compile(r"\beis(?:en)?\b", re.IGNORECASE),
    re.compile(r"wensen", re.IGNORECASE),
    re.compile(r"prioriteiten", re.IGNORECASE),
    re.compile(r"functione(?:le|el|l)\b", re.IGNORECASE),       # functionele / functioneel
    re.compile(r"functional\b", re.IGNORECASE),
    re.compile(r"(?:niet|non)[\s\-]+functione(?:le|el|l)", re.IGNORECASE),
    re.compile(r"(?:niet|non)[\s\-]+functional", re.IGNORECASE),
    re.compile(r"constraint", re.IGNORECASE),
    # MoSCoW is a requirements-PRIORITISATION concept. In this corpus it only
    # appears in headings like "Waarom MoSCoW" (the motivation for the priority
    # ranking), which is genuine requirements content — so a heading naming it
    # belongs to requirements, not to whatever doelgroep section it was nested
    # under. Distinctive token, no contamination risk.
    re.compile(r"moscow", re.IGNORECASE),
)

# security / privacy. Strict: NO bare "risico" (project risk) and NO
# "veiligheid" (generic prose). owasp/avg/encryptie are corpus-real and
# unambiguous.
_SECURITY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"security", re.IGNORECASE),
    re.compile(r"beveiliging", re.IGNORECASE),
    re.compile(r"privacy", re.IGNORECASE),
    re.compile(r"gegevensbescherming", re.IGNORECASE),
    re.compile(r"\bavg\b", re.IGNORECASE),               # "AVG-naleving"
    re.compile(r"owasp", re.IGNORECASE),
    re.compile(r"encryptie", re.IGNORECASE),
)

# taalkeuze: real language-choice headings only. Absent from this corpus, so
# taalkeuze packets are typically empty — see module docstring.
_TAALKEUZE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"taalkeuze", re.IGNORECASE),
    re.compile(r"taalversie", re.IGNORECASE),
    re.compile(r"meertalig", re.IGNORECASE),
    re.compile(r"\btaal\b", re.IGNORECASE),
    re.compile(r"internationalisatie", re.IGNORECASE),
    re.compile(r"lokalisatie", re.IGNORECASE),
    re.compile(r"language", re.IGNORECASE),
)

SECTION_FAMILIES: Final[dict[str, tuple[re.Pattern[str], ...]]] = {
    "beperking":    _BEPERKING_PATTERNS,
    "stakeholders": _STAKEHOLDERS_PATTERNS,
    "requirements": _REQUIREMENTS_PATTERNS,
    "taalkeuze":    _TAALKEUZE_PATTERNS,
    "security":     _SECURITY_PATTERNS,
}
"""criterion_key → compiled heading patterns. Exposed for tests/inspection."""


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------


def families_of(heading_path: list[str]) -> frozenset[str]:
    """Return the set of criterion families a heading_path structurally belongs to.

    A family matches when ANY heading segment matches ANY of its patterns.
    An empty result means the block is *neutral* — it sits under a generic
    heading (Inleiding, an empty path, a page header, an ethics section) that
    belongs to no criterion family. Under the strict heading-only policy,
    neutral blocks are NEVER admitted.
    """
    fams: set[str] = set()
    for seg in heading_path:
        s = seg.strip()
        if not s:
            continue
        for key, patterns in SECTION_FAMILIES.items():
            if key in fams:
                continue
            if any(p.search(s) for p in patterns):
                fams.add(key)
    return frozenset(fams)


def primary_families(heading_path: list[str]) -> frozenset[str]:
    """Return the families of the DEEPEST heading segment that matches any family.

    This is the admission authority for the packet builder. Section identity is
    decided by the most-specific (deepest) heading, NOT by any ancestor — so a
    stale / never-popped root segment (e.g. a document whose H1 is "Stakeholder
    quadrant") cannot drag every block in the document into that family. A block
    under "Stakeholder quadrant > Onderzoek naar web gebruikers > Functionele
    requirements" is a *requirements* block: its deepest meaningful section is
    "Functionele requirements".

    Returns an empty set when no segment matches any family (neutral block).
    Contrast families_of(), which returns the UNION across all segments and is
    kept for inspection / foreign-detection.
    """
    for seg in reversed(heading_path):
        s = seg.strip()
        if not s:
            continue
        fams = {
            key for key, patterns in SECTION_FAMILIES.items()
            if any(p.search(s) for p in patterns)
        }
        if fams:
            return frozenset(fams)
    return frozenset()


def is_in_family(criterion_key: str, fams: frozenset[str]) -> bool:
    """True when the block's section families include this criterion."""
    return criterion_key in fams


def is_foreign(criterion_key: str, fams: frozenset[str]) -> bool:
    """True when the block belongs to other families but NOT this criterion.

    Retained for inspection/tests. Under the strict heading-only policy the
    builder simply never admits foreign blocks — there is no rescue path.
    """
    return bool(fams) and criterion_key not in fams


def is_neutral(fams: frozenset[str]) -> bool:
    """True when the block belongs to no criterion family (generic context)."""
    return not fams


# ---------------------------------------------------------------------------
# Negation phrases (signal hardening — shared across criteria)
# ---------------------------------------------------------------------------
#
# Block-level negation: when a block explicitly negates its own criterion
# signal, the builder must NOT present it as positive supporting evidence.
# Detected at block level only (cannot see across blocks). Corpus examples:
# "Verder heb ik geen bronnen gebruikt.", "Ik heb geen echt onderzoek gedaan",
# "niet gebaseerd op interviews of bronnen, maar klinkt logisch."

_NEGATION_PHRASES: Final[tuple[str, ...]] = (
    "geen bronnen",
    "geen bron ",
    "geen onderzoek",
    "geen echt onderzoek",
    "geen echte bronnen",
    "geen deskresearch",
    "geen literatuur",
    "geen bronnenlijst",
    "geen bronvermelding",
    "niet onderzocht",
    "niet gebaseerd op bronnen",
    "niet gebaseerd op interviews",
    "geen keuze gemaakt",
    "klinkt logisch",
    "zelf bedacht",
    "zelf verzonnen",
)


def contains_negation(text: str) -> bool:
    """True when the block text explicitly negates its own evidence signal."""
    low = text.lower()
    return any(phrase in low for phrase in _NEGATION_PHRASES)
