"""checks.py — CAPS criterion-level deterministic check functions.

Converts retrieval hits (RetrievalHit objects from retrieval.py) into
per-criterion CriterionResult objects (models.py).

No scoring, no blocker aggregation, no document-level stoplight,
no feedback generation, no model calls, no parser or anonymizer changes.

Architecture position:
    parser output → [anonymizer] → CAPS retrieval → CAPS checks → CAPS scoring

All check functions are deterministic: same hits → same result.
Checks operate only on the RetrievalHit objects passed in.
They do not read any file, call any model, or inspect any global state.

Future anonymized input: because anonymized blocks preserve block_id,
page_no, block_type, heading_path, text, and table_meta structure (only
text content changes), these checks require no redesign for that path.

Output contract per CriterionResult:
    status, stoplight, is_blocker  — objective CAPS verdict
    evidence                       — block pointers supporting the verdict
    count                          — found count for countable criteria
    notes                          — human-readable diagnostic observations
    missing_signals                — short English identifiers for absent signals
                                     (e.g. "research_evidence", "stakeholder_count")
                                     Consumed by downstream Qwen diagnosis.
"""

from __future__ import annotations

import re
from typing import Final

from src.caps.criterion_specs import CRITERIA_BY_KEY, CriterionSpec
from src.caps.models import CriterionResult, CriterionStatus, EvidenceRef, StoplightLabel
from src.caps.retrieval import RetrievalHit


# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------

# Signals that a chosen limitation / doelgroep has been described.
# Broad but reasonable for this assignment context.
_LIMITATION_TERMS: Final[frozenset[str]] = frozenset({
    "beperking", "slechtziend", "visuele beperking", "visueel beperkt",
    "blind", "doof", "dyslexie", "motorische beperking", "cognitieve beperking",
    "auditieve beperking", "afbakening", "doelgroep met beperking",
    "niet in scope", "buiten scope", "richt me op", "focus op",
    "gekozen doelgroep", "specifieke doelgroep", "niche",
})

# Signals that deskresearch / literature support is present.
_RESEARCH_TERMS: Final[frozenset[str]] = frozenset({
    "deskresearch", "desk research", "literatuuronderzoek", "literatuurlijst",
    "literatuur", "bronvermelding", "bronnenlijst", "bronnen", "bron",
    "referentie", "onderzoek", "wetenschappelijk", "studie", "publicatie",
    "verkenning", "marktonderzoek", "user research",
})

# Phrases that explicitly negate a research signal within the same block.
# Only matched at block level — cannot detect cross-block negation.
_NEGATION_RESEARCH: Final[frozenset[str]] = frozenset({
    "geen bronnen", "geen onderzoek", "geen deskresearch",
    "niet onderzocht", "niet gebaseerd op bronnen",
    "niet gebaseerd op interviews",
    "geen literatuur", "geen bronnenlijst",
    "geen echt onderzoek", "geen echte bronnen",
    "geen bronvermelding", "klinkt logisch",
})

# Column-header keywords that indicate a stakeholder table.
# Used to skip header rows in table cell extraction.
_STAKEHOLDER_HEADER_KW: Final[frozenset[str]] = frozenset({
    "stakeholder", "naam", "rol", "functie", "belang", "invloed",
    "prioritering", "concern", "actor", "betrokkene", "beschrijving",
})

# Role-like keywords for supplementary paragraph/bullet scanning.
_STAKEHOLDER_ROLE_KW: Final[frozenset[str]] = frozenset({
    "opdrachtgever", "klant", "eindgebruiker", "gebruiker",
    "ontwikkelaar", "developer", "projectmanager", "manager",
    "directeur", "ceo", "cto", "designer", "ux designer",
    "admin", "beheerder", "eigenaar", "leverancier",
    "docent", "begeleider", "coach", "student", "medewerker",
    "investeerder", "aandeelhouder", "afdelingshoofd",
})

# Concrete security mechanisms — not just generic security language.
_CONCRETE_SECURITY: Final[frozenset[str]] = frozenset({
    "authenticatie", "autorisatie", "encryptie", "avg", "gdpr",
    "owasp", "https", "ssl", "tls", "wachtwoord", "toegangscontrole",
    "risicoanalyse", "two-factor", "2fa", "jwt", "xss", "csrf",
    "sql injection", "penetratietest", "beveiligingsaudit", "firewall",
    "hashing", "bcrypt", "privacyverklaring",
})

# Generic security language — necessary but not sufficient for "sufficient".
_GENERIC_SECURITY: Final[frozenset[str]] = frozenset({
    "security", "beveiliging", "veiligheid", "veilig", "privacy",
})

# Explicit language choice terms (for taalkeuze check).
_LANGUAGE_TERMS: Final[frozenset[str]] = frozenset({
    "taalkeuze", "taalversie", "meertalig",
    "in het nederlands", "in het engels", "in de nederlandse",
    "taal van de website", "webshop in het",
    "nederlands", "engels", "french", "duits",
})

# Consequence / effect terms that follow a language choice (for taalkeuze check).
# These should appear in language-related context (already filtered by retrieval).
_CONSEQUENCE_TERMS: Final[frozenset[str]] = frozenset({
    "bereik", "vertaalkosten", "lokalisatie", "internationalisatie",
    "internationaal", "consequentie", "beïnvloed", "beïnvloeden",
    "begrijpelijk", "begrijpelijkheid", "taalbarrière", "taalbarrier",
    "vertaling", "vertalen", "meertaligheid", "doelgroepbereik",
    "taalgebied", "gebruikersvriendelijkheid",
})

# Compound MoSCoW priority labels — the most reliable requirement-count signal.
# Bare "Must" / "Should" / "Could" are intentionally excluded (too noisy).
_MOSCOW_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(must[\s\-]?have|should[\s\-]?have|could[\s\-]?have|won'?t[\s\-]?have)\b",
    re.IGNORECASE,
)

# Bare MoSCoW cell match — used only for individual table cells, not free text.
# `should?` deliberately matches both "should" and the PDF-split artifact "shoul"
# (e.g. "Shoul d have") so that broken priority cells are still recognised.
# This pattern is ONLY applied after retrieval has already identified the block
# as a requirements candidate, keeping the false-positive risk low.
_MOSCOW_BARE_CELL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(must|should?|could|won'?t)\b",
    re.IGNORECASE,
)

# Section classification — used to attribute requirement counts to FR / NFR / UC.
#
# Each requirement block is classified from its own heading_path (see
# _classify_section), so the patterns must cover Dutch AND English spellings
# plus reasonable casing / spacing / hyphen variants:
#   FR  : "functionele requirements", "functioneel", "functional requirements"
#   NFR : "niet-functionele requirements", "niet-functioneel",
#         "non-functional", "non functional", "non functioneel requirement"
#
# A parenthetical "(non-)" / "(niet-)" marks a COMBINED heading that covers both
# functional and non-functional items at once (e.g. "Constraints en (non-)
# Functionele eisen"). Such sections are deliberately left unclassified
# ("other") instead of guessed — splitting them would invent a subcount.
_SECTION_UC_RE: Final[re.Pattern[str]] = re.compile(
    r"\buse[\s\-]?cases?\b", re.IGNORECASE
)

# Functional-family word: Dutch "functionele / functioneel / functionel"
# and English "functional". Used to build both the NFR and FR patterns so the
# two stay in sync across spellings.
_FUNC_WORD: Final[str] = r"functione(?:le|el|l)|functional"

# Combined "(non-)" / "(niet-)" header → covers FR and NFR together.
_SECTION_BOTH_RE: Final[re.Pattern[str]] = re.compile(
    r"\(\s*(?:non|niet)[\s\-]*\)", re.IGNORECASE
)

# Non-functional: a "niet"/"non" negation directly bound to a functional word.
# Matches "niet-functionele", "niet functioneel", "non-functional",
# "non functional", "non functioneel". The trailing \b keeps "functionaliteit"
# (functionality) from matching.
_SECTION_NFR_RE: Final[re.Pattern[str]] = re.compile(
    rf"\b(?:niet|non)[\s\-]+(?:{_FUNC_WORD})\b", re.IGNORECASE
)

# Functional: any functional word. Only consulted AFTER the NFR/combined
# checks, so a negated form ("niet-functionele") never reaches it.
_SECTION_FR_RE: Final[re.Pattern[str]] = re.compile(
    rf"\b(?:{_FUNC_WORD})\b", re.IGNORECASE
)


def _classify_section(heading_path: list[str]) -> str:
    """Classify a requirement block as 'fr', 'nfr', 'uc', or 'other'.

    Classification uses ONLY the block's own heading_path, scanned from the
    most specific (deepest) segment outward, so a precise subsection heading
    ("Functionele requirements") wins over a broad parent
    ("Constraints en (non-) Functionele eisen"). This replaces the previous
    running-context approach, which drifted: counts in an unmarked block
    (e.g. a "Constraints" table) inherited whatever section was seen last,
    and English / "functioneel" / "(non-)" headings were missed entirely.

    Precedence inside one segment: use case > combined("(non-)") > NFR > FR.
    A combined "(non-)" header resolves to 'other' on purpose — that section
    mixes functional and non-functional items and must not be guessed.

    Block body text is deliberately NOT consulted here: prose like
    "functionaliteit" or "zowel functionele als niet-functionele eisen" is
    descriptive, not a section label, and would manufacture false splits.
    A combined/unlabelled section is resolved instead by reliable per-row
    signals (FR/NFR ids) in _hit_section.
    """
    for seg in reversed(heading_path):
        s = seg.lower()
        if _SECTION_UC_RE.search(s):
            return "uc"
        if _SECTION_BOTH_RE.search(s):
            return "other"
        if _SECTION_NFR_RE.search(s):
            return "nfr"
        if _SECTION_FR_RE.search(s):
            return "fr"
    return "other"


def _searchable_block_text(hit: RetrievalHit) -> str:
    """Block text plus all table cells, for per-row ID scanning."""
    text = hit.block["text"]
    tm = hit.block.get("table_meta")
    if tm and tm.get("cells"):
        cells = [c for row in tm["cells"] for c in row if c]
        if cells:
            text = text + " " + " ".join(cells)
    return text


def _id_type_counts(hit: RetrievalHit) -> tuple[set[str], set[str]]:
    """Return (distinct FR ids, distinct NFR ids) found in one block.

    Used only as a per-row fallback for combined/unlabelled sections. IDs are
    normalised ("FR 01" / "FR-01" / "FR01" → "FR01") so duplicates collapse.
    """
    text = _searchable_block_text(hit)
    fr = {m.group(0).upper().replace(" ", "").replace("-", "") for m in _FR_ID_RE.finditer(text)}
    nfr = {m.group(0).upper().replace(" ", "").replace("-", "") for m in _NFR_ID_RE.finditer(text)}
    return fr, nfr


def _hit_section(hit: RetrievalHit) -> str:
    """Resolve a block's requirement section, with a per-row ID fallback.

    The block's heading decides when it clearly says functional or
    non-functional. Only when the heading is combined/unlabelled ("other") and
    the block's rows carry a *single, unambiguous* requirement-ID type does the
    fallback reclassify it: NFR-only → 'nfr', FR-only → 'fr'. A block mixing
    both ID types stays 'other' — the type is not split on heading guesswork.
    """
    sec = _classify_section(hit.block["heading_path"])
    if sec != "other":
        return sec
    fr_ids, nfr_ids = _id_type_counts(hit)
    if nfr_ids and not fr_ids:
        return "nfr"
    if fr_ids and not nfr_ids:
        return "fr"
    return "other"

# Requirement ID patterns: FR-01, NFR-01, UC-01, USC-01, REQ-01, CONSTR-01, etc.
_REQ_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(FR|NFR|USC|UC|US|REQ|CONSTR|CON|F|NF)[-\s]?\d{1,3}\b",
    re.IGNORECASE,
)

# Type-bearing requirement IDs — a per-row signal used ONLY to split a
# combined/unlabelled section ("other") whose heading does not say FR or NFR.
# Kept deliberately strict ("FR##" / "NFR##") so a row's type is unambiguous;
# the \b before "FR" cannot match the "FR" inside "NFR" (preceded by "N"),
# so the two patterns never overlap.
_NFR_ID_RE: Final[re.Pattern[str]] = re.compile(r"\bNFR[-\s]?\d{1,3}\b", re.IGNORECASE)
_FR_ID_RE: Final[re.Pattern[str]] = re.compile(r"\bFR[-\s]?\d{1,3}\b", re.IGNORECASE)

# Narrative requirement sentence detection — used only as a last-resort fallback
# in non-table blocks that have neither explicit IDs nor numbered bullet lines.
_NARRATIVE_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(website|systeem|webshop|applicatie|gebruiker|admin|beheerder"
    r"|klant|platform|pagina|server|database)\b",
    re.IGNORECASE,
)
_NARRATIVE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(moet|moeten|dient|dienen|zal|mag|kan|should|must)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared internal helpers
# ---------------------------------------------------------------------------


def _make_ref(hit: RetrievalHit) -> EvidenceRef:
    """Create a lightweight EvidenceRef from one retrieval hit."""
    b = hit.block
    return EvidenceRef(
        block_id=b["block_id"],
        page_no=b["page_no"],
        block_type=b["block_type"],
        text_snippet=b["text"][:120],
    )


def _norm(text: str) -> str:
    return text.lower().strip()


def _text_of(hit: RetrievalHit) -> str:
    return _norm(hit.block["text"])


def _all_text(hits: list[RetrievalHit]) -> str:
    return " ".join(_text_of(h) for h in hits)


def _found_terms(terms: frozenset[str], text: str) -> list[str]:
    """Return all terms from the set that appear in text (already normalized)."""
    return [t for t in terms if t in text]


def _status_to_stoplight(status: CriterionStatus) -> StoplightLabel:
    if status == "strong":
        return "green"
    if status == "sufficient":
        return "yellow"
    return "red"  # missing or partial


def _iter_cells(hit: RetrievalHit):
    """Yield all non-None cell strings from a table block's table_meta."""
    tm = hit.block.get("table_meta")
    if not tm:
        return
    for row in tm.get("cells") or []:
        for cell in row:
            if cell:
                yield cell


# ---------------------------------------------------------------------------
# Criterion 1: Beperking & deskresearch
# ---------------------------------------------------------------------------


def _negated_research(block_text: str) -> bool:
    """True if the block explicitly negates its own research signal."""
    return any(neg in block_text for neg in _NEGATION_RESEARCH)


def check_beperking(spec: CriterionSpec, hits: list[RetrievalHit]) -> CriterionResult:
    """Detect co-occurrence: explicit limitation signal + research support.

    Verdicts:
    - missing:    neither signal found
    - partial:    only one signal found (limitation OR research, not both)
    - sufficient: both signals present in at least one block each
    - strong:     both signals each appear in ≥ 2 distinct blocks
    """
    evidence: list[EvidenceRef] = []
    limitation_hits: list[RetrievalHit] = []
    research_hits: list[RetrievalHit] = []
    negated_research_count = 0

    for hit in hits:
        text = _text_of(hit)
        has_lim = bool(_found_terms(_LIMITATION_TERMS, text))
        has_res = bool(_found_terms(_RESEARCH_TERMS, text))
        has_neg = _negated_research(text)

        if has_lim or has_res:
            evidence.append(_make_ref(hit))
        if has_lim:
            limitation_hits.append(hit)
        if has_res:
            if has_neg:
                negated_research_count += 1
            else:
                research_hits.append(hit)

    # Separate body (paragraph/bullet) research hits from heading-only signals.
    # Headings signal a research section exists but do not prove research content.
    # Very short fragments (< 8 words) are unlikely to be substantive research evidence
    # (e.g. "deskresearch gebruikt." or "geen koppeling met bronnen.").
    research_body_hits = [
        h for h in research_hits
        if h.block["block_type"] not in ("heading", "caption")
        and len(h.block["text"].split()) >= 8
    ]

    notes: list[str] = []

    has_limitation = bool(limitation_hits)
    has_research = bool(research_hits)

    if not has_limitation and not has_research:
        status: CriterionStatus = "missing"
        notes.append("Geen beperking- of onderbouwingssignaal gevonden.")
        if negated_research_count:
            notes.append(
                f"{negated_research_count} blok(ken) bevatten onderbouwingstermen "
                "maar ontkenden die in dezelfde zin."
            )

    elif not has_limitation:
        status = "partial"
        found = _found_terms(_RESEARCH_TERMS, _all_text(research_hits))
        notes.append(
            f"Onderbouwing aanwezig ({', '.join(found[:3])}), "
            "maar geen expliciete beperking of doelgroepkeuze beschreven."
        )

    elif not has_research:
        status = "partial"
        found = _found_terms(_LIMITATION_TERMS, _all_text(limitation_hits))
        notes.append(
            f"Beperking beschreven ({', '.join(found[:3])}), "
            "maar geen onderbouwende bronnen of deskresearch gevonden."
        )
        if negated_research_count:
            notes.append(
                "Onderbouwingstermen aanwezig maar expliciet ontkend in context."
            )

    else:
        # Both signals present
        lim_signals = _found_terms(_LIMITATION_TERMS, _all_text(limitation_hits))
        res_signals = _found_terms(_RESEARCH_TERMS, _all_text(research_hits))

        # "Strong" requires substantive body evidence (paragraphs, not just headings/fragments).
        if len(limitation_hits) >= 2 and len(research_body_hits) >= 2:
            status = "strong"
            notes.append(
                f"Beperking in meerdere blokken beschreven ({', '.join(lim_signals[:3])}) "
                f"en meerdere onderbouwingsblokken aanwezig ({', '.join(res_signals[:3])})."
            )
        else:
            status = "sufficient"
            notes.append(
                f"Beperking beschreven ({', '.join(lim_signals[:3])}) "
                f"en onderbouwing aanwezig ({', '.join(res_signals[:3])})."
            )
            if len(research_body_hits) == 0 or (
                len(research_body_hits) == 1 and len(res_signals) == 1
            ):
                notes.append(
                    "Onderbouwingsbewijs smal of alleen via heading — verifieer inhoud."
                )

    missing_signals: list[str] = []
    if not has_limitation:
        missing_signals.append("limitation_signal")
    if not has_research:
        missing_signals.append("research_evidence")

    return CriterionResult(
        criterion_key=spec.key,
        status=status,
        stoplight=_status_to_stoplight(status),
        is_blocker=spec.is_blocker,
        evidence=evidence[:8],
        count=None,
        notes=notes,
        missing_signals=missing_signals,
    )


# ---------------------------------------------------------------------------
# Criterion 2: Stakeholders
# ---------------------------------------------------------------------------


def _extract_roles_from_table(hit: RetrievalHit) -> list[str]:
    """Extract plausible stakeholder role entries from a table block.

    Strategy: examine each row's first non-None cell. A cell is a role
    candidate when:
    - Not a known header keyword (Stakeholder, Naam, Rol, etc.)
    - Starts with a capital letter (proper noun or role title)
    - 1–5 words, 3–50 characters (not a continuation fragment)
    - Not purely numeric

    This is robust to messy PDF table parsing where rows are split into
    many partial-row fragments: fragments start lowercase and are skipped.
    """
    tm = hit.block.get("table_meta")
    if not tm or not tm.get("cells"):
        return []

    roles: list[str] = []
    for row in tm["cells"]:
        if not row:
            continue
        first = row[0]
        if not first:
            continue
        text = first.strip()
        if not text or len(text) < 3 or len(text) > 50:
            continue
        if text.replace(".", "").replace(" ", "").isdigit():
            continue
        if _norm(text) in _STAKEHOLDER_HEADER_KW:
            continue
        # Continuation fragments start with a lowercase letter.
        if text[0].islower():
            continue
        words = text.split()
        if 1 <= len(words) <= 5:
            roles.append(text)

    return roles


def _belang_invloed(hit: RetrievalHit) -> tuple[bool, bool]:
    """Detect 'belang' and 'invloed' signals in one retrieval hit."""
    text = _text_of(hit)
    has_belang = any(t in text for t in ("belang", "concern", "interesse"))
    has_invloed = any(t in text for t in ("invloed", "prioriter", "macht", "power"))
    return has_belang, has_invloed


def check_stakeholders(spec: CriterionSpec, hits: list[RetrievalHit]) -> CriterionResult:
    """Count distinct stakeholder roles; verify belang + invloed coverage.

    Primary source: first-column cells of table blocks.
    Supplementary: keyword matches in paragraph/bullet blocks.

    Verdicts follow minimum_count=4 and strong_from=6 from the spec.
    Belang AND invloed must both be present for sufficient/strong.
    """
    evidence: list[EvidenceRef] = []
    all_roles: list[str] = []
    has_belang = False
    has_invloed = False

    table_roles: list[str] = []
    kw_roles: list[str] = []

    for hit in hits:
        evidence.append(_make_ref(hit))
        b, i = _belang_invloed(hit)
        has_belang = has_belang or b
        has_invloed = has_invloed or i

        if hit.block["block_type"] == "table":
            table_roles.extend(_extract_roles_from_table(hit))
        else:
            text = _text_of(hit)
            for kw in _STAKEHOLDER_ROLE_KW:
                if kw in text:
                    kw_roles.append(kw)

    # Prefer table-extracted roles when available: keyword matching from narrative
    # blocks inflates counts (e.g. "klant" mentioned in passing ≠ a listed stakeholder).
    # Fall back to keyword counting only when no table provides roles.
    all_roles = table_roles if table_roles else kw_roles

    # Deduplicate case-insensitively; discard very short entries.
    seen: set[str] = set()
    unique_roles: list[str] = []
    for r in all_roles:
        key = _norm(r)
        if len(key) >= 3 and key not in seen:
            seen.add(key)
            unique_roles.append(r)

    count = len(unique_roles)
    notes: list[str] = []

    has_any = count > 0
    has_bi = has_belang and has_invloed

    bi_str = (
        f"Belang: {'ja' if has_belang else 'nee'}, "
        f"Invloed: {'ja' if has_invloed else 'nee'}."
    )

    # Status is PRESENCE/COVERAGE-based, never count-threshold-based. Counting
    # stakeholders is no longer part of the design (the extracted count is not
    # trustworthy — it harvests first-column cells from every retrieval table, so
    # requirement/use-case/constraint rows leak in), so it must not drive the
    # verdict. We only ask: is there any stakeholder evidence, and are belang AND
    # invloed both covered? The count is still computed for internal/debug use
    # but is neither exposed nor used for the verdict.
    if not has_any:
        status: CriterionStatus = "missing"
        notes.append("Geen stakeholders herkend in de retrievalhits.")
    elif has_bi:
        status = "sufficient"
        notes.append("Stakeholders met belang en invloed beschreven.")
    else:
        status = "partial"
        notes.append(
            f"Stakeholders herkend, maar belang en/of invloed ontbreekt. {bi_str}"
        )

    missing_signals: list[str] = []
    if not has_belang:
        missing_signals.append("belang_signal")
    if not has_invloed:
        missing_signals.append("invloed_signal")

    return CriterionResult(
        criterion_key=spec.key,
        status=status,
        stoplight=_status_to_stoplight(status),
        is_blocker=spec.is_blocker,
        evidence=evidence[:8],
        count=count,
        notes=notes,
        missing_signals=missing_signals,
    )


# ---------------------------------------------------------------------------
# Criterion 3: Requirements
# ---------------------------------------------------------------------------


def _count_requirement_like_sentences(text: str) -> int:
    """Count requirement-like narrative sentences in a non-table block.

    Conservative: both a system/entity subject (_NARRATIVE_SUBJECT_RE) AND a
    requirement modal verb (_NARRATIVE_VERB_RE) must appear in the same
    sentence. Fragments shorter than 6 words are excluded. Duplicate
    sentences are counted once.

    Only used when neither explicit requirement IDs nor numbered bullet lines
    were found in a block.
    """
    seen: set[str] = set()
    count = 0
    for part in re.split(r"[\n]+|(?<=[.!?])\s+", text):
        norm = part.lower().strip()
        if len(norm.split()) < 6:
            continue
        if not _NARRATIVE_SUBJECT_RE.search(norm):
            continue
        if not _NARRATIVE_VERB_RE.search(norm):
            continue
        if norm not in seen:
            seen.add(norm)
            count += 1
    return count


def _req_count_from_block(hit: RetrievalHit) -> tuple[int, bool]:
    """Estimate the number of requirement items in one block.

    Returns (count, has_prioritization).

    For table blocks: count compound MoSCoW label occurrences in the full
    block text. One label normally corresponds to exactly one requirement row.

    For paragraph/bullet blocks: count unique requirement IDs (FR-01, NFR-01,
    UC-01, etc.). Fall back to MoSCoW labels if no IDs are found.

    has_prioritization is True when any MoSCoW pattern is detected.
    """
    text = hit.block["text"]
    moscow_count = len(_MOSCOW_RE.findall(text))
    req_ids = {m.group(0).upper() for m in _REQ_ID_RE.finditer(text)}
    has_prio = moscow_count > 0

    if hit.block["block_type"] == "table":
        # Table rows with MoSCoW labels are the most reliable count source.
        # Also count rows that carry a bare priority label ("Must", "Could",
        # "Shoul d have") for tables that omit the "have" suffix or where the
        # PDF parser split "should have" into two fragments.
        prio_rows = _count_prio_rows_from_table(hit)
        has_prio = has_prio or (prio_rows > 0)
        return max(moscow_count, len(req_ids), prio_rows), has_prio
    else:
        # Req IDs are more precise than MoSCoW in narrative blocks
        # (MoSCoW may appear in explanatory text, not just requirement labels).
        if req_ids:
            return len(req_ids), has_prio
        # Numbered bullet lines as a fallback (e.g. "1. De website moet...")
        bullets = sum(
            1
            for line in text.splitlines()
            if re.match(r"^\s*(\d{1,3}[.)]\s|\([a-z\d]\)\s)", line, re.IGNORECASE)
            and len(line.split()) >= 4
        )
        if bullets > 0:
            return max(moscow_count, bullets), has_prio
        # Narrative fallback: only when neither IDs nor numbered bullets found.
        narrative = _count_requirement_like_sentences(text)
        return max(moscow_count, narrative), has_prio


def _count_use_case_blocks(hits: list[RetrievalHit]) -> int:
    """Count distinct use case description blocks (heading-level not counted)."""
    uc_re = re.compile(r"\buse\s*case\b", re.IGNORECASE)
    return sum(
        1
        for h in hits
        if h.block["block_type"] in ("paragraph", "bullet")
        and uc_re.search(h.block["text"])
        and len(h.block["text"].split()) >= 10
    )


def _count_prio_rows_from_table(hit: RetrievalHit) -> int:
    """Count table rows that carry a bare MoSCoW priority label in any cell.

    Handles requirements tables that use bare labels ("Must", "Could",
    "Should") instead of the compound "must have" form, including
    PDF-parse split artifacts such as "Shoul d have" or "Shoul d".

    Each row is counted at most once — only the first matching cell per row
    is used. Header rows and continuation fragments (no priority cell) are
    skipped automatically because they contain no priority-like value.

    Only called for table blocks that already passed retrieval for the
    requirements criterion, so the surrounding retrieval filter provides
    the primary guard against false positives.
    """
    tm = hit.block.get("table_meta")
    if not tm or not tm.get("cells"):
        return 0
    count = 0
    for row in tm["cells"]:
        for cell in row:
            if cell and _MOSCOW_BARE_CELL_RE.match(cell.strip()):
                count += 1
                break  # count each row at most once
    return count


def _new_moscow_counts() -> dict[str, int]:
    """Fresh zeroed MoSCoW tally with all four tiers kept separate."""
    return {"must": 0, "should": 0, "could": 0, "wont": 0}


def _bucket_moscow(label: str, counts: dict[str, int]) -> None:
    """Add one occurrence of a MoSCoW label to the right tier.

    Handles bare labels ("must"), compound ones ("must have", "won't have"),
    and the PDF-split artifact "shoul" (folded into "should"). Won't-have is
    kept as its own "wont" tier rather than folded into "could".
    """
    label = label.lower()
    if label.startswith("must"):
        counts["must"] += 1
    elif label.startswith("won"):  # won't / wont
        counts["wont"] += 1
    elif label.startswith("could"):
        counts["could"] += 1
    else:  # "should" or "shoul" (PDF artifact)
        counts["should"] += 1


def _block_moscow_counts(hit: RetrievalHit) -> dict[str, int]:
    """Count MoSCoW labels (must/should/could/wont) inside one block.

    Table blocks: at most one count per row, taken from the first cell whose
    value starts with a bare priority label via _MOSCOW_BARE_CELL_RE (handles
    "MUST HAVE: ...", bare "Should", and the split "Shoul d have").
    Paragraph/bullet blocks: every compound MoSCoW label in the block text.
    """
    counts = _new_moscow_counts()
    if hit.block["block_type"] == "table":
        tm = hit.block.get("table_meta")
        if tm and tm.get("cells"):
            for row in tm["cells"]:
                for cell in row:
                    if not cell:
                        continue
                    m = _MOSCOW_BARE_CELL_RE.match(cell.strip())
                    if m:
                        _bucket_moscow(m.group(1), counts)
                        break  # one count per row
    else:
        for m in _MOSCOW_RE.finditer(hit.block["text"]):
            _bucket_moscow(m.group(0), counts)
    return counts


def _moscow_by_section(hits: list[RetrievalHit]) -> dict[str, dict[str, int]]:
    """Split the MoSCoW distribution per requirement section.

    Each hit is resolved to a section (fr / nfr / uc / other) via _hit_section,
    so it stays consistent with the count breakdown: a combined/unlabelled
    block is attributed to FR/NFR only when its rows carry a single,
    unambiguous FR/NFR ID type. Its priority labels are then added to that
    section's tally, letting the notes report Must/Should/Could/Won't
    separately for functional and non-functional requirements.
    """
    dist: dict[str, dict[str, int]] = {
        sec: _new_moscow_counts() for sec in ("fr", "nfr", "uc", "other")
    }
    for hit in hits:
        sec = _hit_section(hit)
        block_counts = _block_moscow_counts(hit)
        for tier, n in block_counts.items():
            dist[sec][tier] += n
    return dist


def _format_moscow(counts: dict[str, int]) -> str:
    """Render a MoSCoW tally as 'Must: a, Should: b, Could: c, Won't: d'.

    Only non-zero tiers are included; returns "" when nothing is present.
    """
    order = (
        ("must", "Must"),
        ("should", "Should"),
        ("could", "Could"),
        ("wont", "Won't"),
    )
    return ", ".join(f"{label}: {counts[key]}" for key, label in order if counts[key])


def check_requirements(spec: CriterionSpec, hits: list[RetrievalHit]) -> CriterionResult:
    """Count distinct requirements and detect prioritization evidence.

    Counts are estimates: table MoSCoW labels and explicit requirement IDs
    are the most reliable signals. Use cases and numbered bullets supplement.

    Verdicts follow minimum_count=15 and strong_from=25 from the spec.
    """
    evidence: list[EvidenceRef] = []
    total = 0
    has_prio = False
    notes: list[str] = []

    # Attribute each counting block to a section using its OWN heading_path
    # (see _classify_section). Unlike the previous running-context approach,
    # an unmarked block (e.g. a "Constraints" table) no longer inherits the
    # section of whatever block preceded it, and Dutch/English/"(non-)"
    # spellings are all recognised.
    #
    # For combined/unlabelled sections ("other") the type is recovered only
    # from a reliable per-row signal — FR-/NFR- item IDs — never guessed from
    # heading context. A block mixing both ID types is split by its ID counts;
    # the remainder stays "other".
    sections: dict[str, int] = {"fr": 0, "nfr": 0, "uc": 0, "other": 0}

    for hit in sorted(hits, key=lambda h: h.block["block_id"]):
        evidence.append(_make_ref(hit))
        n, p = _req_count_from_block(hit)
        total += n
        has_prio = has_prio or p
        if not n:
            continue

        sec = _classify_section(hit.block["heading_path"])
        if sec != "other":
            sections[sec] += n
            continue

        # Combined/unlabelled: fall back to per-row FR/NFR ID prefixes.
        fr_ids, nfr_ids = _id_type_counts(hit)
        ids_total = len(fr_ids) + len(nfr_ids)
        is_table = hit.block["block_type"] == "table"
        # Trust IDs as the item list for tables, or when they cover the count
        # (i.e. the block's items ARE those IDs) — not incidental mentions.
        if ids_total and (is_table or ids_total >= n):
            fr_n = min(len(fr_ids), n)
            nfr_n = min(len(nfr_ids), n - fr_n)
            sections["fr"] += fr_n
            sections["nfr"] += nfr_n
            sections["other"] += n - fr_n - nfr_n
        else:
            sections["other"] += n

    # Supplementary prioritization signal from headings / MoSCoW-labeled sections.
    all_text = _all_text(hits)
    if any(t in all_text for t in ("prioriteit", "moscow", "prioritering", "prio")):
        has_prio = True

    # Add use case blocks only when the primary count is very low,
    # to avoid double-counting with table rows that already include use cases.
    uc_count = _count_use_case_blocks(hits)
    if uc_count > 0 and total < 5:
        # Use-case blocks still feed the internal total that drives the status
        # verdict, but this is no longer surfaced as a note: per request, the
        # handoff exposes no requirement counting.
        total += uc_count
        sections["uc"] += uc_count

    prio_str = f"Prioritering: {'ja' if has_prio else 'nee'}."

    # Status is PRESENCE/PRIORITISATION-based, never count-threshold-based.
    # Counting requirement items is no longer part of the design, so it must not
    # drive the verdict. We only ask: are there requirements at all, and is there
    # MoSCoW prioritisation? The item count is still computed internally for
    # debug use but is neither exposed nor used for the verdict. The MoSCoW
    # prioritering split (Note B below) is kept — that is the distribution the
    # user wants.
    if total == 0:
        status: CriterionStatus = "missing"
        notes.append("Geen herkenbare requirements gevonden.")
    elif has_prio:
        status = "sufficient"
        notes.append(f"Requirements-sectie aanwezig. {prio_str}")
    else:
        status = "partial"
        notes.append(f"Requirements aanwezig, maar geen prioritering. {prio_str}")

    # Note A: only the qualitative 'cannot split' observation is kept — it is a
    # coverage note, not a count.
    if total > 0:
        fr, nfr, other = sections["fr"], sections["nfr"], sections["other"]
        if not fr and not nfr and other > 0:
            # Requirements exist but no block carries a functional/non-functional
            # heading — don't fabricate a split.
            notes.append(
                "Functioneel en niet-functioneel niet apart te onderscheiden "
                "(gecombineerde of ongelabelde requirements-sectie)."
            )

    # Note B: MoSCoW distribution, split per requirement type when possible.
    if has_prio and total > 0:
        mdist = _moscow_by_section(hits)
        fr_prio = _format_moscow(mdist["fr"])
        nfr_prio = _format_moscow(mdist["nfr"])

        if fr_prio:
            notes.append(f"Functioneel prioriteiten: {fr_prio}.")
        if nfr_prio:
            notes.append(f"Niet-functioneel prioriteiten: {nfr_prio}.")

        # Fall back to a single overall distribution only when no functional or
        # non-functional section carried priority labels (combined/unlabelled).
        if not fr_prio and not nfr_prio:
            overall = _new_moscow_counts()
            for sec_counts in mdist.values():
                for tier, n in sec_counts.items():
                    overall[tier] += n
            overall_str = _format_moscow(overall)
            if overall_str:
                notes.append(f"Prioriteitsverdeling: {overall_str}.")

    missing_signals: list[str] = []
    if not has_prio:
        missing_signals.append("prioritization")

    return CriterionResult(
        criterion_key=spec.key,
        status=status,
        stoplight=_status_to_stoplight(status),
        is_blocker=spec.is_blocker,
        evidence=evidence[:8],
        count=total,
        notes=notes,
        missing_signals=missing_signals,
    )


# ---------------------------------------------------------------------------
# Criterion 4: Taalkeuze & consequenties
# ---------------------------------------------------------------------------


def check_taalkeuze(spec: CriterionSpec, hits: list[RetrievalHit]) -> CriterionResult:
    """Detect language choice + consequences of that choice.

    Verdicts:
    - missing:    neither signal found
    - partial:    only one signal found (language OR consequence)
    - sufficient: both language choice and consequences present
    - strong:     both present + either research support or ≥ 3 consequence signals
    """
    all_text = _all_text(hits)

    language_found = _found_terms(_LANGUAGE_TERMS, all_text)
    consequence_found = _found_terms(_CONSEQUENCE_TERMS, all_text)
    research_found = _found_terms(_RESEARCH_TERMS, all_text)

    evidence: list[EvidenceRef] = []
    for hit in hits:
        text = _text_of(hit)
        if _found_terms(_LANGUAGE_TERMS, text) or _found_terms(_CONSEQUENCE_TERMS, text):
            evidence.append(_make_ref(hit))

    notes: list[str] = []

    has_language = bool(language_found)
    has_consequence = bool(consequence_found)

    if not has_language and not has_consequence:
        status: CriterionStatus = "missing"
        notes.append("Geen taalkeuze of gevolgen van die keuze gevonden.")

    elif not has_language:
        status = "partial"
        notes.append(
            f"Gevolg-signalen aanwezig ({', '.join(consequence_found[:3])}), "
            "maar geen expliciete taalkeuze vermeld."
        )

    elif not has_consequence:
        status = "partial"
        notes.append(
            f"Taalkeuze vermeld ({', '.join(language_found[:2])}), "
            "maar geen gevolgen van die keuze beschreven."
        )

    else:
        # Both signals present; upgrade to strong when well-underpinned.
        if research_found:
            status = "strong"
            notes.append(
                f"Taalkeuze ({', '.join(language_found[:2])}) en gevolgen "
                f"({', '.join(consequence_found[:3])}) aanwezig, onderbouwd met onderzoek."
            )
        elif len(consequence_found) >= 3:
            status = "strong"
            notes.append(
                f"Taalkeuze ({', '.join(language_found[:2])}) en meerdere gevolgen "
                f"({', '.join(consequence_found[:3])}) goed uitgewerkt."
            )
        else:
            status = "sufficient"
            notes.append(
                f"Taalkeuze ({', '.join(language_found[:2])}) en gevolgen "
                f"({', '.join(consequence_found[:2])}) aanwezig."
            )

    missing_signals: list[str] = []
    if not has_language:
        missing_signals.append("language_choice")
    if not has_consequence:
        missing_signals.append("consequences")

    return CriterionResult(
        criterion_key=spec.key,
        status=status,
        stoplight=_status_to_stoplight(status),
        is_blocker=spec.is_blocker,
        evidence=evidence[:8],
        count=None,
        notes=notes,
        missing_signals=missing_signals,
    )


# ---------------------------------------------------------------------------
# Criterion 5: Security
# ---------------------------------------------------------------------------


def check_security(spec: CriterionSpec, hits: list[RetrievalHit]) -> CriterionResult:
    """Count distinct concrete security mechanisms.

    Verdicts:
    - missing:    no security signal at all
    - partial:    generic security language only (no concrete mechanism)
    - sufficient: ≥ 1 concrete mechanism (minimum_count=1 from spec)
    - strong:     ≥ 3 distinct concrete mechanisms (strong_from=3 from spec)
    """
    all_text = _all_text(hits)

    concrete_found = _found_terms(_CONCRETE_SECURITY, all_text)
    generic_found = _found_terms(_GENERIC_SECURITY, all_text)

    evidence: list[EvidenceRef] = []
    for hit in hits:
        text = _text_of(hit)
        if _found_terms(_CONCRETE_SECURITY, text) or _found_terms(_GENERIC_SECURITY, text):
            evidence.append(_make_ref(hit))

    count = len(concrete_found)
    notes: list[str] = []

    # Status is PRESENCE-based, never count-threshold-based. We only ask: is at
    # least one CONCRETE mechanism named? Generic-only language is partial; no
    # signal at all is missing. Concrete mechanisms are NAMED rather than counted,
    # so the abandoned counting no longer drives the verdict or the notes. The
    # count is still computed for internal/debug use only.
    if not concrete_found and not generic_found:
        status: CriterionStatus = "missing"
        notes.append("Geen security-signalen gevonden.")
    elif not concrete_found:
        status = "partial"
        notes.append(
            f"Generieke security-termen aanwezig ({', '.join(generic_found[:3])}), "
            "maar geen concreet beveiligingsmechanisme benoemd."
        )
    else:
        status = "sufficient"
        notes.append(
            "Concreet beveiligingsmechanisme(n) gevonden: "
            f"{', '.join(concrete_found[:6])}."
        )

    missing_signals: list[str] = []
    if not concrete_found:
        missing_signals.append("concrete_mechanism")

    return CriterionResult(
        criterion_key=spec.key,
        status=status,
        stoplight=_status_to_stoplight(status),
        is_blocker=spec.is_blocker,
        evidence=evidence[:8],
        count=count,
        notes=notes,
        missing_signals=missing_signals,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_CHECKS: Final[dict] = {
    "beperking": check_beperking,
    "stakeholders": check_stakeholders,
    "requirements": check_requirements,
    "taalkeuze": check_taalkeuze,
    "security": check_security,
}


def run_check_for_criterion(
    criterion_key: str,
    hits: list[RetrievalHit],
) -> CriterionResult:
    """Run the deterministic check for one criterion by key.

    Args:
        criterion_key: One of the 5 CAPS criterion keys
            (beperking, stakeholders, requirements, taalkeuze, security).
        hits: Retrieval hits for this criterion, as returned by
            retrieve_for_criterion or retrieve_all_criteria.

    Returns:
        CriterionResult with verdict, stoplight, evidence, notes, and missing_signals.

    Raises:
        KeyError: if criterion_key is not one of the 5 known keys.
    """
    spec = CRITERIA_BY_KEY[criterion_key]
    return _CHECKS[criterion_key](spec, hits)


def run_all_checks(
    candidates: dict[str, list[RetrievalHit]],
) -> dict[str, CriterionResult]:
    """Run all 5 criterion checks in one call.

    Args:
        candidates: Output of retrieve_all_criteria —
            dict[criterion_key → list[RetrievalHit]].

    Returns:
        dict[criterion_key → CriterionResult], one entry per criterion.
    """
    return {key: run_check_for_criterion(key, hits) for key, hits in candidates.items()}
