"""packet_builder.py — strict, heading-only per-criterion evidence selection.

Takes a CapsPipelineArtifacts object (retrieval candidates + per-criterion
results + final run result + full ordered blocks) and produces one
EvidencePacket per criterion for the CAPS→Qwen quality handoff.

Architecture position:
    CapsPipelineArtifacts → build_evidence_packets → dict[str, EvidencePacket]

CAPS is the source of truth for every verdict (status, count, stoplight).
This module only SELECTS and SHAPES evidence — it never re-judges or overrides
any CAPS decision.

Selection strategy: HEADING-ONLY (precision-first)
--------------------------------------------------
Evidence admission is driven SOLELY by section identity (section_family.py):

    A block is admitted for a criterion if, and only if, its heading_path
    belongs to that criterion's section family.

There is intentionally NO:
    - neutral-block promotion on a keyword hit,
    - foreign-section rescue (a security token in a use-case/NFR table),
    - image / quadrant stakeholder fallback,
    - stakeholder inference from actor/use-case structure,
    - taalkeuze inference from scattered language mentions,
    - requirements expansion into use-case sections,
    - count-driven selection or count-driven notes.

If a document lacks a section family for a criterion, that criterion simply
gets little or no evidence. Lower recall, higher precision — by design.

Use cases are NOT requirements: a block under a use-case heading is rejected
for requirements even when nested beneath a requirements heading
(_classify_section == "uc").

Per-item enrichment (subtype, matched row-ids, MoSCoW tiers, focused excerpt)
is kept ONLY as an honest description of the already-admitted block. It never
admits, rescues, or re-ranks across section families. Short tokens are matched
with word boundaries so they cannot fire inside unrelated words.

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
    is_in_family,
    primary_families,
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

# ---------------------------------------------------------------------------
# Lightweight signal term sets (LABELLING ONLY — never admission)
# ---------------------------------------------------------------------------
# These describe what an already-admitted in-section block contains. They are
# used for criterion_subtype, matched_signals and missing_signals — never to
# admit, rescue, or re-rank a block across section families.

_BEP_LIMITATION: Final[frozenset[str]] = frozenset({
    "beperking", "slechtziend", "blind", "doof", "dyslexie",
    "visuele beperking", "motorische", "auditieve", "cognitieve",
    "afbakening", "doelgroep met beperking", "specifieke doelgroep",
    "niche", "focus op", "gekozen doelgroep", "toegankelijkheid",
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
    "nederlands", "engels", "duits", "frans",
})

_TAAL_CONSEQUENCE: Final[frozenset[str]] = frozenset({
    "bereik", "vertaalkosten", "lokalisatie", "consequentie",
    "begrijpelijk", "taalbarrière", "taalbarrier", "vertaling",
    "internationaal", "beïnvloeden", "beïnvloed", "doelgroepbereik",
})

# High-precision language-CHOICE phrases. This is the ONE explicit cross-section
# exception in the whole builder: taalkeuze has no dedicated heading family in
# the corpus (language choice is stated inside doelgroep / requirements / intro
# prose), so a block from ANY section is admitted when — and only when — it
# carries one of these decision-bearing phrases. Deliberately multi-word /
# concept-noun: bare tokens ("nederlands", "engels", "meertalig") are excluded
# because they fire on doelgroep descriptions ("meertalige gebruikers") rather
# than on an actual language choice. Corpus-validated: catches "taalkeuzeknop …
# taal van de webshop", "de website alleen in het nederlands beschikbaar",
# "Meertaligheid verbetert …", "NFR06 Taal | De website is in het Nederlands",
# while rejecting the "meertalige mensen/gebruikers" noise.
_TAAL_CHOICE_STRICT: Final[frozenset[str]] = frozenset({
    "taalkeuze",            # also matches "taalkeuzeknop"
    "in het nederlands", "in het engels", "in het duits", "in het frans",
    "nederlandstalig", "engelstalig", "anderstalig",
    "meertaligheid", "tweetalig",
    "taal van de", "standaardtaal",
    "beschikbaar in het",
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

_STK_BELANG: Final[frozenset[str]] = frozenset({"belang", "concern", "interesse"})
_STK_INVLOED: Final[frozenset[str]] = frozenset({"invloed", "macht", "prioriter"})

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
    tokens from firing inside unrelated words. Acronym-like tokens (≤4 chars or
    with a digit) also require a trailing boundary, so "AVG" matches but
    "AVGregels" does not. Longer Dutch word tokens allow a trailing suffix so
    normal inflection still matches ("wachtwoord" → "wachtwoorden").
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
        stopping at max_chars. Never widens into a different heading_path, so the
        excerpt stays inside the selected heading family.
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

    def tile_window(
        self,
        anchor_bid: str,
        covered: set[int],
        max_chars: int,
        budget_left: int,
    ) -> tuple[str, set[int]]:
        """Coverage-oriented window: expand over UNCOVERED same-section neighbours.

        Unlike window(), this tiles a section across multiple items: it expands
        from the anchor over adjacent paragraph/bullet blocks that share the
        heading_path AND are not yet covered, so the union of all items' excerpts
        spans the section without repeating text.

        Returns (text, newly_covered_indices). Returns ("", set()) when the
        anchor is already covered (the item is redundant) or no budget remains.
        """
        i = self.pos.get(anchor_bid)
        if i is None or i in covered:
            return "", set()
        cap = max(0, min(max_chars, budget_left))
        if cap <= 0:
            return "", set()

        hp = self.blocks[i]["heading_path"]
        chosen = [i]
        running = len(" ".join(self.blocks[i]["text"].split()))

        def _extend(step: int) -> None:
            nonlocal running
            j = i + step
            while 0 <= j < len(self.blocks):
                if j in covered:
                    break
                b = self.blocks[j]
                if b["block_type"] not in ("paragraph", "bullet"):
                    break
                if b["heading_path"] != hp:
                    break
                t_len = len(" ".join(b["text"].split()))
                if running + t_len + 1 > cap:
                    break
                chosen.append(j)
                running += t_len + 1
                j += step

        _extend(1)
        _extend(-1)
        idxs = sorted(chosen)
        text = " ".join(" ".join(self.blocks[k]["text"].split()) for k in idxs)
        text = " ".join(text.split())
        if len(text) > cap:
            text = text[:cap].rstrip() + "…"
        return text, set(idxs)

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
    """Format a table as 'header | header\\nrow | row…' for compact display."""
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
    """Cleaner table snippet: header + the first rows that carry a signal."""
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
    """Substring presence of any term in the block's text (labelling use)."""
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
# Section-family admission (heading-only)
# ---------------------------------------------------------------------------


def _in_family_hits(criterion_key: str, hits: list[RetrievalHit]) -> list[RetrievalHit]:
    """In-family hits for a criterion, content-first ordered.

    A hit is in-family when its DEEPEST heading segment belongs to the
    criterion's section family (primary_families). Nothing else is admitted — no
    neutral promotion, no foreign rescue, and no ancestor/stale-root carryover.
    """
    in_f = [
        h for h in hits
        if is_in_family(criterion_key, primary_families(h.block["heading_path"]))
    ]
    return _sort_content_first(in_f)


# ---------------------------------------------------------------------------
# Requirement-structure helpers (row IDs / MoSCoW)
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


def _requirement_subtype(hit: RetrievalHit) -> tuple[str, str]:
    """Resolve (subtype, classification_source) for a requirement block.

    Heading identity wins. When the heading is combined/unlabelled ("other"),
    fall back to per-row requirement-ID prefixes (FR/NFR/CON). No block-text
    section-switch heuristic — that rescue logic was removed.
    """
    sec = _classify_section(hit.block["heading_path"])
    if sec != "other":
        return _SUBTYPE_BY_SECTION[sec], "heading_path"

    ids = _extract_req_ids(hit, max_n=24)
    has_fr = any(re.match(r"FR\d", i) for i in ids)
    has_nfr = any(re.match(r"NFR\d", i) for i in ids)
    has_con = any(re.match(r"(CONSTR|CON)\d", i) for i in ids)

    if has_nfr and not has_fr:
        return "non_functional", "row_id_prefix"
    if has_fr and not has_nfr:
        return "functional", "row_id_prefix"
    if has_con and not has_fr and not has_nfr:
        return "constraint", "row_id_prefix"
    return "", ""


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


_MOSCOW_HAVE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(must|should|could|won'?t)[\s\-]?have\b", re.IGNORECASE
)


def _req_signals_from_text(text: str) -> tuple[list[str], list[str]]:
    """(matched_signals, req_ids) found in a plain text string.

    Used to RE-describe a requirements prose item after coverage tiling, so a
    MoSCoW label or req-ID that lives in a merged same-section neighbour (e.g. a
    "Must Have:" header split from its item) is reflected in the metadata. This
    only labels text that was already selected by the heading-based gate — it
    never admits anything.
    """
    tiers: set[str] = set()
    for m in _MOSCOW_HAVE_RE.finditer(text):
        tiers.add(_tier_of(m.group(1)))
    order = ["must", "should", "could", "wont"]
    sigs = [f"moscow:{t}" for t in order if t in tiers]

    seen: set[str] = set()
    ids: list[str] = []
    for m in _REQ_ID_RE.finditer(text):
        norm = m.group(0).upper().replace(" ", "").replace("-", "")
        if norm not in seen:
            seen.add(norm)
            ids.append(norm)
        if len(ids) >= 12:
            break
    if ids:
        sigs.append("req_id")
    return sigs, ids


def _is_use_case_section(hit: RetrievalHit) -> bool:
    """True when the block's most-specific requirement section is a use case.

    Use cases are not requirements. A use-case subsection nested under a
    requirements heading is therefore excluded from the requirements packet.
    """
    return _classify_section(hit.block["heading_path"]) == "uc"


# ---------------------------------------------------------------------------
# Evidence-item construction
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
    pair: bool = True,
    matched_signals: list[str] | None = None,
    criterion_subtype: str = "",
    classification_source: str = "",
    matched_row_count: int | None = None,
    matched_row_ids: list[str] | None = None,
    context_warning: str = "",
    excerpt_override: str = "",
) -> EvidenceItem:
    """Construct an EvidenceItem from an in-family RetrievalHit.

    Excerpts stay inside the selected heading family:
      * paragraph/bullet → a coherent same-section window (via ctx),
      * table → header + a readable row slice,
      * heading → the heading text, optionally paired with nearby same-section
        prose.

    excerpt_override, when non-empty, replaces the computed broad excerpt. It is
    used by the taalkeuze choice-phrase fallback so an item found inside an
    unrelated NFR/use-case table shows only the language-bearing row, not the
    whole foreign table.
    """
    b = hit.block
    bt = b["block_type"]
    sigs = matched_signals or []
    row_ids = matched_row_ids or []

    # --- broad excerpt (same-section only) ---
    if excerpt_override:
        excerpt = excerpt_override
    elif bt == "table":
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
    if excerpt_override:
        # The override already IS the tight, targeted excerpt — a second focused
        # snippet (which may surface an unrelated req-id row) would only mislead.
        focused = ""
    elif bt == "table":
        focused = _focused_table_excerpt(b, focused_terms, boundary=focused_boundary)
    elif bt in ("paragraph", "bullet"):
        focused = _focused_paragraph_excerpt(
            b["text"], focused_terms, boundary=focused_boundary
        )
    else:
        focused = ""

    # A bare structural block gains nearby same-section prose context (never for
    # an override, whose neighbours are the unrelated foreign section).
    if pair and not excerpt_override and ctx is not None and bt in ("table", "heading") and not focused:
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
        local_section_label="",
        context_warning=context_warning,
    )


def _heading_only_marker(
    criterion_key: str,
    hits: list[RetrievalHit],
    reason: str,
    ctx: _DocCtx,
) -> list[EvidenceItem]:
    """One absent_marker item from an in-family heading-only block, if any.

    Surfaces a 'section named but not populated' situation. Returns at most one
    item, or an empty list when no in-family heading block exists.
    """
    for h in _in_family_hits(criterion_key, hits):
        if _is_heading_only(h):
            return [_make_item(h, reason, "absent_marker", ctx=ctx, pair=False)]
    return []


# ---------------------------------------------------------------------------
# Criterion 1: Beperking & deskresearch
# ---------------------------------------------------------------------------


# Literatuurlijst / bron section detection for the deskresearch half. A real
# reference list contains at least one concrete source — a URL or a year
# citation. A heading with neither is an empty/insufficient reference list.
_BRON_HEADING_RE: Final[re.Pattern[str]] = re.compile(
    r"literatuur|bron(?:nen|vermelding)?\b|deskresearch", re.IGNORECASE
)
_CONCRETE_SOURCE_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://|www\.|\b(?:19|20)\d{2}\b", re.IGNORECASE
)


def _empty_bron_section(ctx: _DocCtx) -> BlockDict | None:
    """Return the bron/literatuur heading block when such a section exists in the
    FULL document but contains no concrete source (URL or year citation); else None.

    Scans the full ordered block list — not the ≤max_candidates retrieval hits —
    so a populated reference list whose URL blocks happened to rank low is never
    wrongly flagged as empty.
    """
    section = [
        b for b in ctx.blocks
        if any(_BRON_HEADING_RE.search(s) for s in b["heading_path"])
    ]
    if not section:
        return None
    if any(_CONCRETE_SOURCE_RE.search(b["text"]) for b in section):
        return None
    return next((b for b in section if b["block_type"] == "heading"), section[0])


def _build_beperking_packet(
    spec: CriterionSpec,
    hits: list[RetrievalHit],
    cr: CriterionResult,
    ctx: _DocCtx,
) -> EvidencePacket:
    """Heading-only limitation + deskresearch evidence.

    In-family = doelgroep / onderzoek-naar-web-gebruikers / beperking /
    toegankelijkheid sections plus the deskresearch headings (literatuurlijst /
    bronvermelding). Limitation/research labels describe the selected blocks
    only. A block that negates its own research signal is surfaced as weak.
    """
    MAX = _MAX_ITEMS["beperking"]
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()
    seen_text: set[str] = set()
    _ALL = _BEP_LIMITATION | _BEP_RESEARCH

    content = [h for h in _in_family_hits("beperking", hits) if not _is_heading_only(h)]

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

    def _text_key(h: RetrievalHit) -> str:
        if h.block["block_type"] in ("paragraph", "bullet"):
            return ctx.window(h.block["block_id"])[:160].lower()
        return " ".join(h.block["text"].split())[:120].lower()

    # Reserve one slot for the bron-gap marker (added below) so an empty
    # literatuurlijst is still reported even when the doelgroep section would
    # otherwise fill the whole budget.
    gap_block = _empty_bron_section(ctx)
    content_max = MAX - 1 if gap_block is not None else MAX

    # Limitation-bearing blocks first, then research-bearing, then the rest.
    lim_first = [h for h in content if _has_terms(h, _BEP_LIMITATION)]
    res_first = [h for h in content if _has_terms(h, _BEP_RESEARCH)]
    for h in lim_first + res_first + content:
        if len(items) >= content_max:
            break
        bid = h.block["block_id"]
        if bid in seen:
            continue
        tkey = _text_key(h)
        if tkey and tkey in seen_text:
            continue
        seen.add(bid)
        seen_text.add(tkey)
        l_t = _matched_terms(h, _BEP_LIMITATION)
        r_t = _matched_terms(h, _BEP_RESEARCH)
        negated = contains_negation(h.block["text"])
        if l_t and r_t:
            reason = f"Beperking + onderbouwing: {', '.join((l_t + r_t)[:3])}"
        elif l_t:
            reason = f"Beperking/doelgroep: {', '.join(l_t)}"
        elif r_t:
            reason = f"Onderbouwing/deskresearch: {', '.join(r_t)}"
        else:
            reason = "Inhoud in de beperking/doelgroep-sectie"
        sc: SignalClass = "weak" if (negated or not (l_t or r_t)) else "positive"
        warn = "onderbouwing/onderzoek wordt in deze passage juist ontkend" if negated else ""
        items.append(_make_item(
            h, reason, sc, ctx=ctx, focused_terms=_ALL,
            matched_signals=(l_t + r_t)[:4],
            criterion_subtype=_subtype(h),
            classification_source="block_text" if (l_t or r_t) else "",
            context_warning=warn,
        ))

    if not items:
        items = _heading_only_marker(
            "beperking", hits,
            "Sectieheading gevonden maar geen inhoud herkend", ctx,
        )

    # Bronnen-check (deskresearch-helft): flag a literatuurlijst/bron-sectie that
    # exists but lists no concrete sources (no URL, no year citation). Surfaced as
    # an absent_marker evidence item so the quality stage sees it, even though
    # CAPS' broad term-match would otherwise treat the bare heading as research.
    if gap_block is not None and gap_block["block_id"] not in seen and len(items) < MAX:
        seen.add(gap_block["block_id"])
        items.append(EvidenceItem(
            block_id=gap_block["block_id"],
            page_no=gap_block["page_no"],
            block_type=gap_block["block_type"],
            heading_path=list(gap_block["heading_path"]),
            excerpt=_excerpt_paragraph(gap_block["text"])
                    or " ".join(gap_block["heading_path"][-1:]),
            selection_reason=(
                "Literatuurlijst/bron-sectie aanwezig maar zonder concrete "
                "bronnen (geen URL of verwijzing)"
            ),
            signal_class="absent_marker",
            evidence_strength="absent",
        ))
        gap_note = "Literatuurlijst zonder concrete bronnen (geen URL of verwijzing)"
        if gap_note not in missing_sigs:
            missing_sigs.append(gap_note)

    if cr.status in ("missing", "partial"):
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
    """Heading-only stakeholder evidence: in-family tables first, then prose.

    No image/quadrant fallback and no actor/use-case inference: when a document
    has no stakeholder heading, the packet is empty (or an absent_marker). A
    requirements/use-case table can never masquerade as a stakeholder table.
    """
    MAX = _MAX_ITEMS["stakeholders"]
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    _BI = _STK_BELANG | _STK_INVLOED

    content = [h for h in _in_family_hits("stakeholders", hits) if not _is_heading_only(h)]

    def _bi_signals(h: RetrievalHit) -> list[str]:
        t = h.block["text"].lower()
        sigs = []
        if any(x in t for x in _STK_BELANG):
            sigs.append("belang")
        if any(x in t for x in _STK_INVLOED):
            sigs.append("invloed")
        return sigs

    tables = [h for h in content if h.block["block_type"] == "table"]
    prose = [h for h in content if h.block["block_type"] != "table"]
    for h in tables + prose:
        if len(items) >= MAX:
            break
        is_table = h.block["block_type"] == "table"
        n_rows = _data_row_count(h) if is_table else None
        sc: SignalClass = "weak" if cr.status == "partial" else "positive"
        reason = (
            f"Stakeholder-tabel ({n_rows} rijen)" if is_table
            else "Stakeholder-beschrijving in lopende tekst"
        )
        items.append(_make_item(
            h, reason, sc, ctx=ctx, focused_terms=_BI,
            matched_signals=_bi_signals(h),
            criterion_subtype="table" if is_table else "text",
            classification_source="block_type",
            matched_row_count=n_rows,
        ))

    if not items:
        items = _heading_only_marker(
            "stakeholders", hits,
            "Stakeholder-heading gevonden maar geen tabelinhoud herkend", ctx,
        )

    if cr.status in ("missing", "partial"):
        has_belang = any("belang" in i.matched_signals for i in items)
        has_invloed = any("invloed" in i.matched_signals for i in items)
        if not items:
            missing_sigs.append("Geen stakeholder-sectie aangetroffen")
        if not has_belang:
            missing_sigs.append("Belang per stakeholder niet beschreven")
        if not has_invloed:
            missing_sigs.append("Invloed per stakeholder niet beschreven")

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
    """Heading-only requirements evidence: in-family tables then prose.

    In-family = requirements / eisen / wensen / constraints / functional /
    non-functional sections — explicitly NOT use cases. A use-case subsection
    (even nested under a requirements heading) is excluded. No subtype-spread
    interleaving and no foreign rescue: just the strongest in-section tables,
    then in-section prose.
    """
    MAX = _MAX_ITEMS["requirements"]
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()

    content = [
        h for h in _in_family_hits("requirements", hits)
        if not _is_heading_only(h) and not _is_use_case_section(h)
    ]

    if cr.status == "missing" or not content:
        items = _heading_only_marker(
            "requirements", hits,
            "Requirements-sectieheading gevonden maar geen inhoud herkend", ctx,
        )
        if cr.status in ("missing", "partial"):
            missing_sigs.append("Geen herkenbare requirements gevonden")
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
            "weak" if cr.status == "partial" else "positive"
        )

    def _add(h: RetrievalHit, reason: str, sc: SignalClass) -> None:
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return
        seen.add(bid)
        subtype, source = _requirement_subtype(h)
        items.append(_make_item(
            h, reason, sc, ctx=ctx,
            matched_signals=_req_signals(h),
            criterion_subtype=subtype,
            classification_source=source,
            matched_row_count=_req_matched_row_count(h),
            matched_row_ids=_extract_req_ids(h),
        ))

    tables = sorted(
        [h for h in content if h.block["block_type"] == "table"],
        key=lambda h: (-_prio_row_count(h), -_req_id_count(h), -h.score, h.block["block_id"]),
    )
    prose = _sort_content_first(
        [h for h in content if h.block["block_type"] in ("paragraph", "bullet")]
    )

    for h in tables:
        if len(items) >= MAX:
            break
        reason, sc = _table_reason(h)
        _add(h, reason, sc)

    for h in prose:
        if len(items) >= MAX:
            break
        rcount = _req_id_count(h)
        if rcount > 0:
            _add(h, f"Requirements in tekst ({rcount} IDs herkend)", "positive")
        else:
            _add(h, "Requirements in tekst", "weak")

    has_prio = any(
        any(s.startswith("moscow:") for s in it.matched_signals) for it in items
    )
    if cr.status == "partial":
        missing_sigs.append("Requirements-sectie aanwezig maar mogelijk onvolledig")
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
    """Heading-primary taalkeuze evidence with a narrow choice-phrase fallback.

    Primary: blocks under a real language-choice heading (rare in this corpus).
    Fallback: language choice has no dedicated heading here — it is stated inside
    doelgroep / requirements / intro prose — so a block from ANY section is
    admitted when, and only when, it carries an explicit language-CHOICE phrase
    (_TAAL_CHOICE_STRICT). This is the single, deliberate cross-section exception
    in the builder; it is gated on decision-bearing multi-word phrases, never on
    bare tokens, and every such item is tagged with an honest context_warning.
    """
    MAX = _MAX_ITEMS["taalkeuze"]
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()
    _ALL = _TAAL_CHOICE | _TAAL_CONSEQUENCE

    def _strict_phrases(text: str) -> list[str]:
        low = text.lower()
        return [p for p in _TAAL_CHOICE_STRICT if p in low]

    def _lang_rows_excerpt(block: dict) -> str:
        """Header + only the table rows that actually carry a language phrase."""
        tm = block.get("table_meta")
        if not tm or not tm.get("cells"):
            return ""
        rows = [
            r for r in tm["cells"]
            if _strict_phrases(_row_text(r)) or _has_any_token(_row_text(r), _TAAL_CHOICE)
        ]
        if not rows:
            return ""
        lines: list[str] = []
        header = tm.get("header_row")
        if header:
            lines.append(header.strip())
        for r in rows[:2]:
            rt = _row_text(r)
            if rt:
                lines.append(rt)
        out = "\n".join(lines)
        return out[:260].rstrip() + ("…" if len(out) > 260 else "")

    def _add(h: RetrievalHit, *, outside_section: bool) -> None:
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return
        seen.add(bid)
        text = h.block["text"]
        # A genuine choice signal is either a _TAAL_CHOICE token or, failing that,
        # one of the high-precision strict phrases that admitted the block.
        c_terms = _matched_tokens(text, _TAAL_CHOICE, 2) or _strict_phrases(text)[:2]
        k_terms = _matched_tokens(text, _TAAL_CONSEQUENCE, 2)
        negated = contains_negation(text)
        if c_terms and k_terms:
            reason = f"Taalkeuze ({', '.join(c_terms)}) én gevolgen ({', '.join(k_terms)})"
        elif c_terms:
            reason = f"Taalkeuze: {', '.join(c_terms)}"
        elif k_terms:
            reason = f"Gevolgen van de taalkeuze: {', '.join(k_terms)}"
        else:
            reason = "Inhoud in de taalkeuze-sectie"
        if c_terms and k_terms:
            subtype = "choice_and_consequence"
        elif c_terms:
            subtype = "choice"
        elif k_terms:
            subtype = "consequence"
        else:
            subtype = ""
        sc: SignalClass = "weak" if (negated or not (c_terms or k_terms)) else "positive"
        warn_parts: list[str] = []
        if outside_section:
            warn_parts.append("taalkeuze besproken buiten een aparte taalsectie")
        if negated:
            sc = "weak"
            warn_parts.append("taalkeuze wordt in deze passage juist als onbeslist beschreven")
        # For a fallback hit inside a foreign table, narrow the excerpt to the
        # language-bearing row(s) instead of dumping the whole NFR/UC table.
        override = ""
        if outside_section and h.block["block_type"] == "table":
            override = _lang_rows_excerpt(h.block)
        items.append(_make_item(
            h, reason, sc, ctx=ctx, focused_terms=_ALL, focused_boundary=True,
            matched_signals=(c_terms + k_terms)[:4],
            criterion_subtype=subtype,
            classification_source="block_text" if (c_terms or k_terms) else "",
            context_warning="; ".join(warn_parts),
            excerpt_override=override,
        ))

    # 1. Real taalkeuze-heading blocks (primary path).
    for h in _in_family_hits("taalkeuze", hits):
        if len(items) >= MAX or _is_heading_only(h):
            continue
        _add(h, outside_section=False)

    # 2. Explicit choice-phrase fallback from anywhere (precise, multi-word gate).
    if len(items) < MAX:
        for h in _sort_content_first([h for h in hits if not _is_heading_only(h)]):
            if len(items) >= MAX:
                break
            if _strict_phrases(h.block["text"]):
                _add(h, outside_section=True)

    if not items:
        items = _heading_only_marker(
            "taalkeuze", hits, "Taalkeuze-heading zonder inhoud aangetroffen", ctx,
        )

    if cr.status in ("missing", "partial"):
        has_choice = any(i.criterion_subtype in ("choice", "choice_and_consequence")
                         and i.signal_class == "positive" for i in items)
        has_cons = any(i.criterion_subtype in ("consequence", "choice_and_consequence")
                       and i.signal_class == "positive" for i in items)
        if not has_choice:
            missing_sigs.append("Geen expliciete taalkeuze vermeld")
        if not has_cons:
            missing_sigs.append(
                "Gevolgen van taalkeuze niet beschreven (bijv. bereik, vertaalkosten, begrijpelijkheid)"
            )

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
    """Heading-only security evidence.

    In-family = security / beveiliging / privacy / AVG / OWASP / encryptie
    sections. Concrete mechanisms in those sections rank first; generic security
    prose in those sections fills remaining slots as weak. A `wachtwoord` in a
    use-case table or an `avg` in an NFR table is NOT admitted — there is no
    foreign rescue and no neutral promotion.
    """
    MAX = _MAX_ITEMS["security"]
    items: list[EvidenceItem] = []
    missing_sigs: list[str] = []
    seen: set[str] = set()
    _ALL = _SEC_CONCRETE | _SEC_GENERIC

    content = [h for h in _in_family_hits("security", hits) if not _is_heading_only(h)]

    def _concrete(h: RetrievalHit) -> list[str]:
        return _matched_tokens(h.block["text"], _SEC_CONCRETE, 4)

    def _add(h: RetrievalHit) -> None:
        bid = h.block["block_id"]
        if bid in seen or len(items) >= MAX:
            return
        seen.add(bid)
        terms = _concrete(h)
        if terms:
            subtype, sc = "concrete", ("weak" if cr.status == "partial" else "positive")
            reason = f"Concrete beveiligingsmechanisme(n): {', '.join(terms)}"
            sigs = terms
        else:
            subtype, sc = "generic", "weak"
            gterms = _matched_tokens(h.block["text"], _SEC_GENERIC, 3)
            reason = f"Aanvullende beveiligingscontext: {', '.join(gterms)}"
            sigs = gterms
        items.append(_make_item(
            h, reason, sc, ctx=ctx, focused_terms=_ALL, focused_boundary=True,
            matched_signals=sigs,
            criterion_subtype=subtype,
            classification_source="block_text" if sigs else "",
        ))

    concrete_first = [h for h in content if _concrete(h)]
    generic_rest = [h for h in content if not _concrete(h)]
    for h in concrete_first + generic_rest:
        if len(items) >= MAX:
            break
        _add(h)

    if not items:
        items = _heading_only_marker(
            "security", hits, "Security-heading gevonden maar geen inhoud herkend", ctx,
        )

    if cr.status in ("missing", "partial"):
        if cr.status == "missing":
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
# Coverage tiling — maximise unique section text shown to Qwen
# ---------------------------------------------------------------------------

# Criteria that use the "everything under a header" section strategy. taalkeuze
# is excluded: it has no dedicated section here and is gated on cross-section
# choice phrases, so header-based tiling does not apply.
_COVERAGE_KEYS: Final[frozenset[str]] = frozenset(
    {"beperking", "stakeholders", "requirements", "security"}
)

# Per-paragraph-excerpt cap and the total paragraph-text budget per criterion.
# "Ruim maar begrensd": enough that Qwen sees most of the section, capped so the
# combined prompt stays manageable for a local model.
_COVERAGE_ITEM_CAP: Final[int] = 1000
_COVERAGE_TOTAL_BUDGET: Final[int] = 3600


def _resignal_requirements(it: EvidenceItem) -> None:
    """Re-describe a requirements prose item from its (tiled) excerpt.

    Coverage tiling can pull a MoSCoW label or req-ID from a merged same-section
    neighbour into the excerpt while the anchor block itself carried none. This
    re-reads the FINAL excerpt so the metadata matches the text actually shown:
    it sets matched_signals / matched_row_ids and, when a real requirement signal
    is present, lifts a weak item to positive and recomputes its strength.

    This labels already-admitted, in-section text only — admission stays
    strictly heading-based; nothing is pulled in on a keyword.
    """
    sigs, ids = _req_signals_from_text(it.excerpt)
    if not sigs:
        return
    it.matched_signals = sigs
    if ids and not it.matched_row_ids:
        it.matched_row_ids = ids
    if it.signal_class == "weak":
        it.signal_class = "positive"
        it.selection_reason = "Requirements in tekst (prioritering/IDs herkend)"
    it.evidence_strength = _evidence_strength(
        it.signal_class, it.matched_signals, it.matched_row_count
    )


def _apply_coverage(
    items: list[EvidenceItem],
    ctx: _DocCtx,
    criterion_key: str,
    item_cap: int = _COVERAGE_ITEM_CAP,
    total_budget: int = _COVERAGE_TOTAL_BUDGET,
) -> list[EvidenceItem]:
    """Re-shape paragraph/bullet excerpts so the packet TILES its section.

    Each paragraph/bullet item's excerpt is rebuilt to cover uncovered
    same-section text (via ctx.tile_window), tracking covered blocks across the
    whole packet so no passage repeats. Items whose text is already fully covered
    are dropped (they would only duplicate). Tables and headings keep their own
    excerpts unchanged. Stops growing paragraph text once total_budget is spent,
    but still keeps non-paragraph items.

    After tiling, requirements prose items are re-described from their final
    excerpt (see _resignal_requirements) so a MoSCoW label/req-ID that ended up
    in the merged text is reflected in the metadata. This re-labels already
    selected in-section text only — it never changes which blocks are admitted.

    Net effect: the union of excerpts spans as much unique section text as the
    budget allows, instead of several items repeating the same merged window.
    """
    covered: set[int] = set()
    spent = 0
    out: list[EvidenceItem] = []
    for it in items:
        if it.block_type not in ("paragraph", "bullet"):
            out.append(it)
            continue
        if spent >= total_budget:
            continue  # paragraph budget exhausted — drop further prose
        text, newly = ctx.tile_window(
            it.block_id, covered, item_cap, total_budget - spent
        )
        if not text:
            continue  # anchor already covered → redundant item
        covered |= newly
        spent += len(text)
        it.excerpt = text
        # The focused snippet may now duplicate the (larger) excerpt; clear it
        # only when it became an exact prefix-equal of the excerpt.
        if it.focused_excerpt and it.focused_excerpt.strip() == it.excerpt.strip():
            it.focused_excerpt = ""
        if criterion_key == "requirements":
            _resignal_requirements(it)
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_evidence_packets(
    artifacts: CapsPipelineArtifacts,
) -> dict[str, EvidencePacket]:
    """Build one EvidencePacket per criterion from CapsPipelineArtifacts.

    CAPS is the source of truth for all judgements. This function only SELECTS
    and SHAPES evidence — it never re-judges or overrides any CAPS decision.

    Selection is strictly heading-only (section_family.py): only blocks whose
    heading_path belongs to the criterion's section family are admitted. No
    neutral promotion, no foreign rescue, no count-driven behaviour. When CAPS
    rates a criterion `missing`, any positive items are coerced to weak so the
    handoff never presents positive evidence for a criterion CAPS calls missing.
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
        pkt = builder(spec, hits, cr, ctx)

        # Coverage tiling: rebuild paragraph excerpts so the packet spans as much
        # unique section text as the budget allows (no repeated windows). Applied
        # to the header-based criteria only; taalkeuze keeps its phrase-fallback
        # excerpts.
        if key in _COVERAGE_KEYS:
            pkt.evidence_items = _apply_coverage(pkt.evidence_items, ctx, key)

        # Safety net: a 'missing' verdict must never carry positive evidence.
        if cr.status == "missing":
            for it in pkt.evidence_items:
                if it.signal_class == "positive":
                    it.signal_class = "weak"
                    it.evidence_strength = "thin"

        # A missing/partial criterion always carries at least one coverage gap,
        # so the downstream stage never sees a silent "incomplete-but-no-reason".
        if cr.status in ("missing", "partial") and not pkt.missing_signals:
            pkt.missing_signals.append(
                f"{spec.label}: sectie aanwezig maar mogelijk onvolledig"
            )

        packets[key] = pkt

    return packets
