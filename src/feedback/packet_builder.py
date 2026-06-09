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