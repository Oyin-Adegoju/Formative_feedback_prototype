"""packet_builder.py — builds per-criterion evidence packets from CAPS artifacts.

Takes a CapsPipelineArtifacts object (retrieval candidates + per-criterion
results + final run result) and produces one EvidencePacket per criterion.

Architecture position:
    CapsPipelineArtifacts → build_evidence_packets → dict[str, EvidencePacket]

CAPS is the source of truth for every verdict (status, count, stoplight,
manual_review).  This module only SELECTS and SHAPES evidence — it never
re-judges, re-scores, or overrides any CAPS decision.

Selection limits (evidence items per criterion):
    beperking      3   — limitation block + research block + one more
    stakeholders   2   — stakeholder table preferred; paragraphs as fallback
    requirements   3   — FR / NFR / UC tables preferred in document order
    taalkeuze      3   — blocks with both choice AND consequence signals first
    security       3   — blocks with concrete mechanisms preferred
"""

from __future__ import annotations

import re
from typing import Final

from src.caps.caps import CapsPipelineArtifacts
from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS, CriterionSpec
from src.caps.models import CriterionResult
from src.caps.retrieval import RetrievalHit
from src.feedback.evidence import EvidenceItem, EvidencePacket, SignalClass

# ---------------------------------------------------------------------------
# Per-criterion item limits
# ---------------------------------------------------------------------------

_MAX_ITEMS: Final[dict[str, int]] = {
    "beperking":    3,
    "stakeholders": 2,
    "requirements": 3,
    "taalkeuze":    3,
    "security":     3,
}

# ---------------------------------------------------------------------------
# Lightweight signal term sets
# (selection / labelling only — CAPS judgement is authoritative)
# ---------------------------------------------------------------------------

_BEP_LIMITATION: Final[frozenset[str]] = frozenset({
    "beperking", "slechtziend", "blind", "doof", "dyslexie",
    "visuele beperking", "motorische", "auditieve", "cognitieve",
    "afbakening", "doelgroep met beperking", "specifieke doelgroep",
    "niche", "focus op", "gekozen doelgroep",
})

_BEP_RESEARCH: Final[frozenset[str]] = frozenset({
    "deskresearch", "desk research", "literatuuronderzoek", "literatuur",
    "literatuurlijst", "bronvermelding", "bronnenlijst", "bronnen", "bron",
    "onderzoek", "verkenning", "marktonderzoek", "user research",
    "wetenschappelijk",
})

_TAAL_CHOICE: Final[frozenset[str]] = frozenset({
    "taalkeuze", "taalversie", "meertalig", "in het nederlands",
    "in het engels", "nederlandstalig", "dutch", "english",
    "webshop in het", "taal van de",
    # Bare language names — aligned with checks.py _LANGUAGE_TERMS
    "nederlands", "engels", "duits", "french",
})

_TAAL_CONSEQUENCE: Final[frozenset[str]] = frozenset({
    "bereik", "vertaalkosten", "lokalisatie", "consequentie",
    "begrijpelijk", "taalbarrière", "taalbarrier", "vertaling",
    "meertaligheid", "internationaal", "beïnvloeden", "beïnvloed",
    "doelgroepbereik",
})

_SEC_CONCRETE: Final[frozenset[str]] = frozenset({
    "authenticatie", "autorisatie", "encryptie", "avg", "gdpr",
    "owasp", "https", "ssl", "tls", "jwt", "2fa", "two-factor",
    "xss", "csrf", "firewall", "bcrypt", "hashing", "wachtwoord",
    "toegangscontrole", "risicoanalyse", "privacyverklaring",
    "beveiligingsaudit",
})

_SEC_GENERIC: Final[frozenset[str]] = frozenset({
    "security", "beveiliging", "veiligheid", "veilig", "privacy",
})

# Requirement ID pattern — reused from checks.py logic, kept local to avoid
# coupling to checks.py internals.
_REQ_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(FR|NFR|USC|UC|US|REQ|CONSTR|CON|F|NF)[-\s]?\d{1,3}\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Excerpt helpers
# ---------------------------------------------------------------------------


def _excerpt_paragraph(text: str, max_len: int = 200) -> str:
    """Normalise whitespace and trim to max_len characters."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _excerpt_table(block: dict, max_data_rows: int = 4) -> str:
    """Format a table as 'header | header\\nrow | row…' for compact display.

    Includes header_row (if present) then up to max_data_rows data rows.
    Falls back to _excerpt_paragraph when table_meta is absent.
    """
    tm = block.get("table_meta")
    if not tm:
        return _excerpt_paragraph(block["text"])

    lines: list[str] = []
    header = tm.get("header_row")
    if header:
        lines.append(header)

    cells = tm.get("cells") or []
    for row in cells[:max_data_rows]:
        non_null = [c for c in row if c is not None]
        if non_null:
            lines.append(" | ".join(non_null))

    remaining = len(cells) - max_data_rows
    if remaining > 0:
        lines.append(f"… ({remaining} meer rijen)")

    return "\n".join(lines) if lines else _excerpt_paragraph(block["text"])


def _excerpt_for(hit: RetrievalHit) -> str:
    """Dispatch to the appropriate excerpt shaper for a retrieval hit."""
    b = hit.block
    if b["block_type"] == "table":
        return _excerpt_table(b)
    return _excerpt_paragraph(b["text"])

# ---------------------------------------------------------------------------
# Consecutive fragment merging
# ---------------------------------------------------------------------------
 
 
def _merge_consecutive_fragments(
    hits: list[RetrievalHit],
    max_fragment_chars: int = 80,
    max_total_chars: int = 240,
) -> list[RetrievalHit]:
    """Merge consecutive short paragraph/bullet fragments sharing the same heading_path.
 
    PDF parsers sometimes split a section into many tiny blocks (e.g. a
    label on one line, its value on the next).  Merging produces a readable
    excerpt without dumping many near-empty items.
 
    Only merges when:
      - both blocks are paragraph or bullet
      - both are ≤ max_fragment_chars chars
      - both share the same heading_path
      - the combined length stays ≤ max_total_chars
 
    Returns a new list; input hits are not mutated.
    """
    if not hits:
        return []
 
    ordered = sorted(hits, key=lambda h: h.block["block_id"])
    result: list[RetrievalHit] = []
    i = 0
 
    while i < len(ordered):
        hit = ordered[i]
        bt = hit.block["block_type"]
        t_len = len(hit.block["text"].strip())
 
        if bt not in ("paragraph", "bullet") or t_len > max_fragment_chars:
            result.append(hit)
            i += 1
            continue
 
        fragments = [hit]
        j = i + 1
        running = t_len
 
        while j < len(ordered):
            nxt = ordered[j]
            n_len = len(nxt.block["text"].strip())
            if (
                nxt.block["block_type"] in ("paragraph", "bullet")
                and n_len <= max_fragment_chars
                and nxt.block["heading_path"] == hit.block["heading_path"]
                and running + n_len + 1 <= max_total_chars
            ):
                fragments.append(nxt)
                running += n_len + 1
                j += 1
            else:
                break
 
        if len(fragments) > 1:
            merged_text = " ".join(f.block["text"].strip() for f in fragments)
            merged_block: dict = dict(fragments[0].block)
            merged_block["text"] = merged_text
            merged_hit = RetrievalHit(
                block=merged_block,  # type: ignore[arg-type]
                score=max(f.score for f in fragments),
                matched_heading_hints=list(
                    dict.fromkeys(h for f in fragments for h in f.matched_heading_hints)
                ),
                matched_text_hints=list(
                    dict.fromkeys(h for f in fragments for h in f.matched_text_hints)
                ),
                reasons=fragments[0].reasons,
            )
            result.append(merged_hit)
            i = j
        else:
            result.append(hit)
            i += 1
 
    return result
 
 
 # ---------------------------------------------------------------------------
# Hit classification helpers
# ---------------------------------------------------------------------------
 
 
def _is_heading_only(hit: RetrievalHit) -> bool:
    """True when the block is a heading with no text-hint matches.
 
    These blocks signal section structure but carry no substantive content
    evidence — candidates for absent_marker classification.
    """
    return hit.block["block_type"] == "heading" and not hit.matched_text_hints
 
 
def _has_terms(hit: RetrievalHit, terms: frozenset[str]) -> bool:
    """Check whether any term from the set appears in the block's text."""
    text = hit.block["text"].lower()
    return any(t in text for t in terms)
 
 
def _matched_terms(hit: RetrievalHit, terms: frozenset[str], max_n: int = 3) -> list[str]:
    """Return up to max_n terms from the set that appear in the block's text."""
    text = hit.block["text"].lower()
    return [t for t in terms if t in text][:max_n]
 
 
def _sort_content_first(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    """Sort: content blocks before heading-only blocks; within each, higher score first.
 
    Stable on block_id as a tiebreaker for deterministic ordering across runs.
    """
    return sorted(
        hits,
        key=lambda h: (int(_is_heading_only(h)), -h.score, h.block["block_id"]),
    )
 
 
def _make_item(
    hit: RetrievalHit,
    reason: str,
    signal_class: SignalClass,
) -> EvidenceItem:
    """Construct an EvidenceItem from a RetrievalHit."""
    b = hit.block
    return EvidenceItem(
        block_id=b["block_id"],
        page_no=b["page_no"],
        block_type=b["block_type"],
        heading_path=list(b["heading_path"]),
        excerpt=_excerpt_for(hit),
        selection_reason=reason,
        signal_class=signal_class,
    )
 # ---------------------------------------------------------------------------
# Criterion 1: Beperking & deskresearch
# ---------------------------------------------------------------------------
 
 
def _build_beperking_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
) -> EvidencePacket:
    """Select up to 3 items: best limitation block + best research block + one extra.
 
    Merges consecutive short fragments (constraint lists) before selection so
    that PDF-split label/value pairs appear as one readable excerpt.
    """
    MAX = _MAX_ITEMS["beperking"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
 
    processed = _merge_consecutive_fragments(hits)
 
    lim_hits = _sort_content_first([
        h for h in processed
        if _has_terms(h, _BEP_LIMITATION) and not _is_heading_only(h)
    ])
    res_hits = _sort_content_first([
        h for h in processed
        if _has_terms(h, _BEP_RESEARCH) and not _is_heading_only(h)
    ])
    heading_hits = [h for h in processed if _is_heading_only(h)]
 
    if status == "missing":
        # Show one weak content hit + one absent_marker heading when available.
        content = _sort_content_first([h for h in processed if not _is_heading_only(h)])
        for h in content[:1]:
            items.append(_make_item(h, "Geen voldoende bewijs gevonden", "weak"))
        for h in heading_hits[:1]:
            items.append(_make_item(
                h,
                "Sectieheading gevonden maar geen inhoud herkend",
                "absent_marker",
            ))
        missing_sigs.append("Geen expliciete beperking of doelgroepkeuze beschreven")
        missing_sigs.append("Geen deskresearch of bronvermelding aangetroffen")
 
    elif status == "partial":
        if lim_hits and not res_hits:
            for h in lim_hits[:2]:
                terms = _matched_terms(h, _BEP_LIMITATION)
                items.append(_make_item(h, f"Beperking/doelgroep: {', '.join(terms)}", "positive"))
            missing_sigs.append("Geen deskresearch of bronvermelding aangetroffen")
        elif res_hits and not lim_hits:
            for h in res_hits[:2]:
                terms = _matched_terms(h, _BEP_RESEARCH)
                items.append(_make_item(h, f"Onderbouwing: {', '.join(terms)}", "positive"))
            missing_sigs.append("Geen expliciete beperking of doelgroepkeuze beschreven")
        else:
            for h in _sort_content_first(processed)[:2]:
                items.append(_make_item(h, "Partieel bewijs aangetroffen", "weak"))
 
    else:  # sufficient / strong
        seen: set[str] = set()
 
        def _add(h: RetrievalHit, reason: str) -> None:
            if len(items) < MAX and h.block["block_id"] not in seen:
                seen.add(h.block["block_id"])
                items.append(_make_item(h, reason, "positive"))
 
        for h in lim_hits[:1]:
            terms = _matched_terms(h, _BEP_LIMITATION)
            _add(h, f"Beperking/doelgroep: {', '.join(terms)}")
 
        for h in res_hits:
            if h.block["block_id"] not in seen:
                terms = _matched_terms(h, _BEP_RESEARCH)
                _add(h, f"Onderbouwing/deskresearch: {', '.join(terms)}")
                break
 
        for h in _sort_content_first(processed):
            if len(items) >= MAX:
                break
            if h.block["block_id"] not in seen:
                l_t = _matched_terms(h, _BEP_LIMITATION)
                r_t = _matched_terms(h, _BEP_RESEARCH)
                all_t = l_t + r_t
                reason = f"Aanvullend bewijs: {', '.join(all_t[:3])}" if all_t else "Aanvullende context"
                _add(h, reason)
 
    return EvidencePacket(
        criterion_key=spec.key,
        manual_review=cr.manual_review,
        notes=cr.notes,
        evidence_items=items,
        missing_signals=missing_sigs,
    )
# ---------------------------------------------------------------------------
# Criterion 2: Stakeholders
# ---------------------------------------------------------------------------
 
 
def _build_stakeholders_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
) -> EvidencePacket:
    """Select up to 2 items, strongly preferring table blocks with role data.
 
    Table blocks with belang/invloed columns are the primary source.
    Paragraph/bullet blocks with role keywords serve as fallback.
    """
    MAX = _MAX_ITEMS["stakeholders"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
 
    table_hits = _sort_content_first(
        [h for h in hits if h.block["block_type"] == "table"]
    )
    para_hits = _sort_content_first(
        [h for h in hits if h.block["block_type"] in ("paragraph", "bullet")]
    )
    heading_hits = [h for h in hits if _is_heading_only(h)]
 
    all_text = " ".join(h.block["text"].lower() for h in hits)
    has_belang = any(t in all_text for t in ("belang", "concern", "interesse"))
    has_invloed = any(t in all_text for t in ("invloed", "prioriter", "macht"))
 
    count = cr.count or 0
    minimum = spec.minimum_count or 4
 
    if status == "missing":
        for h in heading_hits[:1]:
            items.append(_make_item(
                h,
                "Stakeholder-sectieheading gevonden maar geen tabelinhoud herkend",
                "absent_marker",
            ))
        missing_sigs.append("Geen stakeholders herkend in de aangeleverde evidence")
 
    elif status == "partial":
        if table_hits:
            h = table_hits[0]
            tm = h.block.get("table_meta")
            n_rows = len(tm.get("cells") or []) if tm else 0
            items.append(_make_item(
                h,
                f"Stakeholder-tabel ({n_rows} rijen) — onvolledig ({count} van {minimum} min.)",
                "weak",
            ))
        elif para_hits:
            h = para_hits[0]
            items.append(_make_item(
                h,
                f"Stakeholder-vermelding in tekst ({count} herkend)",
                "weak",
            ))
        if count < minimum:
            missing_sigs.append(
                f"Slechts {count} stakeholder(s) gevonden (minimum {minimum})"
            )
        if not has_belang:
            missing_sigs.append("Belang per stakeholder niet beschreven")
        if not has_invloed:
            missing_sigs.append("Invloed per stakeholder niet beschreven")
 
    else:  # sufficient / strong
        candidates = (table_hits + para_hits)[:MAX]
        for h in candidates:
            if h.block["block_type"] == "table":
                tm = h.block.get("table_meta")
                n_rows = len(tm.get("cells") or []) if tm else 0
                reason = f"Stakeholder-tabel ({n_rows} rijen)"
            else:
                reason = "Stakeholder-beschrijving in lopende tekst"
            items.append(_make_item(h, reason, "positive"))
 
        if not has_belang:
            missing_sigs.append("Belang per stakeholder niet expliciet beschreven")
        if not has_invloed:
            missing_sigs.append("Invloed per stakeholder niet expliciet beschreven")
 
    return EvidencePacket(
        criterion_key=spec.key,
        manual_review=cr.manual_review,
        notes=cr.notes,
        evidence_items=items,
        missing_signals=missing_sigs,
    )
 # ---------------------------------------------------------------------------
# Criterion 3: Requirements
# ---------------------------------------------------------------------------
 
 
def _prio_row_count(hit: RetrievalHit) -> int:
    """Count rows in a table block whose first matching cell starts with a MoSCoW label."""
    tm = hit.block.get("table_meta")
    if not tm or not tm.get("cells"):
        return 0
    count = 0
    for row in tm["cells"]:
        for cell in row:
            if cell and any(
                cell.strip().lower().startswith(w)
                for w in ("must", "should", "could", "shoul", "won")
            ):
                count += 1
                break
    return count
 
 
def _req_id_count(hit: RetrievalHit) -> int:
    """Count requirement IDs (FR-01, NFR-01, …) in a block's text."""
    return len(_REQ_ID_RE.findall(hit.block["text"]))
 
 
def _build_requirements_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
) -> EvidencePacket:
    """Select up to 3 items, preferring tables with MoSCoW priority labels.
 
    Tables with the most prio-rows rank first; ties broken by retrieval score.
    Paragraph / bullet blocks fill remaining slots.
    """
    MAX = _MAX_ITEMS["requirements"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
 
    table_hits = sorted(
        [h for h in hits if h.block["block_type"] == "table"],
        key=lambda h: (-_prio_row_count(h), -h.score, h.block["block_id"]),
    )
    para_hits = _sort_content_first(
        [h for h in hits if h.block["block_type"] in ("paragraph", "bullet")]
    )
    heading_hits = [h for h in hits if _is_heading_only(h)]
 
    all_text = " ".join(h.block["text"].lower() for h in hits)
    has_prio = any(
        t in all_text for t in ("must", "should", "could", "moscow", "prioriteit")
    )
    count = cr.count or 0
    minimum = spec.minimum_count or 15
 
    if status == "missing":
        for h in heading_hits[:1]:
            items.append(_make_item(
                h,
                "Requirements-sectieheading gevonden maar geen inhoud herkend",
                "absent_marker",
            ))
        missing_sigs.append(f"Geen herkenbare requirements gevonden (minimum {minimum})")
        if not has_prio:
            missing_sigs.append("Geen MoSCoW-prioritering aangetroffen")
 
    elif status == "partial":
        candidates = (table_hits + para_hits)[:MAX]
        for h in candidates:
            pcount = _prio_row_count(h)
            rcount = _req_id_count(h)
            if h.block["block_type"] == "table":
                if pcount > 0:
                    reason = f"Requirements-tabel met {pcount} MoSCoW-rijen"
                    sc: SignalClass = "positive"
                elif rcount > 0:
                    reason = f"Requirements-tabel ({rcount} req-IDs)"
                    sc = "positive"
                else:
                    reason = "Requirements-tabel (beperkte inhoud herkend)"
                    sc = "weak"
            else:
                reason = "Requirements in tekst"
                sc = "weak"
            items.append(_make_item(h, reason, sc))
        missing_sigs.append(f"Slechts ~{count} requirements gevonden (minimum {minimum})")
        if not has_prio:
            missing_sigs.append("Geen MoSCoW-prioritering aangetroffen")
 
    else:  # sufficient / strong
        candidates = (table_hits + para_hits)[:MAX]
        seen: set[str] = set()
        for h in candidates:
            bid = h.block["block_id"]
            if bid in seen:
                continue
            seen.add(bid)
            pcount = _prio_row_count(h)
            rcount = _req_id_count(h)
            tm = h.block.get("table_meta")
            n_rows = len(tm.get("cells") or []) if tm else 0
            if h.block["block_type"] == "table":
                if pcount > 0:
                    reason = f"Requirements-tabel: {n_rows} rijen, {pcount} MoSCoW-labels"
                else:
                    reason = f"Requirements-tabel: {n_rows} rijen, {rcount} req-IDs"
            else:
                reason = f"Requirements in tekst ({rcount} IDs herkend)"
            items.append(_make_item(h, reason, "positive"))
        if not has_prio:
            missing_sigs.append("Geen MoSCoW-prioritering aangetroffen")
 
    return EvidencePacket(
        criterion_key=spec.key,
        manual_review=cr.manual_review,
        notes=cr.notes,
        evidence_items=items,
        missing_signals=missing_sigs,
    )
 
# ---------------------------------------------------------------------------
# Criterion 4: Taalkeuze & consequenties
# ---------------------------------------------------------------------------
 
 
def _build_taalkeuze_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
) -> EvidencePacket:
    """Select up to 3 items, preferring blocks with BOTH choice AND consequence signals.
 
    Merges short consecutive fragments before selection to handle documents
    where the language section is split into many small paragraphs.
    """
    MAX = _MAX_ITEMS["taalkeuze"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
 
    processed = _merge_consecutive_fragments(hits)
 
    choice_hits = _sort_content_first([
        h for h in processed
        if _has_terms(h, _TAAL_CHOICE) and not _is_heading_only(h)
    ])
    cons_hits = _sort_content_first([
        h for h in processed
        if _has_terms(h, _TAAL_CONSEQUENCE) and not _is_heading_only(h)
    ])
    heading_hits = [h for h in processed if _is_heading_only(h)]
 
    all_text = " ".join(h.block["text"].lower() for h in hits)
    has_choice = any(t in all_text for t in _TAAL_CHOICE)
    has_consequence = any(t in all_text for t in _TAAL_CONSEQUENCE)
 
    if status == "missing":
        for h in heading_hits[:1]:
            items.append(_make_item(
                h,
                "Taalkeuze-sectieheading zonder inhoud aangetroffen",
                "absent_marker",
            ))
        missing_sigs.append("Geen expliciete taalkeuze vermeld")
        missing_sigs.append("Geen gevolgen van de taalkeuze beschreven")
 
    elif status == "partial":
        if has_choice and not has_consequence:
            for h in choice_hits[:2]:
                terms = _matched_terms(h, _TAAL_CHOICE)
                items.append(_make_item(h, f"Taalkeuze: {', '.join(terms)}", "positive"))
            missing_sigs.append(
                "Gevolgen van taalkeuze niet beschreven (bijv. bereik, vertaalkosten, begrijpelijkheid)"
            )
        elif has_consequence and not has_choice:
            for h in cons_hits[:2]:
                terms = _matched_terms(h, _TAAL_CONSEQUENCE)
                items.append(_make_item(h, f"Gevolgen beschreven: {', '.join(terms)}", "positive"))
            missing_sigs.append(
                "Geen expliciete taalkeuze vermeld (bijv. 'de webshop is in het Nederlands')"
            )
        else:
            for h in _sort_content_first(processed)[:2]:
                if not _is_heading_only(h):
                    items.append(_make_item(h, "Partieel taalkeuze-bewijs", "weak"))
            # Ensure missing_signals is always populated for partial status even
            # when our detection set diverges slightly from checks.py's detection.
            notes_lower = " ".join(cr.notes).lower()
            if "geen gevolgen" in notes_lower or "geen consequen" in notes_lower:
                missing_sigs.append("Gevolgen van taalkeuze niet beschreven")
            elif "geen expliciete" in notes_lower or "geen taalkeuze" in notes_lower:
                missing_sigs.append("Geen expliciete taalkeuze vermeld")
            else:
                missing_sigs.append("Taalkeuze of gevolgen niet volledig beschreven")
 
    else:  # sufficient / strong
        seen: set[str] = set()
 
        # Blocks with BOTH signals are the most informative — pick first.
        both = _sort_content_first([
            h for h in processed
            if _has_terms(h, _TAAL_CHOICE)
            and _has_terms(h, _TAAL_CONSEQUENCE)
            and not _is_heading_only(h)
        ])
        for h in both[:1]:
            c_terms = _matched_terms(h, _TAAL_CHOICE)
            k_terms = _matched_terms(h, _TAAL_CONSEQUENCE)
            reason = (
                f"Taalkeuze ({', '.join(c_terms)}) én gevolgen ({', '.join(k_terms)})"
            )
            seen.add(h.block["block_id"])
            items.append(_make_item(h, reason, "positive"))
 
        # Fill remaining slots with the best choice/consequence hits.
        for h in (choice_hits + cons_hits):
            if len(items) >= MAX:
                break
            bid = h.block["block_id"]
            if bid in seen:
                continue
            seen.add(bid)
            c_terms = _matched_terms(h, _TAAL_CHOICE)
            k_terms = _matched_terms(h, _TAAL_CONSEQUENCE)
            if c_terms:
                reason = f"Taalkeuze: {', '.join(c_terms)}"
            else:
                reason = f"Gevolgen: {', '.join(k_terms)}"
            items.append(_make_item(h, reason, "positive"))
 
    return EvidencePacket(
        criterion_key=spec.key,
        manual_review=cr.manual_review,
        notes=cr.notes,
        evidence_items=items,
        missing_signals=missing_sigs,
    )
 
 
# ---------------------------------------------------------------------------
# Criterion 5: Security
# ---------------------------------------------------------------------------
 
 
def _build_security_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
) -> EvidencePacket:
    """Select up to 3 items, preferring blocks with concrete security mechanisms.
 
    Concrete mechanisms (authenticatie, encryptie, AVG, …) rank above generic
    security language (beveiliging, veilig).  Generic-only blocks map to "weak".
    """
    MAX = _MAX_ITEMS["security"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
 
    def _n_concrete(h: RetrievalHit) -> int:
        text = h.block["text"].lower()
        return sum(1 for t in _SEC_CONCRETE if t in text)
 
    concrete_hits = _sort_content_first([
        h for h in hits
        if _n_concrete(h) >= 1 and not _is_heading_only(h)
    ])
    generic_hits = _sort_content_first([
        h for h in hits
        if _has_terms(h, _SEC_GENERIC)
        and _n_concrete(h) == 0
        and not _is_heading_only(h)
    ])
    heading_hits = [h for h in hits if _is_heading_only(h)]
 
    if status == "missing":
        for h in heading_hits[:1]:
            items.append(_make_item(
                h,
                "Security-sectieheading gevonden maar geen inhoud herkend",
                "absent_marker",
            ))
        missing_sigs.append("Geen beveiligingsinhoud aangetroffen")
        missing_sigs.append(
            "Geen concrete beveiligingsmechanismen benoemd (bijv. authenticatie, encryptie, AVG)"
        )
 
    elif status == "partial":
        if generic_hits:
            h = generic_hits[0]
            terms = _matched_terms(h, _SEC_GENERIC)
            items.append(_make_item(
                h,
                f"Generieke beveiligingstaal aangetroffen: {', '.join(terms)}",
                "weak",
            ))
        elif concrete_hits:
            h = concrete_hits[0]
            terms = _matched_terms(h, _SEC_CONCRETE)
            items.append(_make_item(
                h,
                f"Security-bewijs (beperkt): {', '.join(terms)}",
                "weak",
            ))
        missing_sigs.append(
            "Geen concrete beveiligingsmechanismen benoemd (bijv. authenticatie, encryptie, AVG)"
        )
 
    else:  # sufficient / strong
        for h in concrete_hits[:MAX]:
            terms = _matched_terms(h, _SEC_CONCRETE)
            items.append(_make_item(
                h,
                f"Concrete beveiligingsmechanisme(n): {', '.join(terms)}",
                "positive",
            ))
 
    return EvidencePacket(
        criterion_key=spec.key,
        manual_review=cr.manual_review,
        notes=cr.notes,
        evidence_items=items,
        missing_signals=missing_sigs,
    )
 
 
# ---------------------------------------------------------------------------
# Per-criterion dispatch table
# ---------------------------------------------------------------------------
 
_PACKET_BUILDERS = {
    "beperking":    _build_beperking_packet,
    "stakeholders": _build_stakeholders_packet,
    "requirements": _build_requirements_packet,
    "taalkeuze":    _build_taalkeuze_packet,
    "security":     _build_security_packet,
}
 
 
# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
 
 
def build_evidence_packets(
    artifacts: CapsPipelineArtifacts,
) -> dict[str, EvidencePacket]:
    """Build one EvidencePacket per criterion from CapsPipelineArtifacts.
 
    CAPS is the source of truth for all judgements.  This function only
    SELECTS and SHAPES evidence — it never re-judges or overrides any CAPS
    decision.
 
    Args:
        artifacts: The full output of run_caps_with_artifacts, containing
            retrieval candidates (candidates), per-criterion verdicts
            (criterion_results), and the final CapsRunResult (result).
 
    Returns:
        dict[criterion_key → EvidencePacket], one entry per CAPS criterion,
        in CRITERIA_KEYS order.
    """
    packets: dict[str, EvidencePacket] = {}
 
    for key in CRITERIA_KEYS:
        spec = CRITERIA_BY_KEY[key]
        hits = artifacts.candidates.get(key, [])
        cr = artifacts.criterion_results.get(key)
 
        if cr is None:
            packets[key] = EvidencePacket(
                criterion_key=key,
                manual_review=False,
                notes=[],
                evidence_items=[],
                missing_signals=["Criterium niet geëvalueerd door CAPS"],
            )
            continue
 
        builder = _PACKET_BUILDERS[key]
        packets[key] = builder(spec, hits, cr)
 
    return packets 