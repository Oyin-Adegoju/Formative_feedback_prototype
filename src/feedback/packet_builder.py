"""packet_builder.py — builds per-criterion evidence packets from CAPS artifacts.

Takes a CapsPipelineArtifacts object (retrieval candidates + per-criterion
results + final run result) and produces one EvidencePacket per criterion.

Architecture position:
    CapsPipelineArtifacts → build_evidence_packets → dict[str, EvidencePacket]

CAPS is the source of truth for every verdict (status, count, stoplight).
This module only SELECTS and SHAPES evidence — it never re-judges,
re-scores, or overrides any CAPS decision.  Manual review is no longer a
CAPS concept and is not carried on the packet (deferred to the Qwen stage).

Evidence model: one evidence item == one selected parsed block (heading /
paragraph / bullet / table).  This module does NOT explode blocks into
sentences and does NOT push whole pages/documents downstream.  It selects
MORE distinct blocks per criterion (see _MAX_ITEMS) and attaches richer,
rule-based metadata per item so the one-document-per-run Qwen stage can reason
without seeing the full document.

Selection budget (max evidence items per criterion) — deliberately generous
because Qwen evaluates one document at a time, but still curated (distinct
blocks, deduped, diverse types):
    beperking      5   — limitation + research blocks + supporting context
    stakeholders   6   — stakeholder table(s) preferred; paragraphs to fill
    requirements  10   — FR / NFR / UC / constraint tables, then narrative
    taalkeuze      5   — choice+consequence blocks first, then either signal
    security       6   — concrete-mechanism blocks, then generic context

Each selected item additionally carries rule-based enrichment (focused_excerpt,
matched_signals, criterion_subtype, matched_row_ids, matched_row_count,
evidence_strength, classification_source, local_section_label, context_warning).
Fields are left empty when they cannot be filled honestly.
"""

from __future__ import annotations

import re
from typing import Final

from src.caps.caps import CapsPipelineArtifacts
from src.caps.checks import _classify_section  # pure heading→section classifier
from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS, CriterionSpec
from src.caps.models import CriterionResult
from src.caps.retrieval import RetrievalHit
from src.feedback.evidence import (
    EvidenceItem,
    EvidencePacket,
    EvidenceStrength,
    SignalClass,
)

# ---------------------------------------------------------------------------
# Per-criterion item limits
# ---------------------------------------------------------------------------

_MAX_ITEMS: Final[dict[str, int]] = {
    "beperking":    5,
    "stakeholders": 6,
    "requirements": 10,
    "taalkeuze":    5,
    "security":     6,
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

# Bare MoSCoW priority cell — mirrors checks._MOSCOW_BARE_CELL_RE so the four
# tiers can be surfaced as matched_signals without importing the private name.
_MOSCOW_BARE_CELL_RE: Final[re.Pattern[str]] = re.compile(
    r"^(must|should?|could|won'?t)\b",
    re.IGNORECASE,
)

# Local in-block section labels — used only to detect a section switch inside a
# block whose heading_path is stale (carryover). Deliberately conservative.
_LOCAL_SECTIONS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"\buse[\s\-]?cases?\b", re.IGNORECASE), "Use cases"),
    (re.compile(r"\b(?:niet|non)[\s\-]+functione(?:le|el|l)\b", re.IGNORECASE),
     "Niet-functionele requirements"),
    (re.compile(r"\b(?:niet|non)[\s\-]+functional\b", re.IGNORECASE),
     "Niet-functionele requirements"),
    (re.compile(r"\bconstraints?\b", re.IGNORECASE), "Constraints"),
    (re.compile(r"\bfunctione(?:le|el|l)\b", re.IGNORECASE),
     "Functionele requirements"),
    (re.compile(r"\bfunctional\b", re.IGNORECASE), "Functionele requirements"),
)

# Human-friendly subtype labels for requirement sections.
_SUBTYPE_BY_SECTION: Final[dict[str, str]] = {
    "fr": "functional",
    "nfr": "non_functional",
    "uc": "use_case",
    "other": "",
}


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


def _row_text(row: list) -> str:
    """Join a table row's non-null cells, trimmed, for compact display."""
    return " | ".join(c.strip() for c in row if c)


def _focused_table_excerpt(
    hit: RetrievalHit,
    terms: frozenset[str] | None,
    max_rows: int = 2,
    max_len: int = 220,
) -> str:
    """Cleaner table snippet: header + the first rows that carry a signal.

    Rows are preferred when they contain a matched term, a MoSCoW priority
    label, or a requirement ID. Falls back to the first data rows. Derived from
    the SAME block as the broad excerpt — never from another block.
    """
    tm = hit.block.get("table_meta")
    if not tm or not tm.get("cells"):
        return ""

    cells = tm.get("cells") or []
    header = tm.get("header_row")

    def _is_signal_row(row: list) -> bool:
        joined = _row_text(row).lower()
        if not joined:
            return False
        if terms and any(t in joined for t in terms):
            return True
        if _REQ_ID_RE.search(joined):
            return True
        return any(
            c and _MOSCOW_BARE_CELL_RE.match(c.strip()) for c in row
        )

    signal_rows = [r for r in cells if _is_signal_row(r)]
    chosen = (signal_rows or cells)[:max_rows]

    lines: list[str] = []
    if header:
        lines.append(header.strip())
    for row in chosen:
        rt = _row_text(row)
        if rt:
            lines.append(rt)

    out = "\n".join(lines)
    if len(out) > max_len:
        out = out[:max_len].rstrip() + "…"
    return out


def _focused_paragraph_excerpt(
    text: str,
    terms: frozenset[str] | None,
    max_len: int = 180,
) -> str:
    """Return the first sentence carrying a matched term, trimmed.

    Falls back to the leading slice of the block when no term is present.
    """
    norm = " ".join(text.split())
    if terms:
        for part in re.split(r"(?<=[.!?])\s+", norm):
            low = part.lower()
            if any(t in low for t in terms):
                return part if len(part) <= max_len else part[:max_len].rstrip() + "…"
    return norm if len(norm) <= max_len else norm[:max_len].rstrip() + "…"


def _focused_excerpt(hit: RetrievalHit, terms: frozenset[str] | None) -> str:
    """Build a tighter, cleaner snippet from the selected block."""
    if hit.block["block_type"] == "table":
        return _focused_table_excerpt(hit, terms)
    return _focused_paragraph_excerpt(hit.block["text"], terms)


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


# ---------------------------------------------------------------------------
# Requirement-structure helpers (row IDs / MoSCoW / local section)
# ---------------------------------------------------------------------------


def _searchable_block_text(hit: RetrievalHit) -> str:
    """Block text plus all table cells, for per-row ID / signal scanning."""
    text = hit.block["text"]
    tm = hit.block.get("table_meta")
    if tm and tm.get("cells"):
        cells = [c for row in tm["cells"] for c in row if c]
        if cells:
            text = text + " " + " ".join(cells)
    return text


def _extract_req_ids(hit: RetrievalHit, max_n: int = 12) -> list[str]:
    """Return normalised, de-duplicated requirement IDs found in a block.

    "FR 01" / "FR-01" / "FR01" all collapse to "FR01". Order of first
    appearance is preserved; capped at max_n to keep the item compact.
    """
    text = _searchable_block_text(hit)
    seen: set[str] = set()
    ids: list[str] = []
    for m in _REQ_ID_RE.finditer(text):
        norm = m.group(0).upper().replace(" ", "").replace("-", "")
        if norm not in seen:
            seen.add(norm)
            ids.append(norm)
        if len(ids) >= max_n:
            break
    return ids


def _prio_row_count(hit: RetrievalHit) -> int:
    """Count table rows whose first matching cell starts with a MoSCoW label."""
    tm = hit.block.get("table_meta")
    if not tm or not tm.get("cells"):
        return 0
    count = 0
    for row in tm["cells"]:
        for cell in row:
            if cell and _MOSCOW_BARE_CELL_RE.match(cell.strip()):
                count += 1
                break
    return count


def _moscow_tiers_in_block(hit: RetrievalHit) -> list[str]:
    """Distinct MoSCoW tiers present in a block, as 'moscow:<tier>' signals."""
    tiers: set[str] = set()
    if hit.block["block_type"] == "table":
        for cell in _iter_cells(hit):
            m = _MOSCOW_BARE_CELL_RE.match(cell.strip())
            if m:
                tiers.add(_tier_of(m.group(1)))
    else:
        for m in re.finditer(
            r"\b(must|should|could|won'?t)[\s\-]?have\b",
            hit.block["text"],
            re.IGNORECASE,
        ):
            tiers.add(_tier_of(m.group(1)))
    order = ["must", "should", "could", "wont"]
    return [f"moscow:{t}" for t in order if t in tiers]


def _tier_of(label: str) -> str:
    label = label.lower()
    if label.startswith("must"):
        return "must"
    if label.startswith("won"):
        return "wont"
    if label.startswith("could"):
        return "could"
    return "should"


def _iter_cells(hit: RetrievalHit):
    tm = hit.block.get("table_meta")
    if not tm:
        return
    for row in tm.get("cells") or []:
        for cell in row:
            if cell:
                yield cell


def _data_row_count(hit: RetrievalHit) -> int:
    tm = hit.block.get("table_meta")
    if not tm:
        return 0
    return len(tm.get("cells") or [])


def _local_section_label(text: str) -> str:
    """Detect an in-block section label (e.g. 'Use cases'). '' when none."""
    head = text[:200]
    for pattern, label in _LOCAL_SECTIONS:
        if pattern.search(head):
            return label
    return ""


def _requirement_subtype(hit: RetrievalHit) -> tuple[str, str, str]:
    """Resolve (subtype, classification_source, local_section_label) for a req block.

    subtype uses checks._classify_section on the block's OWN heading_path first
    (the same authority the notes use). When the heading is combined/unlabelled
    ('other'), a per-row FR/NFR ID prefix is consulted. local_section_label is
    only reported when the heading classified as 'other' yet the block body
    clearly names a section — a stale-heading carryover signal.
    """
    sec = _classify_section(hit.block["heading_path"])
    if sec != "other":
        return _SUBTYPE_BY_SECTION[sec], "heading_path", ""

    # Heading is combined/unlabelled — try a reliable per-row ID prefix.
    ids = _extract_req_ids(hit, max_n=24)
    has_fr = any(re.match(r"FR\d", i) for i in ids)
    has_nfr = any(re.match(r"NFR\d", i) for i in ids)
    has_con = any(re.match(r"(CONSTR|CON)\d", i) for i in ids)
    local = _local_section_label(_searchable_block_text(hit))

    if has_nfr and not has_fr:
        return "non_functional", "row_id_prefix", local
    if has_fr and not has_nfr:
        return "functional", "row_id_prefix", local
    if has_con and not has_fr and not has_nfr:
        return "constraint", "row_id_prefix", local
    if local:
        # Heading stale but the body names a section — report it, don't guess subtype.
        return "", "block_text_section_switch", local
    return "", "", ""


# ---------------------------------------------------------------------------
# Evidence-item construction with enrichment
# ---------------------------------------------------------------------------


def _evidence_strength(
    signal_class: SignalClass,
    matched_signals: list[str],
    matched_row_count: int | None,
) -> EvidenceStrength:
    """Derive a compact strength label deterministically (not a CAPS verdict)."""
    if signal_class == "absent_marker":
        return "absent"
    if signal_class == "weak":
        return "thin"
    dense = (matched_row_count or 0) >= 3 or len(matched_signals) >= 2
    return "strong" if dense else "moderate"


def _make_item(
    hit: RetrievalHit,
    reason: str,
    signal_class: SignalClass,
    *,
    focused_terms: frozenset[str] | None = None,
    matched_signals: list[str] | None = None,
    criterion_subtype: str = "",
    classification_source: str = "",
    matched_row_count: int | None = None,
    matched_row_ids: list[str] | None = None,
    local_section_label: str = "",
    context_warning: str = "",
) -> EvidenceItem:
    """Construct an enriched EvidenceItem from a RetrievalHit.

    The broad `excerpt` is preserved unchanged; `focused_excerpt` is the cleaner
    derived snippet. All enrichment fields default to empty/None and are only
    populated by callers with real, rule-based observations.
    """
    b = hit.block
    sigs = matched_signals or []
    row_ids = matched_row_ids or []
    focused = _focused_excerpt(hit, focused_terms)
    excerpt = _excerpt_for(hit)
    # Avoid duplicating identical text in both fields.
    if focused and focused.strip() == excerpt.strip():
        focused = ""
    return EvidenceItem(
        block_id=b["block_id"],
        page_no=b["page_no"],
        block_type=b["block_type"],
        heading_path=list(b["heading_path"]),
        excerpt=excerpt,
        selection_reason=reason,
        signal_class=signal_class,
        focused_excerpt=focused,
        matched_signals=sigs,
        criterion_subtype=criterion_subtype,
        classification_source=classification_source,
        evidence_strength=_evidence_strength(
            signal_class, sigs, matched_row_count
        ),
        matched_row_count=matched_row_count,
        matched_row_ids=row_ids,
        local_section_label=local_section_label,
        context_warning=context_warning,
    )


# ---------------------------------------------------------------------------
# Criterion 1: Beperking & deskresearch
# ---------------------------------------------------------------------------


def _build_beperking_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
) -> EvidencePacket:
    """Select up to _MAX_ITEMS['beperking'] distinct limitation/research blocks.

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

    seen: set[str] = set()

    def _subtype(h: RetrievalHit) -> str:
        has_l = _has_terms(h, _BEP_LIMITATION)
        has_r = _has_terms(h, _BEP_RESEARCH)
        if has_l and has_r:
            return "limitation_and_research"
        if has_l:
            return "limitation"
        if has_r:
            return "research"
        return ""

    def _add(h: RetrievalHit, reason: str, sc: SignalClass) -> bool:
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return False
        seen.add(bid)
        terms = _matched_terms(h, _BEP_LIMITATION, 3) + _matched_terms(h, _BEP_RESEARCH, 3)
        items.append(_make_item(
            h, reason, sc,
            focused_terms=_BEP_LIMITATION | _BEP_RESEARCH,
            matched_signals=terms[:4],
            criterion_subtype=_subtype(h),
            classification_source="block_text" if terms else "",
        ))
        return True

    if status == "missing":
        content = _sort_content_first([h for h in processed if not _is_heading_only(h)])
        for h in content[:1]:
            _add(h, "Geen voldoende bewijs gevonden", "weak")
        for h in heading_hits[:1]:
            items.append(_make_item(
                h, "Sectieheading gevonden maar geen inhoud herkend", "absent_marker",
            ))
        missing_sigs.append("Geen expliciete beperking of doelgroepkeuze beschreven")
        missing_sigs.append("Geen deskresearch of bronvermelding aangetroffen")

    elif status == "partial":
        if lim_hits and not res_hits:
            for h in lim_hits[:MAX]:
                terms = _matched_terms(h, _BEP_LIMITATION)
                _add(h, f"Beperking/doelgroep: {', '.join(terms)}", "positive")
            missing_sigs.append("Geen deskresearch of bronvermelding aangetroffen")
        elif res_hits and not lim_hits:
            for h in res_hits[:MAX]:
                terms = _matched_terms(h, _BEP_RESEARCH)
                _add(h, f"Onderbouwing: {', '.join(terms)}", "positive")
            missing_sigs.append("Geen expliciete beperking of doelgroepkeuze beschreven")
        else:
            for h in _sort_content_first(processed):
                if len(items) >= MAX:
                    break
                _add(h, "Partieel bewijs aangetroffen", "weak")

    else:  # sufficient / strong
        for h in lim_hits[:2]:
            terms = _matched_terms(h, _BEP_LIMITATION)
            _add(h, f"Beperking/doelgroep: {', '.join(terms)}", "positive")
        for h in res_hits[:2]:
            terms = _matched_terms(h, _BEP_RESEARCH)
            _add(h, f"Onderbouwing/deskresearch: {', '.join(terms)}", "positive")
        for h in _sort_content_first(processed):
            if len(items) >= MAX:
                break
            l_t = _matched_terms(h, _BEP_LIMITATION)
            r_t = _matched_terms(h, _BEP_RESEARCH)
            all_t = l_t + r_t
            reason = (
                f"Aanvullend bewijs: {', '.join(all_t[:3])}" if all_t
                else "Aanvullende context"
            )
            _add(h, reason, "positive" if all_t else "weak")

    return EvidencePacket(
        criterion_key=spec.key,
        notes=cr.notes,
        evidence_items=items,
        missing_signals=missing_sigs,
    )


# ---------------------------------------------------------------------------
# Criterion 2: Stakeholders
# ---------------------------------------------------------------------------


def _stakeholder_row_count(hit: RetrievalHit) -> int:
    return _data_row_count(hit)


def _build_stakeholders_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
) -> EvidencePacket:
    """Select up to _MAX_ITEMS['stakeholders'] distinct blocks, tables first.

    Table blocks with belang/invloed columns are the primary source.
    Paragraph/bullet blocks with role keywords fill remaining slots.
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

    _BI_TERMS = frozenset({"belang", "invloed", "concern", "interesse", "macht", "prioriter"})

    def _bi_signals(h: RetrievalHit) -> list[str]:
        t = h.block["text"].lower()
        sigs = []
        if any(x in t for x in ("belang", "concern", "interesse")):
            sigs.append("belang")
        if any(x in t for x in ("invloed", "macht", "prioriter")):
            sigs.append("invloed")
        return sigs

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
            n_rows = _stakeholder_row_count(h)
            items.append(_make_item(
                h,
                f"Stakeholder-tabel ({n_rows} rijen) — onvolledig ({count} van {minimum} min.)",
                "weak",
                focused_terms=_BI_TERMS,
                matched_signals=_bi_signals(h),
                criterion_subtype="table",
                classification_source="block_type",
                matched_row_count=n_rows,
            ))
        elif para_hits:
            h = para_hits[0]
            items.append(_make_item(
                h,
                f"Stakeholder-vermelding in tekst ({count} herkend)",
                "weak",
                focused_terms=_BI_TERMS,
                matched_signals=_bi_signals(h),
                criterion_subtype="text",
                classification_source="block_type",
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
        seen: set[str] = set()
        for h in (table_hits + para_hits):
            if len(items) >= MAX:
                break
            bid = h.block["block_id"]
            if bid in seen:
                continue
            seen.add(bid)
            if h.block["block_type"] == "table":
                n_rows = _stakeholder_row_count(h)
                items.append(_make_item(
                    h, f"Stakeholder-tabel ({n_rows} rijen)", "positive",
                    focused_terms=_BI_TERMS,
                    matched_signals=_bi_signals(h),
                    criterion_subtype="table",
                    classification_source="block_type",
                    matched_row_count=n_rows,
                ))
            else:
                items.append(_make_item(
                    h, "Stakeholder-beschrijving in lopende tekst", "positive",
                    focused_terms=_BI_TERMS,
                    matched_signals=_bi_signals(h),
                    criterion_subtype="text",
                    classification_source="block_type",
                ))

        if not has_belang:
            missing_sigs.append("Belang per stakeholder niet expliciet beschreven")
        if not has_invloed:
            missing_sigs.append("Invloed per stakeholder niet expliciet beschreven")

    return EvidencePacket(
        criterion_key=spec.key,
        notes=cr.notes,
        evidence_items=items,
        missing_signals=missing_sigs,
    )


# ---------------------------------------------------------------------------
# Criterion 3: Requirements
# ---------------------------------------------------------------------------


def _req_id_count(hit: RetrievalHit) -> int:
    """Count requirement IDs (FR-01, NFR-01, …) in a block (incl. table cells)."""
    return len(_REQ_ID_RE.findall(_searchable_block_text(hit)))


def _req_matched_row_count(hit: RetrievalHit) -> int | None:
    """Best rule-based 'requirement rows' estimate for a table block.

    Prefer MoSCoW priority rows; fall back to distinct requirement IDs; then to
    the raw data-row count. None for non-table blocks.
    """
    if hit.block["block_type"] != "table":
        return None
    prio = _prio_row_count(hit)
    if prio:
        return prio
    ids = len(_extract_req_ids(hit, max_n=99))
    if ids:
        return ids
    return _data_row_count(hit) or None


def _req_signals(hit: RetrievalHit) -> list[str]:
    """Structured requirement signals: MoSCoW tiers + req-id marker."""
    sigs = _moscow_tiers_in_block(hit)
    if _extract_req_ids(hit, max_n=1):
        sigs.append("req_id")
    return sigs


def _add_requirement_item(
    items: list[EvidenceItem],
    seen: set[str],
    hit: RetrievalHit,
    reason: str,
    signal_class: SignalClass,
    max_items: int,
) -> bool:
    """Append one enriched requirements item if not a duplicate / over budget."""
    bid = hit.block["block_id"]
    if bid in seen or len(items) >= max_items:
        return False
    seen.add(bid)
    subtype, source, local = _requirement_subtype(hit)
    items.append(_make_item(
        hit, reason, signal_class,
        focused_terms=None,
        matched_signals=_req_signals(hit),
        criterion_subtype=subtype,
        classification_source=source,
        matched_row_count=_req_matched_row_count(hit),
        matched_row_ids=_extract_req_ids(hit),
        local_section_label=local,
        context_warning=(
            "heading_path mogelijk verouderd; lokale sectie gedetecteerd"
            if (local and source == "block_text_section_switch") else ""
        ),
    ))
    return True


def _build_requirements_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
) -> EvidencePacket:
    """Select up to _MAX_ITEMS['requirements'] distinct requirement blocks.

    Tables with the most MoSCoW prio-rows rank first; ties by retrieval score.
    Paragraph / bullet blocks fill remaining slots. Each item exposes subtype
    (functional / non_functional / use_case / constraint), matched row IDs,
    matched row count, and MoSCoW tier signals where the block supports them.
    """
    MAX = _MAX_ITEMS["requirements"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()

    table_hits = sorted(
        [h for h in hits if h.block["block_type"] == "table"],
        key=lambda h: (-_prio_row_count(h), -_req_id_count(h), -h.score, h.block["block_id"]),
    )
    para_hits = _sort_content_first(
        [h for h in hits if h.block["block_type"] in ("paragraph", "bullet")]
    )
    heading_hits = [h for h in hits if _is_heading_only(h)]

    all_text = " ".join(h.block["text"].lower() for h in hits)
    has_prio = any(
        t in all_text for t in ("must", "should", "could", "moscow", "prioriteit")
    )
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
        return EvidencePacket(
            criterion_key=spec.key, notes=cr.notes,
            evidence_items=items, missing_signals=missing_sigs,
        )

    # partial / sufficient / strong all surface the real requirement blocks.
    for h in table_hits:
        if len(items) >= MAX:
            break
        pcount = _prio_row_count(h)
        rcount = _req_id_count(h)
        n_rows = _data_row_count(h)
        if pcount > 0:
            reason = f"Requirements-tabel: {n_rows} rijen, {pcount} MoSCoW-labels"
            sc: SignalClass = "positive"
        elif rcount > 0:
            reason = f"Requirements-tabel: {n_rows} rijen, {rcount} req-IDs"
            sc = "positive"
        else:
            reason = "Requirements-tabel (beperkte inhoud herkend)"
            sc = "weak" if status == "partial" else "positive"
        _add_requirement_item(items, seen, h, reason, sc, MAX)

    for h in para_hits:
        if len(items) >= MAX:
            break
        rcount = _req_id_count(h)
        if rcount > 0:
            reason = f"Requirements in tekst ({rcount} IDs herkend)"
            sc = "positive"
        else:
            reason = "Requirements in tekst"
            sc = "weak"
        _add_requirement_item(items, seen, h, reason, sc, MAX)

    if status == "partial":
        missing_sigs.append(
            f"Slechts ~{cr.count or 0} requirements gevonden (minimum {minimum})"
        )
    if not has_prio:
        missing_sigs.append("Geen MoSCoW-prioritering aangetroffen")

    return EvidencePacket(
        criterion_key=spec.key,
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
    """Select up to _MAX_ITEMS['taalkeuze'] distinct blocks.

    Blocks carrying BOTH choice AND consequence signals are the most
    informative and come first. Merges short consecutive fragments first.
    """
    MAX = _MAX_ITEMS["taalkeuze"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    _ALL_TAAL = _TAAL_CHOICE | _TAAL_CONSEQUENCE

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

    def _subtype(h: RetrievalHit) -> str:
        c = _has_terms(h, _TAAL_CHOICE)
        k = _has_terms(h, _TAAL_CONSEQUENCE)
        if c and k:
            return "choice_and_consequence"
        if c:
            return "choice"
        if k:
            return "consequence"
        return ""

    seen: set[str] = set()

    def _add(h: RetrievalHit, reason: str, sc: SignalClass) -> bool:
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return False
        seen.add(bid)
        sigs = _matched_terms(h, _TAAL_CHOICE, 2) + _matched_terms(h, _TAAL_CONSEQUENCE, 2)
        items.append(_make_item(
            h, reason, sc,
            focused_terms=_ALL_TAAL,
            matched_signals=sigs[:4],
            criterion_subtype=_subtype(h),
            classification_source="block_text" if sigs else "",
        ))
        return True

    if status == "missing":
        for h in heading_hits[:1]:
            items.append(_make_item(
                h, "Taalkeuze-sectieheading zonder inhoud aangetroffen", "absent_marker",
            ))
        missing_sigs.append("Geen expliciete taalkeuze vermeld")
        missing_sigs.append("Geen gevolgen van de taalkeuze beschreven")

    elif status == "partial":
        if has_choice and not has_consequence:
            for h in choice_hits[:MAX]:
                terms = _matched_terms(h, _TAAL_CHOICE)
                _add(h, f"Taalkeuze: {', '.join(terms)}", "positive")
            missing_sigs.append(
                "Gevolgen van taalkeuze niet beschreven (bijv. bereik, vertaalkosten, begrijpelijkheid)"
            )
        elif has_consequence and not has_choice:
            for h in cons_hits[:MAX]:
                terms = _matched_terms(h, _TAAL_CONSEQUENCE)
                _add(h, f"Gevolgen beschreven: {', '.join(terms)}", "positive")
            missing_sigs.append(
                "Geen expliciete taalkeuze vermeld (bijv. 'de webshop is in het Nederlands')"
            )
        else:
            for h in _sort_content_first(processed):
                if len(items) >= MAX:
                    break
                if not _is_heading_only(h):
                    _add(h, "Partieel taalkeuze-bewijs", "weak")
            notes_lower = " ".join(cr.notes).lower()
            if "geen gevolgen" in notes_lower or "geen consequen" in notes_lower:
                missing_sigs.append("Gevolgen van taalkeuze niet beschreven")
            elif "geen expliciete" in notes_lower or "geen taalkeuze" in notes_lower:
                missing_sigs.append("Geen expliciete taalkeuze vermeld")
            else:
                missing_sigs.append("Taalkeuze of gevolgen niet volledig beschreven")

    else:  # sufficient / strong
        both = _sort_content_first([
            h for h in processed
            if _has_terms(h, _TAAL_CHOICE)
            and _has_terms(h, _TAAL_CONSEQUENCE)
            and not _is_heading_only(h)
        ])
        for h in both:
            if len(items) >= MAX:
                break
            c_terms = _matched_terms(h, _TAAL_CHOICE)
            k_terms = _matched_terms(h, _TAAL_CONSEQUENCE)
            _add(h, f"Taalkeuze ({', '.join(c_terms)}) én gevolgen ({', '.join(k_terms)})", "positive")
        for h in (choice_hits + cons_hits):
            if len(items) >= MAX:
                break
            c_terms = _matched_terms(h, _TAAL_CHOICE)
            k_terms = _matched_terms(h, _TAAL_CONSEQUENCE)
            reason = (
                f"Taalkeuze: {', '.join(c_terms)}" if c_terms
                else f"Gevolgen: {', '.join(k_terms)}"
            )
            _add(h, reason, "positive")

    return EvidencePacket(
        criterion_key=spec.key,
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
    """Select up to _MAX_ITEMS['security'] distinct blocks.

    Concrete-mechanism blocks (authenticatie, encryptie, AVG, …) come first and
    are 'positive'; generic-only security language is surfaced as 'weak' context.
    """
    MAX = _MAX_ITEMS["security"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    _ALL_SEC = _SEC_CONCRETE | _SEC_GENERIC

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

    seen: set[str] = set()

    def _add(h: RetrievalHit, reason: str, sc: SignalClass, subtype: str) -> bool:
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return False
        seen.add(bid)
        sigs = _matched_terms(h, _SEC_CONCRETE, 4) or _matched_terms(h, _SEC_GENERIC, 3)
        items.append(_make_item(
            h, reason, sc,
            focused_terms=_ALL_SEC,
            matched_signals=sigs,
            criterion_subtype=subtype,
            classification_source="block_text" if sigs else "",
        ))
        return True

    if status == "missing":
        for h in heading_hits[:1]:
            items.append(_make_item(
                h, "Security-sectieheading gevonden maar geen inhoud herkend", "absent_marker",
            ))
        missing_sigs.append("Geen beveiligingsinhoud aangetroffen")
        missing_sigs.append(
            "Geen concrete beveiligingsmechanismen benoemd (bijv. authenticatie, encryptie, AVG)"
        )

    elif status == "partial":
        if generic_hits:
            h = generic_hits[0]
            terms = _matched_terms(h, _SEC_GENERIC)
            _add(h, f"Generieke beveiligingstaal aangetroffen: {', '.join(terms)}", "weak", "generic")
        elif concrete_hits:
            h = concrete_hits[0]
            terms = _matched_terms(h, _SEC_CONCRETE)
            _add(h, f"Security-bewijs (beperkt): {', '.join(terms)}", "weak", "concrete")
        missing_sigs.append(
            "Geen concrete beveiligingsmechanismen benoemd (bijv. authenticatie, encryptie, AVG)"
        )

    else:  # sufficient / strong
        for h in concrete_hits:
            if len(items) >= MAX:
                break
            terms = _matched_terms(h, _SEC_CONCRETE)
            _add(h, f"Concrete beveiligingsmechanisme(n): {', '.join(terms)}", "positive", "concrete")
        # Fill remaining slots with generic context (clearly weaker).
        for h in generic_hits:
            if len(items) >= MAX:
                break
            terms = _matched_terms(h, _SEC_GENERIC)
            _add(h, f"Aanvullende beveiligingscontext: {', '.join(terms)}", "weak", "generic")

    return EvidencePacket(
        criterion_key=spec.key,
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
                notes=[],
                evidence_items=[],
                missing_signals=["Criterium niet geëvalueerd door CAPS"],
            )
            continue

        builder = _PACKET_BUILDERS[key]
        packets[key] = builder(spec, hits, cr)

    return packets
