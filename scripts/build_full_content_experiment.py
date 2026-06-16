#!/usr/bin/env python3
"""build_full_content_experiment.py — full-content-per-criterion prompts for a 32B test.

Experiment goal
---------------
Test whether a larger model (Qwen2.5-32B) writes better quality diagnoses when it
receives the FULL relevant document text per criterion, instead of the strict,
budget-trimmed CAPS evidence the 14B pipeline normally gets. Built for the two
highest-potential anonymized documents, but works on any *_anonymized.json.

What "full content per criterion" means here
--------------------------------------------
For each criterion we gather, in document order, the UNTRIMMED text of:
  1. every block whose deepest heading belongs to that criterion's section family
     (section_family.primary_families — the same admission authority CAPS uses), AND
  2. for taalkeuze and security ONLY: any block ELSEWHERE in the document that
     carries a relevant keyword (the packet_builder term sets). These two criteria
     rarely have a dedicated heading in this corpus, so a heading-only scope would
     leave them empty. Such blocks are tagged "[trefwoord-treffer buiten sectie]"
     so the model judges their relevance itself.

No excerpt trimming, no per-criterion budget cap, no tiling — the whole point is
to give the big model everything and let it decide.

Output (per input document) under data/full_content_experiment/<doc_id>/:
    full_content.json   structured per-criterion block list (provenance-tagged)
    prompt.txt          ready-to-run prompt (template + assembled content)
A run_manifest.json at the top level lists every doc with char/token estimates.

Usage:
    python scripts/build_full_content_experiment.py            # the two default docs
    python scripts/build_full_content_experiment.py --input PATH [--input PATH ...]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Final

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.caps.criterion_specs import CRITERIA_BY_KEY, CRITERIA_KEYS
from src.feedback.packet_builder import (
    _SEC_CONCRETE,
    _SEC_GENERIC,
    _TAAL_CHOICE,
    _TAAL_CHOICE_STRICT,
    _has_token,
)
from src.feedback.section_family import primary_families

_PROJECT_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
_OUT_DIR: Final[pathlib.Path] = _PROJECT_ROOT / "data" / "full_content_experiment"
_PROMPT_PATH: Final[pathlib.Path] = (
    _PROJECT_ROOT / "prompts" / "qwen_quality" / "quality_full_content_v1.txt"
)

_DEFAULT_INPUTS: Final[tuple[str, ...]] = (
    "data/anonymized/Goed/51d405f7_anonymized.json",
    "data/anonymized/Voldoende/2f4d9d91_anonymized.json",
)

# Block types that carry no rubric evidence — mirrors retrieval._EXCLUDED_TYPES.
_EXCLUDED_TYPES: Final[frozenset[str]] = frozenset({"front_matter", "noise", "template"})

# Keyword-supplement term sets for the two heading-less criteria. Generous by
# design ("ruim gescoped"): the 32B model is trusted to discard stray hits.
#
# taalkeuze admits on CHOICE words only (strict phrases + meertalig/nederlands/
# engels/taalkeuze...). Consequence words (_TAAL_CONSEQUENCE: bereik,
# internationaal, begrijpelijk...) are deliberately NOT admission signals: they
# are too generic and would drag whole unrelated tables (e.g. a stakeholder
# table mentioning "bereik") into taalkeuze. A real consequence discussion
# almost always sits in the same block as the choice itself ("als de website
# alleen in het Nederlands is, dan..."), so choice-word admission still captures it.
_KEYWORD_TERMS: Final[dict[str, frozenset[str]]] = {
    "taalkeuze": _TAAL_CHOICE_STRICT | _TAAL_CHOICE,
    "security": _SEC_CONCRETE | _SEC_GENERIC,
}


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
    """Full, untrimmed display text for a block: table as header + rows, else text."""
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


def _collect_for_criterion(blocks: list[dict], key: str) -> list[dict]:
    """Provenance-tagged blocks for one criterion, in document order.

    source = "section" (heading family) or "keyword" (out-of-section keyword hit,
    taalkeuze/security only). De-duplicated by block_id; section wins over keyword.
    """
    fam_ids = {
        b["block_id"] for b in blocks if key in primary_families(b["heading_path"])
    }
    terms = _KEYWORD_TERMS.get(key)
    out: list[dict] = []
    for b in blocks:
        bid = b["block_id"]
        # Skip structurally-empty blocks (front matter, table-of-contents lines,
        # parser noise/templates) — they carry no rubric content and are excluded
        # everywhere else in the pipeline. A TOC line "SECURITY ......" or a page
        # header must never count as evidence.
        if b.get("is_front_matter") or b["block_type"] in _EXCLUDED_TYPES:
            continue
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


def _format_content(per_criterion: dict[str, list[dict]]) -> str:
    """Render the per-criterion block lists into the prompt's <<FULL_CONTENT>> body."""
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


def _process(path: pathlib.Path, template: str) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    blocks = raw.get("blocks") or []
    doc_id = raw.get("doc_id") or path.stem
    source_name = raw.get("source_name", "")

    per_criterion = {key: _collect_for_criterion(blocks, key) for key in CRITERIA_KEYS}
    content_body = _format_content(per_criterion)
    prompt = template.replace("<<DOC_ID>>", doc_id).replace("<<FULL_CONTENT>>", content_body)

    doc_dir = _OUT_DIR / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "full_content.json").write_text(
        json.dumps(
            {"document_id": doc_id, "source_name": source_name, "criteria": per_criterion},
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (doc_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    chars = len(prompt)
    summary = {
        "doc_id": doc_id,
        "source_name": source_name,
        "quality_label": raw.get("quality_label"),
        "prompt_chars": chars,
        "prompt_tokens_est": round(chars / 4),
        "blocks_per_criterion": {k: len(v) for k, v in per_criterion.items()},
        "prompt_path": str((doc_dir / "prompt.txt").resolve()),
    }
    print(f"  {doc_id} ({raw.get('quality_label')}): "
          f"{chars:,} chars (~{summary['prompt_tokens_est']:,} tokens)")
    for k in CRITERIA_KEYS:
        n = len(per_criterion[k])
        kw = sum(1 for it in per_criterion[k] if it["source"] == "keyword")
        print(f"      {k:14} {n:3} blokken" + (f"  ({kw} via trefwoord)" if kw else ""))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", action="append", metavar="PATH",
                        help="Anonymized JSON document(s). Repeatable. "
                             "Defaults to the two highest-potential docs.")
    args = parser.parse_args()

    inputs = args.input or list(_DEFAULT_INPUTS)
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Building full-content prompts → {_OUT_DIR}\n")
    manifest: list[dict] = []
    for rel in inputs:
        path = pathlib.Path(rel)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        if not path.exists():
            print(f"  [SKIP] not found: {path}")
            continue
        manifest.append(_process(path, template))

    (_OUT_DIR / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n[saved] manifest → {_OUT_DIR / 'run_manifest.json'}")
    print("Next: run each prompt.txt through Qwen2.5-32B on Kaggle "
          "(see scripts/kaggle_run_full_content.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
