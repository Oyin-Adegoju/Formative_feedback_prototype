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

# Requirement ID patterns: FR-01, NFR-01, UC-01, USC-01, REQ-01, CONSTR-01, etc.
_REQ_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(FR|NFR|USC|UC|US|REQ|CONSTR|CON|F|NF)[-\s]?\d{1,3}\b",
    re.IGNORECASE,
)

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
    manual_review = False

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
        manual_review = True

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
            # Thin research: only headings or short fragments → flag for review.
            if len(research_body_hits) == 0 or (
                len(research_body_hits) == 1 and len(res_signals) == 1
            ):
                manual_review = True
                notes.append(
                    "Onderbouwingsbewijs smal of alleen via heading — verifieer inhoud."
                )

    return CriterionResult(
        criterion_key=spec.key,
        status=status,
        stoplight=_status_to_stoplight(status),
        is_blocker=spec.is_blocker,
        evidence=evidence[:8],
        count=None,
        notes=notes,
        manual_review=manual_review,
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
    manual_review = False

    minimum = spec.minimum_count or 4
    strong_from = spec.strong_from or 6
    has_bi = has_belang and has_invloed

    bi_str = (
        f"Belang: {'ja' if has_belang else 'nee'}, "
        f"Invloed: {'ja' if has_invloed else 'nee'}."
    )

    if count == 0:
        status: CriterionStatus = "missing"
        notes.append("Geen stakeholders herkend in de retrievalhits.")

    elif count < minimum:
        status = "partial"
        notes.append(
            f"{count} unieke stakeholder(s) herkend (minimum {minimum}). {bi_str}"
        )
        if count >= minimum - 1:
            manual_review = True
            notes.append("Één stakeholder onder het minimum — grenswaardegeval.")

    elif count < strong_from:
        if has_bi:
            status = "sufficient"
            notes.append(
                f"{count} stakeholder(s) herkend met belang en invloed beschreven."
            )
            if count == minimum:
                manual_review = True
                notes.append(
                    "Exact op minimum (4) — verifieer tabel op volledigheid."
                )
        else:
            # Count reached minimum but belang/invloed missing → partial.
            status = "partial"
            notes.append(
                f"{count} stakeholder(s) herkend, maar belang en/of invloed "
                f"ontbreekt. {bi_str}"
            )
            manual_review = True

    else:  # count >= strong_from
        if has_bi:
            status = "strong"
            notes.append(
                f"{count} stakeholder(s) herkend — boven drempel, "
                "met belang en invloed gedocumenteerd."
            )
            if count == strong_from:
                manual_review = True
                notes.append("Exact op grenswaarde voor 'goed' (6).")
        else:
            status = "sufficient"
            notes.append(
                f"{count} stakeholder(s) herkend, maar belang en/of invloed "
                f"ontbreekt. {bi_str}"
            )
            manual_review = True

    return CriterionResult(
        criterion_key=spec.key,
        status=status,
        stoplight=_status_to_stoplight(status),
        is_blocker=spec.is_blocker,
        evidence=evidence[:8],
        count=count,
        notes=notes,
        manual_review=manual_review,
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
        return max(moscow_count, len(req_ids)), has_prio
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
    manual_review = False

    for hit in hits:
        evidence.append(_make_ref(hit))
        n, p = _req_count_from_block(hit)
        total += n
        has_prio = has_prio or p

    # Supplementary prioritization signal from headings / MoSCoW-labeled sections.
    all_text = _all_text(hits)
    if any(t in all_text for t in ("prioriteit", "moscow", "prioritering", "prio")):
        has_prio = True

    # Add use case blocks only when the primary count is very low,
    # to avoid double-counting with table rows that already include use cases.
    uc_count = _count_use_case_blocks(hits)
    if uc_count > 0 and total < 5:
        total += uc_count
        notes.append(
            f"Use case beschrijvingen ({uc_count}) meegeteld als requirements "
            "bij laag primair aantal."
        )

    prio_str = f"Prioritering: {'ja' if has_prio else 'nee'}."
    minimum = spec.minimum_count or 15
    strong_from = spec.strong_from or 25

    if total == 0:
        status: CriterionStatus = "missing"
        notes.append("Geen herkenbare requirements gevonden.")

    elif total < minimum:
        status = "partial"
        notes.append(f"Geschat {total} requirement-items (minimum {minimum}). {prio_str}")
        # Flag edge cases near the threshold.
        if minimum - 3 <= total <= minimum + 2:
            manual_review = True
            notes.append(
                f"Telling ({total}) dicht bij minimum ({minimum}) — handmatig verificeren."
            )

    elif total < strong_from:
        status = "sufficient"
        notes.append(f"Geschat {total} requirement-items. {prio_str}")
        if total <= minimum + 2:
            manual_review = True
            notes.append(f"Telling ({total}) net boven minimum — grenswaardegeval.")

    else:
        status = "strong"
        notes.append(f"Geschat {total} requirement-items — ruim gedekt. {prio_str}")

    return CriterionResult(
        criterion_key=spec.key,
        status=status,
        stoplight=_status_to_stoplight(status),
        is_blocker=spec.is_blocker,
        evidence=evidence[:8],
        count=total,
        notes=notes,
        manual_review=manual_review,
    )
