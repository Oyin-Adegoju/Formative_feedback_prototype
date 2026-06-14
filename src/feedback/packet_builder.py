"""packet_builder.py — builds per-criterion evidence packets from CAPS artifacts.

Takes a CapsPipelineArtifacts object (retrieval candidates + per-criterion
results + final run result + full ordered blocks) and produces one
EvidencePacket per criterion for the CAPS→Qwen quality handoff.

Architecture position:
    CapsPipelineArtifacts → build_evidence_packets → dict[str, EvidencePacket]

CAPS is the source of truth for every verdict (status, count, stoplight).
This module only SELECTS and SHAPES evidence — it never re-judges, re-scores,
or overrides any CAPS decision.

Selection strategy: SECTION-FIRST, SIGNAL-ASSISTED
--------------------------------------------------
Evidence admission is driven primarily by *section identity* (section_family.py),
not by keyword hits:

  1. In-family blocks  — heading_path belongs to the criterion's section family.
                         These are ranked first and form the backbone of the
                         packet.
  2. Neutral blocks    — heading_path belongs to no family (Inleiding, page
                         headers, …). Admitted as supporting context ONLY when
                         they carry a real, criterion-defining content signal.
  3. Foreign rescue    — heading_path clearly belongs to ANOTHER criterion's
                         family. Admitted only for a strong, explainable reason
                         (a criterion-defining token), only to preserve coverage,
                         bounded in number, with a focused excerpt + an honest
                         context_warning. This is how legitimately cross-placed
                         content (security/taalkeuze inside non-functional
                         requirements) still reaches the right criterion without
                         letting generic words pull whole foreign sections in.

Signals (matched keywords, MoSCoW tiers, row-ids, subtypes) ASSIST: they label
subtype, rank within a tier, gate rescue, and slice table rows. They never
override a clear section mismatch, and they are matched with word boundaries so
short tokens (avg, ssl, tls) cannot fire inside unrelated words.

Excerpts are broader but bounded: paragraph/bullet evidence is widened into a
coherent same-section window (using the full ordered blocks), tables show the
header plus a readable row slice, and structural blocks are paired with nearby
explanatory prose when that prose clarifies their meaning.

Selection budget (max evidence items per criterion):
    beperking      6
    stakeholders   6
    requirements  10
    taalkeuze      5
    security       6
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from src.caps.caps import CapsPipelineArtifacts
from src.caps.checks import _classify_section  # pure heading→section classifier
from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS, CriterionSpec
from src.caps.models import BlockDict, CriterionResult
from src.caps.retrieval import RetrievalHit
from src.feedback.evidence import (
    EvidenceItem,
    EvidencePacket,
    EvidenceStrength,
    SignalClass,
)
from src.feedback.section_family import (
    contains_negation,
    families_of,
    is_foreign,
    is_in_family,
)

# ---------------------------------------------------------------------------
# Per-criterion item limits
# ---------------------------------------------------------------------------

_MAX_ITEMS: Final[dict[str, int]] = {
    "beperking":    6,
    "stakeholders": 6,
    "requirements": 10,
    "taalkeuze":    5,
    "security":     6,
}

# Max foreign-rescue items admitted per criterion (coverage safety valve, kept
# small so rescues never dominate genuine in-section evidence).
_MAX_RESCUE: Final[dict[str, int]] = {
    "beperking":    2,
    "stakeholders": 4,   # image/quadrant fallback may need a few substitutes
    "requirements": 3,
    "taalkeuze":    3,
    "security":     3,
}

# ---------------------------------------------------------------------------
# Lightweight signal term sets (selection / labelling only — CAPS is authoritative)
# ---------------------------------------------------------------------------

_BEP_LIMITATION: Final[frozenset[str]] = frozenset({
    "beperking", "slechtziend", "blind", "doof", "dyslexie",
    "visuele beperking", "motorische", "auditieve", "cognitieve",
    "afbakening", "doelgroep met beperking", "specifieke doelgroep",
    "niche", "focus op", "gekozen doelgroep",
})

# Disability/accessibility-specific limitation tokens. These are the ONLY tokens
# allowed to rescue a foreign block into beperking — the generic word "beperking"
# is excluded because it fires inside "budgetbeperking" / "betaalbeperking", which
# are constraints, not a chosen target-group limitation.
_BEP_DISABILITY: Final[frozenset[str]] = frozenset({
    "slechtziend", "slechtziende", "blind", "blinde", "kleurenblind",
    "kleurenblindheid", "visuele beperking", "visueel beperkt", "dyslexie",
    "dyslect", "doof", "slechthorend", "auditieve beperking",
    "motorische beperking", "cognitieve beperking", "schermlezer",
    "screenreader", "wcag", "toetsenbordnavigatie",
})

_BEP_RESEARCH: Final[frozenset[str]] = frozenset({
    "deskresearch", "desk research", "literatuuronderzoek", "literatuur",
    "literatuurlijst", "bronvermelding", "bronnenlijst", "bronnen", "bron",
    "onderzoek", "verkenning", "marktonderzoek", "user research",
    "wetenschappelijk",
})

_TAAL_CHOICE: Final[frozenset[str]] = frozenset({
    "taalkeuze", "taalversie", "meertalig", "meertaligheid",
    "in het nederlands", "in het engels", "nederlandstalig",
    "webshop in het", "taal van de", "taalkeuzeknop",
    # Bare language names — aligned with checks.py _LANGUAGE_TERMS
    "nederlands", "engels", "duits", "frans",
})

_TAAL_CONSEQUENCE: Final[frozenset[str]] = frozenset({
    "bereik", "vertaalkosten", "lokalisatie", "consequentie",
    "begrijpelijk", "taalbarrière", "taalbarrier", "vertaling",
    "internationaal", "beïnvloeden", "beïnvloed", "doelgroepbereik",
})

_SEC_CONCRETE: Final[frozenset[str]] = frozenset({
    "authenticatie", "autorisatie", "encryptie", "avg", "gdpr",
    "owasp", "https", "ssl", "tls", "jwt", "2fa", "two-factor",
    "xss", "csrf", "firewall", "bcrypt", "hashing", "wachtwoord",
    "toegangscontrole", "risicoanalyse", "privacyverklaring",
    "beveiligingsaudit",
})

# Concrete tokens trusted enough to RESCUE a foreign requirements block into
# security. Excludes bare https/ssl/http — these fire inside citation URLs and
# random words and are not, on their own, evidence of a security *mechanism*.
_SEC_RESCUE: Final[frozenset[str]] = frozenset(_SEC_CONCRETE - {"https", "ssl"})

_SEC_GENERIC: Final[frozenset[str]] = frozenset({
    "security", "beveiliging", "veiligheid", "veilig", "privacy",
})

_STK_BELANG: Final[frozenset[str]] = frozenset({"belang", "concern", "interesse"})
_STK_INVLOED: Final[frozenset[str]] = frozenset({"invloed", "macht", "prioriter"})
_STK_ROLE: Final[frozenset[str]] = frozenset({
    "stakeholder", "belanghebbende", "opdrachtgever", "eindgebruiker",
    "actor", "investeerder", "ontwikkelaar", "beheerder",
})

# Requirement ID pattern — reused from checks.py logic, kept local.
_REQ_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(FR|NFR|USC|UC|US|REQ|CONSTR|CON|F|NF)[-\s]?\d{1,3}\b",
    re.IGNORECASE,
)

# Bare MoSCoW priority cell — mirrors checks._MOSCOW_BARE_CELL_RE.
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
# Word-boundary token matching (signal honesty)
# ---------------------------------------------------------------------------


def _is_acronymish(term: str) -> bool:
    """Short / digit-bearing tokens (avg, ssl, tls, 2fa, gdpr) — match strictly."""
    return len(term) <= 4 or any(ch.isdigit() for ch in term)


@lru_cache(maxsize=2048)
def _token_re(term: str) -> re.Pattern[str]:
    """Compile a left-anchored token matcher for one term (cached).

    All matches require a word boundary BEFORE the term, which stops short
    tokens from firing inside unrelated words ("fitnesslie**ssl**iefhebbers",
    "AVGregels"). Acronym-like tokens (≤4 chars or with a digit) also require a
    trailing boundary, so "AVG" matches but "AVGregels" does not. Longer Dutch
    word tokens allow a trailing suffix so normal inflection still matches
    ("meertalig" → "meertaligen", "wachtwoord" → "wachtwoorden",
    "slechtziend" → "slechtziende(n)").
    """
    esc = re.escape(term)
    if _is_acronymish(term):
        return re.compile(r"\b" + esc + r"\b", re.IGNORECASE)
    return re.compile(r"\b" + esc + r"\w*", re.IGNORECASE)


def _has_token(text: str, term: str) -> bool:
    return _token_re(term).search(text) is not None


def _matched_tokens(text: str, terms: frozenset[str], max_n: int = 4) -> list[str]:
    """Word-boundary-matched terms from a set, in set order, capped at max_n."""
    out = [t for t in terms if _has_token(text, t)]
    return out[:max_n]


def _has_any_token(text: str, terms: frozenset[str]) -> bool:
    return any(_has_token(text, t) for t in terms)


# ---------------------------------------------------------------------------
# Document context — same-section neighbour windows + explanation pairing
# ---------------------------------------------------------------------------


@dataclass
class _DocCtx:
    """Indexed view of the full ordered block list for context construction."""

    blocks: list[BlockDict]
    pos: dict[str, int]

    @classmethod
    def build(cls, blocks: list[BlockDict]) -> "_DocCtx":
        return cls(blocks=blocks, pos={b["block_id"]: i for i, b in enumerate(blocks)})

    def window(self, anchor_bid: str, max_chars: int = 440) -> str:
        """Coherent same-section paragraph/bullet window around an anchor block.

        Extends from the anchor forward then backward across immediately adjacent
        blocks that share the anchor's heading_path and are paragraph/bullet,
        stopping at max_chars. Returns text in document order. Falls back to the
        anchor's own (whitespace-normalised, trimmed) text when no neighbours fit.
        """
        i = self.pos.get(anchor_bid)
        if i is None:
            return ""
        anchor = self.blocks[i]
        hp = anchor["heading_path"]
        chosen = {i}
        running = len(" ".join(anchor["text"].split()))

        def _extend(step: int) -> None:
            nonlocal running
            j = i + step
            while 0 <= j < len(self.blocks):
                b = self.blocks[j]
                if b["block_type"] not in ("paragraph", "bullet"):
                    break
                if b["heading_path"] != hp:
                    break
                t_len = len(" ".join(b["text"].split()))
                if running + t_len + 1 > max_chars:
                    break
                chosen.add(j)
                running += t_len + 1
                j += step

        _extend(1)
        _extend(-1)
        text = " ".join(
            " ".join(self.blocks[k]["text"].split()) for k in sorted(chosen)
        )
        text = " ".join(text.split())
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        return text

    def explanation(self, anchor_bid: str, max_chars: int = 200) -> str:
        """Nearest same-section explanatory prose for a structural (table/heading)
        block — the paragraph just before or just after it, under the same
        heading_path. Empty when none is adjacent.
        """
        i = self.pos.get(anchor_bid)
        if i is None:
            return ""
        hp = self.blocks[i]["heading_path"]
        for j in (i - 1, i + 1, i - 2, i + 2):
            if 0 <= j < len(self.blocks):
                b = self.blocks[j]
                if b["block_type"] in ("paragraph", "bullet") and b["heading_path"] == hp:
                    t = " ".join(b["text"].split())
                    if len(t) >= 30:
                        return t if len(t) <= max_chars else t[:max_chars].rstrip() + "…"
        return ""


# ---------------------------------------------------------------------------
# Excerpt helpers
# ---------------------------------------------------------------------------


def _excerpt_paragraph(text: str, max_len: int = 360) -> str:
    """Normalise whitespace and trim to max_len characters."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _excerpt_table(block: dict, max_data_rows: int = 6) -> str:
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


def _row_text(row: list) -> str:
    """Join a table row's non-null cells, trimmed, for compact display."""
    return " | ".join(c.strip() for c in row if c)


def _focused_table_excerpt(
    block: dict,
    terms: frozenset[str] | None,
    max_rows: int = 3,
    max_len: int = 260,
    boundary: bool = False,
) -> str:
    """Cleaner table snippet: header + the first rows that carry a signal.

    Rows are preferred when they contain a matched term, a MoSCoW priority
    label, or a requirement ID. With boundary=True, term matching uses word
    boundaries (used for the short security/language tokens).
    """
    tm = block.get("table_meta")
    if not tm or not tm.get("cells"):
        return ""

    cells = tm.get("cells") or []
    header = tm.get("header_row")

    def _row_has_term(joined: str) -> bool:
        if not terms:
            return False
        if boundary:
            return any(_has_token(joined, t) for t in terms)
        return any(t in joined for t in terms)

    def _is_signal_row(row: list) -> bool:
        joined = _row_text(row).lower()
        if not joined:
            return False
        if _row_has_term(joined):
            return True
        if _REQ_ID_RE.search(joined):
            return True
        return any(c and _MOSCOW_BARE_CELL_RE.match(c.strip()) for c in row)

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
    max_len: int = 200,
    boundary: bool = False,
) -> str:
    """Return the first sentence carrying a matched term, trimmed."""
    norm = " ".join(text.split())
    if terms:
        for part in re.split(r"(?<=[.!?])\s+", norm):
            low = part.lower()
            hit = (
                any(_has_token(low, t) for t in terms) if boundary
                else any(t in low for t in terms)
            )
            if hit:
                return part if len(part) <= max_len else part[:max_len].rstrip() + "…"
    return norm if len(norm) <= max_len else norm[:max_len].rstrip() + "…"


# ---------------------------------------------------------------------------
# Hit classification helpers
# ---------------------------------------------------------------------------


def _is_heading_only(hit: RetrievalHit) -> bool:
    """True when the block is a heading with no text-hint matches."""
    return hit.block["block_type"] == "heading" and not hit.matched_text_hints


def _has_terms(hit: RetrievalHit, terms: frozenset[str]) -> bool:
    """Substring presence of any term in the block's text (broad labelling use)."""
    text = hit.block["text"].lower()
    return any(t in text for t in terms)


def _matched_terms(hit: RetrievalHit, terms: frozenset[str], max_n: int = 3) -> list[str]:
    """Up to max_n terms (substring) present in the block's text."""
    text = hit.block["text"].lower()
    return [t for t in terms if t in text][:max_n]


def _sort_content_first(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    """Content blocks before heading-only; within each, higher score, then id."""
    return sorted(
        hits,
        key=lambda h: (int(_is_heading_only(h)), -h.score, h.block["block_id"]),
    )


def _searchable_block_text(hit: RetrievalHit) -> str:
    """Block text plus all table cells, for per-row ID / signal scanning."""
    text = hit.block["text"]
    tm = hit.block.get("table_meta")
    if tm and tm.get("cells"):
        cells = [c for row in tm["cells"] for c in row if c]
        if cells:
            text = text + " " + " ".join(cells)
    return text


# ---------------------------------------------------------------------------
# Section-family partitioning
# ---------------------------------------------------------------------------


@dataclass
class _Bucketed:
    """Hits partitioned by section identity for one criterion."""

    in_family: list[RetrievalHit]
    neutral: list[RetrievalHit]
    foreign: list[RetrievalHit]
    fams: dict[str, frozenset[str]]  # block_id → families_of(heading_path)


def _bucket(criterion_key: str, hits: list[RetrievalHit]) -> _Bucketed:
    """Partition hits into in-family / neutral / foreign for a criterion.

    Section identity is the primary admission axis; the criterion builders then
    apply signal-based ranking and rescue within these buckets.
    """
    in_f: list[RetrievalHit] = []
    neu: list[RetrievalHit] = []
    fgn: list[RetrievalHit] = []
    fams: dict[str, frozenset[str]] = {}
    for h in hits:
        f = families_of(h.block["heading_path"])
        fams[h.block["block_id"]] = f
        if is_in_family(criterion_key, f):
            in_f.append(h)
        elif is_foreign(criterion_key, f):
            fgn.append(h)
        else:
            neu.append(h)
    return _Bucketed(
        in_family=_sort_content_first(in_f),
        neutral=_sort_content_first(neu),
        foreign=_sort_content_first(fgn),
        fams=fams,
    )


# ---------------------------------------------------------------------------
# Requirement-structure helpers (row IDs / MoSCoW / local section)
# ---------------------------------------------------------------------------


def _extract_req_ids(hit: RetrievalHit, max_n: int = 12) -> list[str]:
    """Normalised, de-duplicated requirement IDs found in a block."""
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
    """Resolve (subtype, classification_source, local_section_label) for a req block."""
    sec = _classify_section(hit.block["heading_path"])
    if sec != "other":
        return _SUBTYPE_BY_SECTION[sec], "heading_path", ""

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
        return "", "block_text_section_switch", local
    return "", "", ""


def _req_id_count(hit: RetrievalHit) -> int:
    return len(_REQ_ID_RE.findall(_searchable_block_text(hit)))


def _req_matched_row_count(hit: RetrievalHit) -> int | None:
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
    sigs = _moscow_tiers_in_block(hit)
    if _extract_req_ids(hit, max_n=1):
        sigs.append("req_id")
    return sigs


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
    ctx: _DocCtx | None = None,
    focused_terms: frozenset[str] | None = None,
    focused_boundary: bool = False,
    rescue: bool = False,
    pair: bool = True,
    matched_signals: list[str] | None = None,
    criterion_subtype: str = "",
    classification_source: str = "",
    matched_row_count: int | None = None,
    matched_row_ids: list[str] | None = None,
    local_section_label: str = "",
    context_warning: str = "",
) -> EvidenceItem:
    """Construct an enriched EvidenceItem from a RetrievalHit.

    Excerpt construction (broader but bounded):
      * paragraph/bullet → a coherent same-section window (via ctx), or a trimmed
        block excerpt when no context is available.
      * table → header + a readable row slice; for a foreign *rescue* the excerpt
        is narrowed to the rows that actually carry the criterion signal.
      * heading → the heading text, optionally paired with nearby prose.

    `focused_excerpt` is a tighter targeted snippet; when a table/heading carries
    no obvious focused snippet, nearby explanatory prose is paired in instead.
    """
    b = hit.block
    bt = b["block_type"]
    sigs = matched_signals or []
    row_ids = matched_row_ids or []

    # --- broad excerpt ---
    if bt == "table":
        if rescue:
            excerpt = _focused_table_excerpt(
                b, focused_terms, max_rows=3, boundary=focused_boundary
            ) or _excerpt_table(b)
        else:
            excerpt = _excerpt_table(b)
    elif bt in ("paragraph", "bullet"):
        excerpt = (
            ctx.window(b["block_id"]) if ctx is not None else _excerpt_paragraph(b["text"])
        )
        if not excerpt:
            excerpt = _excerpt_paragraph(b["text"])
    else:  # heading / other
        excerpt = _excerpt_paragraph(b["text"]) or " ".join(b["heading_path"][-1:])

    # --- focused snippet (assist) ---
    if bt == "table":
        focused = _focused_table_excerpt(b, focused_terms, boundary=focused_boundary)
    elif bt in ("paragraph", "bullet"):
        focused = _focused_paragraph_excerpt(
            b["text"], focused_terms, boundary=focused_boundary
        )
    else:
        focused = ""

    # Explanation pairing: a bare structural block gains nearby prose context.
    if pair and ctx is not None and bt in ("table", "heading") and not focused:
        focused = ctx.explanation(b["block_id"])

    if focused and focused.strip() == excerpt.strip():
        focused = ""

    return EvidenceItem(
        block_id=b["block_id"],
        page_no=b["page_no"],
        block_type=bt,
        heading_path=list(b["heading_path"]),
        excerpt=excerpt,
        selection_reason=reason,
        signal_class=signal_class,
        focused_excerpt=focused,
        matched_signals=sigs,
        criterion_subtype=criterion_subtype,
        classification_source=classification_source,
        evidence_strength=_evidence_strength(signal_class, sigs, matched_row_count),
        matched_row_count=matched_row_count,
        matched_row_ids=row_ids,
        local_section_label=local_section_label,
        context_warning=context_warning,
    )


_RESCUE_WARNING: Final[str] = (
    "buiten eigen sectie aangetroffen ({fam}-sectie); relevante inhoud uitgelicht"
)


def _fam_label(fams: frozenset[str]) -> str:
    return "/".join(sorted(fams)) if fams else "onbekend"


# ---------------------------------------------------------------------------
# Criterion 1: Beperking & deskresearch
# ---------------------------------------------------------------------------


def _build_beperking_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
    ctx: _DocCtx,
) -> EvidencePacket:
    """Section-first limitation + research evidence.

    In-family = doelgroep / beperking / literatuur / bron sections. Neutral
    intro prose is admitted when it carries a limitation or research signal.
    Foreign blocks are rescued only on a disability-specific limitation token
    (never the generic substring 'beperking'), keeping budget/constraint tables
    out. Research signals that the block negates ('geen bronnen gebruikt') are
    surfaced honestly as weak, not positive.
    """
    MAX = _MAX_ITEMS["beperking"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()
    rescued = 0

    bk = _bucket("beperking", hits)
    _ALL = _BEP_LIMITATION | _BEP_RESEARCH
    seen_text: set[str] = set()

    def _text_key(h: RetrievalHit) -> str:
        # Key on the rendered window so adjacent fragments that expand to the
        # same section text (e.g. a run of single-URL paragraphs) collapse.
        if h.block["block_type"] in ("paragraph", "bullet"):
            return ctx.window(h.block["block_id"])[:160].lower()
        return " ".join(h.block["text"].split())[:120].lower()

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

    def _add(h: RetrievalHit, kind: str) -> bool:
        nonlocal rescued
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return False
        tkey = _text_key(h)
        if tkey and tkey in seen_text:
            return False  # drop near-identical duplicates (e.g. repeated URL lists)
        if kind == "rescue":
            if rescued >= _MAX_RESCUE["beperking"]:
                return False
            rescued += 1
        seen.add(bid)
        seen_text.add(tkey)
        text = h.block["text"]
        l_t = _matched_terms(h, _BEP_LIMITATION)
        r_t = _matched_terms(h, _BEP_RESEARCH)
        negated = contains_negation(text)
        sc: SignalClass = "weak" if (negated or kind == "neutral_thin") else "positive"
        warn_parts: list[str] = []
        if kind == "rescue":
            warn_parts.append(_RESCUE_WARNING.format(fam=_fam_label(bk.fams[bid])))
        if negated:
            warn_parts.append("onderbouwing/onderzoek wordt in deze passage juist ontkend")
        warn = "; ".join(warn_parts)
        if l_t and r_t:
            reason = f"Beperking + onderbouwing: {', '.join((l_t + r_t)[:3])}"
        elif l_t:
            reason = f"Beperking/doelgroep: {', '.join(l_t)}"
        elif r_t:
            reason = f"Onderbouwing/deskresearch: {', '.join(r_t)}"
        else:
            reason = "Aanvullende context bij de beperking"
            sc = "weak"
        items.append(_make_item(
            h, reason, sc, ctx=ctx, focused_terms=_ALL,
            matched_signals=(l_t + r_t)[:4],
            criterion_subtype=_subtype(h),
            classification_source="block_text" if (l_t or r_t) else "",
            context_warning=warn,
        ))
        return True

    # In-family content first (limitation-bearing, then research-bearing, then rest).
    lim_first = [h for h in bk.in_family if _has_terms(h, _BEP_LIMITATION) and not _is_heading_only(h)]
    res_first = [h for h in bk.in_family if _has_terms(h, _BEP_RESEARCH) and not _is_heading_only(h)]
    for h in lim_first + res_first + bk.in_family:
        if len(items) >= MAX:
            break
        if _is_heading_only(h):
            continue
        _add(h, "in_family")

    # Neutral intro/doelgroep prose carrying a real signal.
    for h in bk.neutral:
        if len(items) >= MAX:
            break
        if _is_heading_only(h):
            continue
        if _has_terms(h, _BEP_LIMITATION) or _has_terms(h, _BEP_RESEARCH):
            _add(h, "neutral")

    # Foreign rescue: only on a disability-specific limitation token.
    for h in bk.foreign:
        if len(items) >= MAX:
            break
        if _has_any_token(h.block["text"], _BEP_DISABILITY):
            _add(h, "rescue")

    # Heading-only fallback for an otherwise empty packet (named-but-empty section).
    if not items:
        for h in [x for x in bk.in_family if _is_heading_only(x)][:1]:
            items.append(_make_item(
                h, "Sectieheading gevonden maar geen inhoud herkend",
                "absent_marker", ctx=ctx, pair=False,
            ))

    # Coverage gaps (status-driven, unchanged contract).
    if status in ("missing", "partial"):
        has_lim = any(i.criterion_subtype in ("limitation", "limitation_and_research")
                      and i.signal_class == "positive" for i in items)
        has_res = any(i.criterion_subtype in ("research", "limitation_and_research")
                      and i.signal_class == "positive" for i in items)
        if not has_lim:
            missing_sigs.append("Geen expliciete beperking of doelgroepkeuze beschreven")
        if not has_res:
            missing_sigs.append("Geen deskresearch of bronvermelding aangetroffen")

    return EvidencePacket(
        criterion_key=spec.key,
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
    ctx: _DocCtx,
) -> EvidencePacket:
    """Section-first stakeholder evidence: prefer in-family tables, then prose.

    When the document has NO in-family stakeholder section at all (the matrix is
    an image / quadrant — common), fall back to the strongest nearby substitutes:
    foreign blocks that still carry stakeholder structure (role + belang/invloed),
    each tagged with an honest context_warning. Foreign tables are otherwise
    rejected so requirements/use-case tables do not masquerade as stakeholders.
    """
    MAX = _MAX_ITEMS["stakeholders"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()
    rescued = 0

    bk = _bucket("stakeholders", hits)
    _BI = _STK_BELANG | _STK_INVLOED

    all_text = " ".join(h.block["text"].lower() for h in hits)
    has_belang = any(t in all_text for t in _STK_BELANG)
    has_invloed = any(t in all_text for t in _STK_INVLOED)
    count = cr.count or 0
    minimum = spec.minimum_count or 4

    def _bi_signals(h: RetrievalHit) -> list[str]:
        t = h.block["text"].lower()
        sigs = []
        if any(x in t for x in _STK_BELANG):
            sigs.append("belang")
        if any(x in t for x in _STK_INVLOED):
            sigs.append("invloed")
        return sigs

    def _has_structure(h: RetrievalHit) -> bool:
        t = _searchable_block_text(h).lower()
        bi = any(x in t for x in _STK_BELANG) and any(x in t for x in _STK_INVLOED)
        role = _has_any_token(t, _STK_ROLE)
        return bi or (h.block["block_type"] == "table" and role)

    def _add(h: RetrievalHit, kind: str) -> bool:
        nonlocal rescued
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return False
        if kind == "rescue":
            if rescued >= _MAX_RESCUE["stakeholders"]:
                return False
            rescued += 1
        seen.add(bid)
        is_table = h.block["block_type"] == "table"
        n_rows = _data_row_count(h) if is_table else None
        sc: SignalClass = "weak" if (status == "partial" or kind == "rescue") else "positive"
        if kind == "rescue":
            reason = (
                f"Stakeholder-context buiten aparte sectie "
                f"({_fam_label(bk.fams[bid])}-sectie)"
            )
            warn = (
                "geen aparte stakeholder-sectie als tekst herkend (mogelijk "
                "diagram/afbeelding); nabije rol/belang/invloed-context als substituut"
            )
        else:
            reason = (
                f"Stakeholder-tabel ({n_rows} rijen)" if is_table
                else "Stakeholder-beschrijving in lopende tekst"
            )
            if status == "partial" and is_table:
                reason += f" — onvolledig ({count} van {minimum} min.)"
            warn = ""
        items.append(_make_item(
            h, reason, sc, ctx=ctx, focused_terms=_BI,
            matched_signals=_bi_signals(h),
            criterion_subtype="table" if is_table else "text",
            classification_source="block_type",
            matched_row_count=n_rows,
            context_warning=warn,
        ))
        return True

    # In-family: tables first, then prose.
    in_tables = [h for h in bk.in_family if h.block["block_type"] == "table"]
    in_prose = [h for h in bk.in_family if h.block["block_type"] != "table" and not _is_heading_only(h)]
    for h in in_tables + in_prose:
        if len(items) >= MAX:
            break
        _add(h, "in_family")

    # Neutral prose describing belang/invloed — admitted ONLY when it also names
    # a stakeholder role, so a generic "belang" mention (e.g. a "Definitie en
    # belang" heading in a security section) cannot pull the block in.
    for h in bk.neutral:
        if len(items) >= MAX:
            break
        text = h.block["text"].lower()
        if (not _is_heading_only(h)
                and any(s in text for s in _BI)
                and _has_any_token(text, _STK_ROLE)):
            _add(h, "in_family")

    # Image/quadrant fallback: only when NO in-family stakeholder evidence exists.
    if not in_tables and not in_prose:
        for h in bk.foreign:
            if len(items) >= MAX:
                break
            if _has_structure(h):
                _add(h, "rescue")

    if not items:
        for h in [x for x in (bk.in_family + bk.foreign) if _is_heading_only(x)][:1]:
            items.append(_make_item(
                h, "Stakeholder-heading gevonden maar geen tabelinhoud herkend",
                "absent_marker", ctx=ctx, pair=False,
            ))

    if status in ("missing", "partial"):
        if count and count < minimum:
            missing_sigs.append(f"Slechts {count} stakeholder(s) gevonden (minimum {minimum})")
        if not has_belang:
            missing_sigs.append("Belang per stakeholder niet beschreven")
        if not has_invloed:
            missing_sigs.append("Invloed per stakeholder niet beschreven")
        if status == "missing" and not missing_sigs:
            missing_sigs.append("Geen stakeholders herkend in de aangeleverde evidence")

    return EvidencePacket(
        criterion_key=spec.key,
        notes=cr.notes,
        evidence_items=items,
        missing_signals=missing_sigs,
    )


# ---------------------------------------------------------------------------
# Criterion 3: Requirements
# ---------------------------------------------------------------------------


def _build_requirements_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
    ctx: _DocCtx,
) -> EvidencePacket:
    """Section-first requirements evidence with representative spread.

    In-family = requirements / constraints / functional / non-functional / use
    case sections. Tables (ranked by MoSCoW rows then req-ids) form the backbone;
    in-family prose fills out narrative requirements. The selection deliberately
    interleaves subtypes (FR / NFR / UC / constraint) so the handoff reflects the
    real spread of the section rather than only the densest table.
    """
    MAX = _MAX_ITEMS["requirements"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()
    rescued = 0

    bk = _bucket("requirements", hits)
    all_text = " ".join(h.block["text"].lower() for h in hits)
    has_prio = any(t in all_text for t in ("must", "should", "could", "moscow", "prioriteit"))
    minimum = spec.minimum_count or 15

    if status == "missing":
        for h in [x for x in bk.in_family if _is_heading_only(x)][:1]:
            items.append(_make_item(
                h, "Requirements-sectieheading gevonden maar geen inhoud herkend",
                "absent_marker", ctx=ctx, pair=False,
            ))
        missing_sigs.append(f"Geen herkenbare requirements gevonden (minimum {minimum})")
        if not has_prio:
            missing_sigs.append("Geen MoSCoW-prioritering aangetroffen")
        return EvidencePacket(
            criterion_key=spec.key, notes=cr.notes,
            evidence_items=items, missing_signals=missing_sigs,
        )

    def _table_reason(h: RetrievalHit) -> tuple[str, SignalClass]:
        pcount = _prio_row_count(h)
        rcount = _req_id_count(h)
        n_rows = _data_row_count(h)
        if pcount > 0:
            return f"Requirements-tabel: {n_rows} rijen, {pcount} MoSCoW-labels", "positive"
        if rcount > 0:
            return f"Requirements-tabel: {n_rows} rijen, {rcount} req-IDs", "positive"
        return "Requirements-tabel (beperkte inhoud herkend)", (
            "weak" if status == "partial" else "positive"
        )

    def _add(h: RetrievalHit, reason: str, sc: SignalClass, kind: str = "in_family") -> bool:
        nonlocal rescued
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return False
        if kind == "rescue":
            if rescued >= _MAX_RESCUE["requirements"]:
                return False
            rescued += 1
        seen.add(bid)
        subtype, source, local = _requirement_subtype(h)
        warn = (
            "heading_path mogelijk verouderd; lokale sectie gedetecteerd"
            if (local and source == "block_text_section_switch") else ""
        )
        if kind == "rescue":
            warn = (warn + "; " if warn else "") + _RESCUE_WARNING.format(
                fam=_fam_label(bk.fams[bid])
            )
        items.append(_make_item(
            h, reason, sc, ctx=ctx,
            matched_signals=_req_signals(h),
            criterion_subtype=subtype,
            classification_source=source,
            matched_row_count=_req_matched_row_count(h),
            matched_row_ids=_extract_req_ids(h),
            local_section_label=local,
            context_warning=warn,
            rescue=(kind == "rescue"),
            focused_terms=None,
        ))
        return True

    in_tables = sorted(
        [h for h in bk.in_family if h.block["block_type"] == "table"],
        key=lambda h: (-_prio_row_count(h), -_req_id_count(h), -h.score, h.block["block_id"]),
    )
    in_prose = _sort_content_first(
        [h for h in bk.in_family if h.block["block_type"] in ("paragraph", "bullet")]
    )

    # Representative spread: take the strongest table of each subtype first so FR,
    # NFR, UC and constraints all surface before piling on more of one kind.
    by_subtype: dict[str, list[RetrievalHit]] = {}
    for h in in_tables:
        st = _requirement_subtype(h)[0] or "other"
        by_subtype.setdefault(st, []).append(h)
    spread_first: list[RetrievalHit] = []
    for st in ("functional", "non_functional", "use_case", "constraint", "other"):
        if by_subtype.get(st):
            spread_first.append(by_subtype[st][0])
    ordered_tables = spread_first + [h for h in in_tables if h not in spread_first]

    for h in ordered_tables:
        if len(items) >= MAX:
            break
        reason, sc = _table_reason(h)
        _add(h, reason, sc)

    for h in in_prose:
        if len(items) >= MAX:
            break
        rcount = _req_id_count(h)
        if rcount > 0:
            _add(h, f"Requirements in tekst ({rcount} IDs herkend)", "positive")
        else:
            _add(h, "Requirements in tekst", "weak")

    # Foreign rescue (rare): only blocks that actually carry requirement structure.
    for h in bk.foreign:
        if len(items) >= MAX:
            break
        if _extract_req_ids(h, max_n=1) or _prio_row_count(h) > 0:
            reason, sc = (
                _table_reason(h) if h.block["block_type"] == "table"
                else ("Requirements in tekst (buiten requirements-sectie)", "weak")
            )
            _add(h, reason, sc, kind="rescue")

    if status == "partial":
        missing_sigs.append(f"Slechts ~{cr.count or 0} requirements gevonden (minimum {minimum})")
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
    ctx: _DocCtx,
) -> EvidencePacket:
    """Token-gated taalkeuze evidence (language choice has no fixed section).

    Because language choice is discussed inside doelgroep / requirements / intro
    prose, taalkeuze is gated on a genuine CHOICE token rather than on section
    identity. A block is admitted only when it states a language choice; a
    consequence-only block (carrying just 'beïnvloed' / 'bereik') is admitted
    solely as supporting context once a choice block exists, and a CONSEQUENCE-
    ONLY foreign block (e.g. a stakeholder table) is rejected outright — the most
    common contamination source for this criterion.
    """
    MAX = _MAX_ITEMS["taalkeuze"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()
    rescued = 0
    _ALL = _TAAL_CHOICE | _TAAL_CONSEQUENCE

    bk = _bucket("taalkeuze", hits)
    all_text = " ".join(h.block["text"].lower() for h in hits)
    has_choice = _has_any_token(all_text, _TAAL_CHOICE)
    has_consequence = _has_any_token(all_text, _TAAL_CONSEQUENCE)

    def _subtype(text: str) -> str:
        c = _has_any_token(text, _TAAL_CHOICE)
        k = _has_any_token(text, _TAAL_CONSEQUENCE)
        if c and k:
            return "choice_and_consequence"
        if c:
            return "choice"
        if k:
            return "consequence"
        return ""

    def _add(h: RetrievalHit, kind: str) -> bool:
        nonlocal rescued
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return False
        if kind == "rescue":
            if rescued >= _MAX_RESCUE["taalkeuze"]:
                return False
            rescued += 1
        seen.add(bid)
        text = h.block["text"]
        c_terms = _matched_tokens(text, _TAAL_CHOICE, 2)
        k_terms = _matched_tokens(text, _TAAL_CONSEQUENCE, 2)
        negated = contains_negation(text)
        if c_terms and k_terms:
            reason = f"Taalkeuze ({', '.join(c_terms)}) én gevolgen ({', '.join(k_terms)})"
        elif c_terms:
            reason = f"Taalkeuze: {', '.join(c_terms)}"
        else:
            reason = f"Gevolgen van de taalkeuze: {', '.join(k_terms)}"
        sc: SignalClass = "positive"
        warn_parts: list[str] = []
        if kind == "rescue":
            warn_parts.append(_RESCUE_WARNING.format(fam=_fam_label(bk.fams[bid])))
        if negated:
            sc = "weak"
            warn_parts.append("taalkeuze wordt in deze passage juist als onbeslist beschreven")
        elif kind == "consequence_support":
            sc = "weak"
        warn = "; ".join(warn_parts)
        items.append(_make_item(
            h, reason, sc, ctx=ctx,
            focused_terms=_ALL, focused_boundary=True, rescue=(kind == "rescue"),
            matched_signals=(c_terms + k_terms)[:4],
            criterion_subtype=_subtype(text),
            classification_source="block_text" if (c_terms or k_terms) else "",
            context_warning=warn,
        ))
        return True

    def _is_choice(h: RetrievalHit) -> bool:
        return _has_any_token(h.block["text"], _TAAL_CHOICE)

    # Choice-bearing in-family/neutral blocks (both signals first).
    choice_inneu = [
        h for h in (bk.in_family + bk.neutral)
        if _is_choice(h) and not _is_heading_only(h)
    ]
    both = [h for h in choice_inneu if _has_any_token(h.block["text"], _TAAL_CONSEQUENCE)]
    for h in both + choice_inneu:
        if len(items) >= MAX:
            break
        _add(h, "in_family")

    # Foreign rescue: ONLY blocks that carry a real choice token.
    for h in bk.foreign:
        if len(items) >= MAX:
            break
        if _is_choice(h) and not _is_heading_only(h):
            _add(h, "rescue")

    # Consequence-only support, but only once a choice block is present.
    if items and any(i.criterion_subtype in ("choice", "choice_and_consequence") for i in items):
        for h in (bk.in_family + bk.neutral):
            if len(items) >= MAX:
                break
            if (not _is_heading_only(h) and not _is_choice(h)
                    and _has_any_token(h.block["text"], _TAAL_CONSEQUENCE)):
                _add(h, "consequence_support")

    if not items:
        for h in [x for x in bk.in_family if _is_heading_only(x)][:1]:
            items.append(_make_item(
                h, "Taalkeuze-heading zonder inhoud aangetroffen",
                "absent_marker", ctx=ctx, pair=False,
            ))

    if status in ("missing", "partial"):
        if not has_choice:
            missing_sigs.append("Geen expliciete taalkeuze vermeld")
        if not has_consequence:
            missing_sigs.append(
                "Gevolgen van taalkeuze niet beschreven (bijv. bereik, vertaalkosten, begrijpelijkheid)"
            )
        if not missing_sigs:
            missing_sigs.append("Taalkeuze of gevolgen niet volledig beschreven")

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
    ctx: _DocCtx,
) -> EvidencePacket:
    """Section-first security evidence with measure↔risk pairing.

    In-family = security / beveiliging / privacy / AVG / risico sections. Neutral
    blocks (e.g. ethics paragraphs) are admitted when they name a concrete
    mechanism. Because security legitimately lives inside non-functional
    requirements, foreign REQUIREMENTS blocks are rescued on a concrete-mechanism
    token (focused to the relevant rows); foreign stakeholder/doelgroep blocks
    are rejected — they are the contamination source (a stakeholder table
    mentioning 'AVGregels', a doelgroep paragraph where 'ssl' hides inside a
    word). Concrete tokens are matched with word boundaries throughout.
    """
    MAX = _MAX_ITEMS["security"]
    status = cr.status
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()
    rescued = 0
    _ALL = _SEC_CONCRETE | _SEC_GENERIC

    bk = _bucket("security", hits)

    def _concrete(h: RetrievalHit) -> list[str]:
        return _matched_tokens(h.block["text"], _SEC_CONCRETE, 4)

    def _add(h: RetrievalHit, kind: str) -> bool:
        nonlocal rescued
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return False
        if kind == "rescue":
            if rescued >= _MAX_RESCUE["security"]:
                return False
            rescued += 1
        seen.add(bid)
        terms = _concrete(h)
        if terms:
            subtype, sc = "concrete", ("weak" if status == "partial" else "positive")
            reason = f"Concrete beveiligingsmechanisme(n): {', '.join(terms)}"
            sigs = terms
        else:
            subtype, sc = "generic", "weak"
            gterms = _matched_tokens(h.block["text"], _SEC_GENERIC, 3)
            reason = f"Aanvullende beveiligingscontext: {', '.join(gterms)}"
            sigs = gterms
        warn = ""
        if kind == "rescue":
            warn = _RESCUE_WARNING.format(fam=_fam_label(bk.fams[bid]))
            if sc == "positive":
                sc = "positive"  # keep — it is a genuine concrete row in an NFR table
        items.append(_make_item(
            h, reason, sc, ctx=ctx,
            focused_terms=_ALL, focused_boundary=True, rescue=(kind == "rescue"),
            matched_signals=sigs,
            criterion_subtype=subtype,
            classification_source="block_text" if sigs else "",
            context_warning=warn,
        ))
        return True

    # In-family concrete first, then in-family generic.
    in_concrete = [h for h in bk.in_family if _concrete(h) and not _is_heading_only(h)]
    in_generic = [
        h for h in bk.in_family
        if not _concrete(h) and _has_any_token(h.block["text"], _SEC_GENERIC)
        and not _is_heading_only(h)
    ]
    for h in in_concrete:
        if len(items) >= MAX:
            break
        _add(h, "in_family")

    # Neutral blocks naming a concrete mechanism (e.g. ethics/AVG/OWASP prose).
    for h in bk.neutral:
        if len(items) >= MAX:
            break
        if _concrete(h) and not _is_heading_only(h):
            _add(h, "neutral")

    # Foreign rescue: requirements-family blocks only, on a trusted concrete token.
    for h in bk.foreign:
        if len(items) >= MAX:
            break
        fam = bk.fams[h.block["block_id"]]
        if "requirements" in fam and _has_any_token(h.block["text"], _SEC_RESCUE):
            _add(h, "rescue")

    # Fill remaining slots with in-family generic context (clearly weaker).
    for h in in_generic:
        if len(items) >= MAX:
            break
        _add(h, "in_family")

    if not items:
        for h in [x for x in bk.in_family if _is_heading_only(x)][:1]:
            items.append(_make_item(
                h, "Security-heading gevonden maar geen inhoud herkend",
                "absent_marker", ctx=ctx, pair=False,
            ))

    if status in ("missing", "partial"):
        if status == "missing":
            missing_sigs.append("Geen beveiligingsinhoud aangetroffen")
        missing_sigs.append(
            "Geen concrete beveiligingsmechanismen benoemd (bijv. authenticatie, encryptie, AVG)"
        )

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

    CAPS is the source of truth for all judgements. This function only SELECTS
    and SHAPES evidence — it never re-judges or overrides any CAPS decision.

    Selection is section-first (section_family.py): in-family blocks form the
    backbone, neutral blocks add supporting context when they carry a real
    signal, and foreign blocks are rescued only on a strong, criterion-defining
    token. Excerpts are widened into coherent same-section windows using the
    document's full ordered block list (artifacts.blocks).
    """
    packets: dict[str, EvidencePacket] = {}
    ctx = _DocCtx.build(artifacts.blocks or [])

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
        packets[key] = builder(spec, hits, cr, ctx)

    return packets
