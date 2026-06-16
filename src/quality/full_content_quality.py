"""full_content_quality.py — experimental quality stage on FULL per-criterion text.

Alternative to quality_builder.py. Instead of feeding the strict, budget-trimmed
CAPS evidence to the model, it feeds the FULL relevant document text per
criterion and asks for the same QualityDiagnostics contract. Built to test
whether a larger model (e.g. Qwen2.5-32B) writes better diagnoses when it is not
starved of context by the heading-only evidence gate.

What "full content per criterion" means
---------------------------------------
For each criterion, in document order, the UNTRIMMED text of:
  1. every block whose deepest heading belongs to that criterion's section family
     (section_family.primary_families — the same admission authority CAPS uses), AND
  2. for taalkeuze and security ONLY: any block elsewhere carrying a relevant
     CHOICE / security keyword (these two criteria rarely have a dedicated heading
     in this corpus). Such blocks are tagged so the model judges relevance itself.

Structurally-empty blocks (front matter, TOC lines, parser noise/templates) are
excluded. No excerpt trimming, no budget cap, no tiling.

Output is a validated QualityDiagnostics — identical shape to quality_builder's,
so the downstream merge + feedback stages consume it unchanged.

Architecture position:
    anonymized report (full blocks) → full_content_quality → [llm_client] → quality_validator
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Final

from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS
from src.feedback.packet_builder import (
    _SEC_CONCRETE,
    _SEC_GENERIC,
    _TAAL_CHOICE,
    _TAAL_CHOICE_STRICT,
    _has_token,
)
from src.feedback.section_family import primary_families
from src.llm import llm_client
from src.llm.llm_client import LlmCallError
from src.quality.output_schema import QualityDiagnostics
from src.quality.quality_validator import QualityValidationError, validate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_VERSION: Final[str] = "quality_full_content_v1"
_PROMPT_PATH: Final[Path] = (
    Path(__file__).parent.parent.parent
    / "prompts" / "qwen_quality" / "quality_full_content_v1.txt"
)

# Full per-criterion text is large; give the model room for all five diagnoses.
_DEFAULT_MAX_TOKENS: Final[int] = 2048
_DEFAULT_MAX_ATTEMPTS: Final[int] = 3

# Block types carrying no rubric evidence — mirrors retrieval._EXCLUDED_TYPES.
_EXCLUDED_TYPES: Final[frozenset[str]] = frozenset({"front_matter", "noise", "template"})

# Keyword-supplement term sets for the two heading-less criteria. taalkeuze admits
# on CHOICE words only — consequence words (bereik, internationaal...) are too
# generic and would drag whole unrelated tables in. A real consequence discussion
# almost always sits in the same block as the choice itself.
_KEYWORD_TERMS: Final[dict[str, frozenset[str]]] = {
    "taalkeuze": _TAAL_CHOICE_STRICT | _TAAL_CHOICE,
    "security": _SEC_CONCRETE | _SEC_GENERIC,
}


class QualityGenerationError(Exception):
    """Raised when the full-content quality stage cannot produce a validated diagnosis.

    Mirrors quality_builder.QualityGenerationError so callers can treat both
    quality stages the same way.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Per-criterion content extraction
# ---------------------------------------------------------------------------


def _searchable(block: dict) -> str:
    """Block text plus table header/cells — for keyword scanning only."""
    parts: list[str] = [block["text"]]
    tm = block.get("table_meta")
    if tm:
        if tm.get("header_row"):
            parts.append(tm["header_row"])
        for row in tm.get("cells") or []:
            parts.extend(c for c in row if c)
    return " ".join(parts)


def _render_block_text(block: dict) -> str:
    """Full, untrimmed display text: table as header + rows, else normalized text."""
    tm = block.get("table_meta")
    if block["block_type"] == "table" and tm and tm.get("cells"):
        lines: list[str] = []
        if tm.get("header_row"):
            lines.append(tm["header_row"].strip())
        for row in tm["cells"]:
            cells = [c.strip() for c in row if c]
            if cells:
                lines.append(" | ".join(cells))
        return "\n".join(lines)
    return " ".join(block["text"].split())


def collect_for_criterion(blocks: list[dict], key: str) -> list[dict]:
    """Provenance-tagged blocks for one criterion, in document order.

    source = "section" (heading family) or "keyword" (out-of-section keyword hit,
    taalkeuze/security only). De-duplicated by block_id; section wins. Excludes
    front matter / noise / template blocks.
    """
    fam_ids = {
        b["block_id"] for b in blocks if key in primary_families(b["heading_path"])
    }
    terms = _KEYWORD_TERMS.get(key)
    out: list[dict] = []
    for b in blocks:
        if b.get("is_front_matter") or b["block_type"] in _EXCLUDED_TYPES:
            continue
        bid = b["block_id"]
        if bid in fam_ids:
            source = "section"
        elif terms and any(_has_token(_searchable(b), t) for t in terms):
            source = "keyword"
        else:
            continue
        rendered = _render_block_text(b)
        if not rendered.strip():
            continue
        out.append({
            "block_id": bid,
            "page_no": b["page_no"],
            "block_type": b["block_type"],
            "heading_path": list(b["heading_path"]),
            "source": source,
            "text": rendered,
        })
    return out


def extract_full_content(report: dict) -> dict[str, list[dict]]:
    """Per-criterion provenance-tagged content for a full anonymized report."""
    blocks = report.get("blocks") or []
    return {key: collect_for_criterion(blocks, key) for key in CRITERIA_KEYS}


def format_content(per_criterion: dict[str, list[dict]]) -> str:
    """Render per-criterion block lists into the prompt's <<FULL_CONTENT>> body."""
    sections: list[str] = []
    for key in CRITERIA_KEYS:
        label = CRITERIA_BY_KEY[key].label
        items = per_criterion[key]
        header = f"════════ CRITERIUM: {key} ({label}) ════════"
        if not items:
            sections.append(f"{header}\n(geen relevante tekst in het document gevonden)")
            continue
        lines = [header]
        for it in items:
            path = " > ".join(it["heading_path"]) or "(geen kop)"
            tag = "  [trefwoord-treffer buiten sectie]" if it["source"] == "keyword" else ""
            lines.append(
                f"\n— blok {it['block_id']} | p{it['page_no']} | {it['block_type']} "
                f"| kop: {path}{tag}\n{it['text']}"
            )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def assemble_prompt(report: dict, template: str) -> str:
    """Fill the full-content prompt template from a full anonymized report dict."""
    doc_id = report.get("doc_id") or report.get("document_id") or ""
    body = format_content(extract_full_content(report))
    return template.replace("<<DOC_ID>>", doc_id).replace("<<FULL_CONTENT>>", body)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_full_content_diagnostics(
    report: dict,
    *,
    timeout: int = 800,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> QualityDiagnostics:
    """Generate quality diagnostics from FULL per-criterion document text.

    Pipeline:
        1. Load the full-content prompt template.
        2. Extract per-criterion full content from the report and assemble the prompt.
        3. Call the LLM via llm_client.complete().
        4. Validate via quality_validator.validate().
        5. Return the validated QualityDiagnostics.

    Re-samples on validation failure up to max_attempts. Never fabricates output.
    Raises QualityGenerationError on template / LLM / repeated validation failure.

    Args:
        report: The full anonymized report dict (must contain "blocks").
        timeout: LLM request timeout in seconds.
        max_tokens: Maximum response tokens.
        max_attempts: Total LLM attempts (1 initial + retries).
    """
    doc_id = report.get("doc_id") or report.get("document_id") or ""
    model_id = os.environ.get("LLM_MODEL", "qwen2.5:14b")

    try:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(
            "doc_id=%s prompt_version=%s | template_missing: %s",
            doc_id, _PROMPT_VERSION, exc,
        )
        raise QualityGenerationError(f"prompt template not found: {exc}") from exc

    base_prompt = assemble_prompt(report, template)
    last_reason = ""

    for attempt in range(1, max_attempts + 1):
        prompt = base_prompt
        if attempt > 1:
            prompt = (
                f"{base_prompt}\n\n"
                "━━━ CORRECTIE ━━━\n"
                f"Je vorige antwoord was ongeldig: {last_reason}\n"
                "Retourneer OPNIEUW exact het volledige JSON-object. Elk criterium "
                "moet criterion_judgement én alle diagnostics-dimensies bevatten.\n"
                "Alleen geldige JSON, geen tekst eromheen."
            )

        try:
            raw_json = llm_client.complete(
                prompt, model=None, max_tokens=max_tokens, timeout=timeout,
            )
        except LlmCallError as exc:
            logger.error(
                "doc_id=%s prompt_version=%s model=%s attempt=%d/%d | llm_call_failed: %s",
                doc_id, _PROMPT_VERSION, model_id, attempt, max_attempts, exc.reason,
            )
            raise QualityGenerationError(f"LLM call failed: {exc.reason}") from exc

        try:
            result = validate(raw_json, document_id=doc_id)
        except QualityValidationError as exc:
            last_reason = exc.reason
            logger.warning(
                "doc_id=%s prompt_version=%s model=%s attempt=%d/%d "
                "| validation_failed: %s | raw_preview=%.200s",
                doc_id, _PROMPT_VERSION, model_id, attempt, max_attempts,
                exc.reason, exc.raw,
            )
            continue

        logger.info(
            "doc_id=%s prompt_version=%s model=%s attempt=%d/%d | validation_ok",
            doc_id, _PROMPT_VERSION, model_id, attempt, max_attempts,
        )
        return result

    raise QualityGenerationError(
        f"validation failed after {max_attempts} attempts: {last_reason}"
    )
