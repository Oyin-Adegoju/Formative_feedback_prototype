#!/usr/bin/env python3
"""run_full_pipeline_with_feedback.py — end-to-end pipeline test script.

Tests the complete pipeline for one anonymized document:
    anonymized JSON → CAPS → feedback_builder → Qwen/Ollama → feedback JSON

────────────────────────────────────────────────────────────────────────────
Usage — local, CAPS only (no LLM required):
    python scripts/run_full_pipeline_with_feedback.py \\
        --input data/anonymised/2e138dc4_anonymized.json \\
        --input-type anonymized-json \\
        --no-llm

Usage — server with Ollama / Qwen2.5-14B:
    LLM_BASE_URL=http://localhost:11434/v1 \\
    LLM_MODEL=qwen2.5:14b \\
    python scripts/run_full_pipeline_with_feedback.py \\
        --input data/anonymised/2e138dc4_anonymized.json \\
        --input-type anonymized-json

Debug mode (full stack traces on failure):
    python scripts/run_full_pipeline_with_feedback.py \\
        --input data/anonymised/2e138dc4_anonymized.json \\
        --input-type anonymized-json \\
        --debug

────────────────────────────────────────────────────────────────────────────
Server quick-start (Debian, HS Leiden):
    ssh s1140710@145.97.16.50
    cd ~/bpdee2-formatieve-feedback-assistent
    git pull origin develop
    ollama list                              # confirm model is available
    curl http://localhost:11434              # confirm Ollama is running
    LLM_BASE_URL=http://localhost:11434/v1 \\
    LLM_MODEL=qwen2.5:14b \\
    python scripts/run_full_pipeline_with_feedback.py \\
        --input data/anonymised/2e138dc4_anonymized.json \\
        --input-type anonymized-json

────────────────────────────────────────────────────────────────────────────
Output is written to:
    data/full_pipeline_runs/<doc_id>_<YYYYMMDD_HHMMSS>/
        input_copy.json          exact copy of the input file
        anonymized_report.json   normalized report passed to CAPS
        caps_result_summary.json CAPS verdict (same shape as caps_results.json)
        feedback_result.json     validated LLM feedback, or skipped marker if --no-llm
        run_summary.json         machine-readable run metadata + check results

Exit codes:
    0 — all steps completed and all integration checks passed
    1 — fatal error OR LLM call / validation failed OR one or more integration checks failed

Note: in full LLM mode this script does NOT accept fallback feedback.
If the LLM call, timeout, or validation fails the script stops with exit code 1.
Use --no-llm to run CAPS only without requiring a live Ollama instance.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import traceback
from typing import Final

# Ensure UTF-8 output on Windows / server terminals that default to other encodings.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Allow running from the project root without installing the package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.caps.caps import run_caps
from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import CapsRunResult, ParseReportDict
from src.feedback.feedback_builder import (
    _PROMPT_PATH,
    _PROMPT_VERSION,
    _assemble_prompt,
)
from src.feedback.feedback_validator import FeedbackValidationError, validate
from src.feedback.output_schema import DISCLAIMER, FeedbackResult
from src.llm import llm_client
from src.llm.llm_client import LlmCallError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIR: Final[pathlib.Path] = _PROJECT_ROOT / "data" / "full_pipeline_runs"

_EXPECTED_CRITERIA: Final[frozenset[str]] = frozenset(CRITERIA_KEYS)

_CAPS_SUMMARY_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset({
    "file", "doc_id", "source_name", "overall_stoplight", "hidden_score",
    "manual_review_required", "blockers_triggered", "manual_review_flags", "criteria",
})

_CRITERION_REQUIRED_FIELDS: Final[frozenset[str]] = frozenset({
    "status", "stoplight", "count", "manual_review", "notes",
})

_STATUS_LABELS: Final[frozenset[str]] = frozenset({
    "missing", "partial", "sufficient", "strong",
})

_SCORE_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"\b\d+\s*/\s*1[0-5]\b"),
    re.compile(r"\b(hidden[_\s])?score\s*[:=]\s*\d+", re.IGNORECASE),
    re.compile(r"\bcijfer\s*[:=]\s*\d+", re.IGNORECASE),
    re.compile(r"\b\d+\s*punt(?:en)?\b", re.IGNORECASE),
]

_STOPLIGHT_SYMBOL: Final[dict[str, str]] = {
    "green": "[GREEN]", "yellow": "[YELLOW]", "red": "[RED]",
}

_FALLBACK_MARKER: Final[str] = (
    "automatische feedbackgeneratie is tijdelijk niet beschikbaar"
)

# (check_name, passed, detail_message)
CheckResult = tuple[str, bool, str]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the complete CAPS + feedback pipeline on one anonymized JSON file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="Path to the input file (e.g. data/anonymised/2e138dc4_anonymized.json).",
    )
    parser.add_argument(
        "--input-type",
        choices=["anonymized-json"],
        default="anonymized-json",
        metavar="TYPE",
        help="Input type. Only 'anonymized-json' is supported in this version (default).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        metavar="DIR",
        help="Base output directory (default: data/full_pipeline_runs).",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        metavar="N",
        help="Max retrieval candidates per criterion (default: 20).",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip feedback generation. Run CAPS only, no LLM call.",
    )
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=800,
        metavar="SECONDS",
        help=(
            "LLM request timeout in seconds (default: 800). "
            "Increase further if Qwen2.5-14B is slow to load on the server."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print full stack traces on unexpected failures.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Compatibility shim — identical logic to run_caps_on_anonymized.py
# ---------------------------------------------------------------------------


def _normalize_report(raw: dict) -> ParseReportDict:
    """Inject page_count when absent — the only mismatch between anonymized JSON and the CAPS contract.

    Modifies the dict in-place and returns the same object.
    All other extra fields (quality_label, mapping_count, mapping, per-block doc_id)
    are silently ignored by CAPS and do not need to be removed.
    """
    if "page_count" not in raw or raw["page_count"] is None:
        blocks = raw.get("blocks") or []
        raw["page_count"] = max((b["page_no"] for b in blocks), default=0)
    return raw  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Serialization — same shape as caps_results.json
# ---------------------------------------------------------------------------


def _caps_result_to_dict(filename: str, result: CapsRunResult) -> dict:
    """Serialize CapsRunResult to the same dict shape as caps_results.json.

    Replicates _result_to_dict() from run_caps_on_anonymized.py exactly so that
    caps_result_summary.json written here is structurally identical to caps_results.json.
    """
    criteria: dict[str, dict] = {}
    for key, cr in result.scorecard.results.items():
        criteria[key] = {
            "status": cr.status,
            "stoplight": cr.stoplight,
            "count": cr.count,
            "manual_review": cr.manual_review,
            "notes": cr.notes,
        }
    return {
        "file": filename,
        "doc_id": result.doc_id,
        "source_name": result.source_name,
        "overall_stoplight": result.overall_stoplight,
        "hidden_score": result.scorecard.hidden_score,
        "manual_review_required": result.manual_review_required,
        "blockers_triggered": result.blockers_triggered,
        "manual_review_flags": result.manual_review_flags,
        "criteria": criteria,
    }


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------


def _section(title: str) -> None:
    print(f"\n{'─' * 64}")
    print(f"  {title}")
    print(f"{'─' * 64}")


def _print_caps_result(result: CapsRunResult) -> None:
    sym = _STOPLIGHT_SYMBOL.get(result.overall_stoplight, "?")
    print(f"  doc_id              : {result.doc_id}")
    print(f"  source_name         : {result.source_name}")
    print(f"  overall_stoplight   : {sym} {result.overall_stoplight}")
    print(f"  hidden_score        : {result.scorecard.hidden_score} / 15")
    print(f"  manual_review       : {result.manual_review_required}")

    blockers_str = ", ".join(result.blockers_triggered) if result.blockers_triggered else "none"
    flags_str = ", ".join(result.manual_review_flags) if result.manual_review_flags else "none"
    print(f"  blockers_triggered  : {blockers_str}")
    print(f"  manual_review_flags : {flags_str}")
    print()

    for key, cr in result.scorecard.results.items():
        sym_c = _STOPLIGHT_SYMBOL.get(cr.stoplight, "?")
        count_str = f"  count={cr.count}" if cr.count is not None else ""
        print(
            f"  {sym_c} {key:<14} status={cr.status:<12}"
            f"{count_str}  manual_review={cr.manual_review}"
        )
        for note in cr.notes[:2]:
            short = note[:100] + ("…" if len(note) > 100 else "")
            print(f"               -> {short}")

    print()
    print("  Evidence block IDs per criterion:")
    for key, cr in result.scorecard.results.items():
        if cr.evidence:
            ids = ", ".join(ref.block_id for ref in cr.evidence[:4])
            tail = f" (+{len(cr.evidence) - 4} more)" if len(cr.evidence) > 4 else ""
            print(f"    {key:<14} : {ids}{tail}")
        else:
            print(f"    {key:<14} : (geen evidence)")


def _print_feedback_result(fb: FeedbackResult) -> None:
    sym = _STOPLIGHT_SYMBOL.get(fb["stoplight"], "?")
    print(f"  document_id         : {fb['document_id']}")
    print(f"  stoplight           : {sym} {fb['stoplight']}")
    print()

    samenvatting = fb["student_samenvatting"]
    print("  student_samenvatting (first 200 chars):")
    print(f"    {samenvatting[:200]}{'…' if len(samenvatting) > 200 else ''}")
    print()

    ff = fb["feed_forward"]
    print(f"  feed_forward ({len(ff)} items):")
    for item in ff[:4]:
        print(f"    - {str(item)[:100]}")
    print()

    entries = fb["feedback"]
    print(f"  feedback entries ({len(entries)}):")
    for entry in entries:
        refs = entry.get("evidence_ref") or []
        crit = entry.get("criterium", "?")
        print(f"    [{crit:<14}]  evidence_ref={refs}")
    print()

    disclaimer = fb["disclaimer"]
    print(f"  disclaimer: {disclaimer[:80]}{'…' if len(disclaimer) > 80 else ''}")


# ---------------------------------------------------------------------------
# Integration checks
# ---------------------------------------------------------------------------


def _collect_known_block_ids(caps_result: CapsRunResult) -> frozenset[str]:
    ids: set[str] = set()
    for cr in caps_result.scorecard.results.values():
        for ref in cr.evidence:
            ids.add(ref.block_id)
    return frozenset(ids)


def _feedback_text_combined(fb: FeedbackResult) -> str:
    """Concatenate all LLM-written text fields for leak scanning.

    Mirrors feedback_validator._llm_text_fields() exactly.
    Excludes evidence_ref — block IDs legitimately contain alphanumerics.
    """
    parts = [
        fb.get("student_samenvatting") or "",
        fb.get("docent_toelichting") or "",
        fb.get("feed_up") or "",
        fb.get("taalgebruik") or "",
        " ".join(str(x) for x in (fb.get("feed_forward") or [])),
        " ".join(
            entry.get("observatie") or ""
            for entry in (fb.get("feedback") or [])
            if isinstance(entry, dict)
        ),
    ]
    return " ".join(parts)


def _check_caps_summary(summary: dict) -> list[CheckResult]:
    results: list[CheckResult] = []

    missing_top = _CAPS_SUMMARY_TOP_LEVEL_KEYS - set(summary.keys())
    results.append((
        "caps_summary_top_level_keys",
        not missing_top,
        "all present" if not missing_top else f"missing: {sorted(missing_top)}",
    ))

    actual_criteria = set(summary.get("criteria", {}).keys())
    criteria_ok = actual_criteria == _EXPECTED_CRITERIA
    results.append((
        "caps_criteria_keys",
        criteria_ok,
        "all 5 present" if criteria_ok
        else f"expected {sorted(_EXPECTED_CRITERIA)}, got {sorted(actual_criteria)}",
    ))

    bad_fields: list[str] = []
    for crit_key, crit_val in summary.get("criteria", {}).items():
        missing = _CRITERION_REQUIRED_FIELDS - set(crit_val.keys())
        if missing:
            bad_fields.append(f"{crit_key}: missing {sorted(missing)}")
    results.append((
        "caps_criterion_internal_fields",
        not bad_fields,
        "all fields present" if not bad_fields else "; ".join(bad_fields),
    ))

    return results


def _check_feedback(fb: FeedbackResult, caps_result: CapsRunResult) -> list[CheckResult]:
    results: list[CheckResult] = []
    known_ids = _collect_known_block_ids(caps_result)

    doc_id_ok = fb["document_id"] == caps_result.doc_id
    results.append((
        "feedback_document_id",
        doc_id_ok,
        "match" if doc_id_ok
        else f"feedback='{fb['document_id']}' caps='{caps_result.doc_id}'",
    ))

    stoplight_ok = fb["stoplight"] == caps_result.overall_stoplight
    results.append((
        "feedback_stoplight_matches_caps",
        stoplight_ok,
        "match" if stoplight_ok
        else f"feedback='{fb['stoplight']}' caps='{caps_result.overall_stoplight}'",
    ))

    disclaimer_ok = fb["disclaimer"] == DISCLAIMER
    results.append((
        "feedback_disclaimer_correct",
        disclaimer_ok,
        "correct" if disclaimer_ok else f"got: {fb['disclaimer'][:60]}",
    ))

    bad_criteria = [
        str(entry.get("criterium"))
        for entry in fb.get("feedback", [])
        if isinstance(entry, dict)
        and entry.get("criterium") not in _EXPECTED_CRITERIA
    ]
    results.append((
        "feedback_criterium_keys_valid",
        not bad_criteria,
        "all valid" if not bad_criteria else f"unknown: {bad_criteria}",
    ))

    bad_refs: list[str] = []
    for entry in fb.get("feedback", []):
        if not isinstance(entry, dict):
            continue
        for ref in entry.get("evidence_ref") or []:
            if ref not in known_ids:
                bad_refs.append(ref)
    results.append((
        "feedback_evidence_ref_ids_valid",
        not bad_refs,
        "all valid" if not bad_refs else f"unknown block IDs: {bad_refs[:5]}",
    ))

    try:
        json.dumps(fb)
        results.append(("feedback_json_serializable", True, "ok"))
    except (TypeError, ValueError) as exc:
        results.append(("feedback_json_serializable", False, str(exc)))

    return results


def _check_leaks(fb: FeedbackResult) -> list[CheckResult]:
    results: list[CheckResult] = []
    combined = _feedback_text_combined(fb)
    fb_json_str = json.dumps(fb)

    hidden_score_leak = "hidden_score" in fb_json_str
    results.append((
        "leak_no_hidden_score_in_feedback_json",
        not hidden_score_leak,
        "clean" if not hidden_score_leak else "FOUND 'hidden_score' in feedback JSON",
    ))

    score_leaks = [p.pattern for p in _SCORE_PATTERNS if p.search(combined)]
    results.append((
        "leak_no_numeric_score_patterns",
        not score_leaks,
        "clean" if not score_leaks else f"patterns matched: {score_leaks}",
    ))

    label_leaks = sorted(
        label for label in _STATUS_LABELS
        if re.search(rf"\b{label}\b", combined, re.IGNORECASE)
    )
    results.append((
        "leak_no_internal_status_labels_in_text_fields",
        not label_leaks,
        "clean" if not label_leaks else f"labels found: {label_leaks}",
    ))

    return results


def _is_fallback(fb: FeedbackResult) -> bool:
    return _FALLBACK_MARKER.lower() in (fb.get("student_samenvatting") or "").lower()


# ---------------------------------------------------------------------------
# Strict LLM generation (no fallback accepted)
# ---------------------------------------------------------------------------


def _generate_feedback_strict(
    caps_result: CapsRunResult,
    llm_base_url: str,
    llm_model: str,
    timeout: int = 800,
    max_tokens: int = 2048,
) -> FeedbackResult:
    """Call the LLM pipeline directly with step-by-step progress logging.

    Does NOT use generate_feedback() — that swallows errors and returns fallback output.
    This function is strict: any failure raises RuntimeError so main() can exit with code 1.

    Sequence: load template → assemble prompt → llm_client.complete() → validate()
    """
    # ── Load template ─────────────────────────────────────────────────────────
    print(f"  prompt version : {_PROMPT_VERSION}")
    try:
        rel_path = _PROMPT_PATH.relative_to(_PROJECT_ROOT)
    except ValueError:
        rel_path = _PROMPT_PATH
    print(f"  template path  : {rel_path}")
    try:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Could not load prompt template: {exc}") from exc
    print("  template       : loaded")

    # ── Assemble prompt ───────────────────────────────────────────────────────
    prompt = _assemble_prompt(caps_result, template)
    print("  prompt         : assembled")
    print(f"  prompt chars   : {len(prompt)}")
    print(f"  model          : {llm_model}")
    print(f"  base URL       : {llm_base_url}")
    print(f"  timeout        : {timeout} s")
    print(f"  max_tokens     : {max_tokens}")
    print()

    # ── LLM call ──────────────────────────────────────────────────────────────
    print("[LLM] Starting Ollama call...")
    print("[LLM] This may take several minutes on the server.")
    t_start = datetime.datetime.now()
    try:
        raw_json = llm_client.complete(
            prompt, model=llm_model, max_tokens=max_tokens, timeout=timeout,
        )
    except LlmCallError as exc:
        raise RuntimeError(f"LLM call failed: {exc.reason}") from exc
    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    print(f"[LLM] Ollama call completed in {elapsed:.1f} seconds.")
    print("[LLM] Raw response received.")
    print(f"[LLM] Raw response chars: {len(raw_json)}")
    print()

    # ── Validation ────────────────────────────────────────────────────────────
    print("[VALIDATION] Starting feedback validation...")
    try:
        result = validate(raw_json, caps_result)
    except FeedbackValidationError as exc:
        raise RuntimeError(f"Feedback validation failed: {exc.reason}") from exc
    print("[VALIDATION] Feedback JSON validated successfully.")
    print("[OK] Feedback received and validated.")

    return result


# ---------------------------------------------------------------------------
# File writing helper
# ---------------------------------------------------------------------------


def _write_json(path: pathlib.Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    size_kb = path.stat().st_size / 1024
    print(f"    saved: {path.name:<34}  ({size_kb:.1f} KB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    args = _parse_args()
    debug: bool = args.debug

    llm_base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_model = os.environ.get("LLM_MODEL", "Qwen2.5-14B-Instruct")

    fatal_errors: list[str] = []
    warnings: list[str] = []

    print("=" * 64)
    print("  Full Pipeline Test: CAPS + Feedback")
    print("=" * 64)
    print(f"  input        : {args.input}")
    print(f"  input-type   : {args.input_type}")
    print(f"  LLM base URL : {llm_base_url}")
    print(f"  LLM model    : {llm_model}")
    if args.no_llm:
        print("  mode         : CAPS only (--no-llm, feedback step skipped)")
    else:
        print("  mode         : full pipeline (CAPS + feedback)")
    print()

    # ── Step 1: Load input ────────────────────────────────────────────────────
    _section("Step 1 — Load input")

    input_path = pathlib.Path(args.input)
    if not input_path.exists():
        print(f"[FATAL] Input file not found: {input_path}")
        print("        Check the --input path and try again.")
        return 1

    try:
        raw_text = input_path.read_text(encoding="utf-8")
        raw: dict = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"[FATAL] JSON decode error in {input_path.name}: {exc}")
        if debug:
            traceback.print_exc()
        return 1

    print(f"  file         : {input_path.name}")
    print(f"  doc_id       : {raw.get('doc_id', '?')}")
    print(f"  source_name  : {raw.get('source_name', '?')}")
    print(f"  block_count  : {raw.get('block_count', '?')}")
    print(f"  quality_label: {raw.get('quality_label', '—')}")

    # ── Step 2: Normalize report ──────────────────────────────────────────────
    _section("Step 2 — Normalize report")

    had_page_count = "page_count" in raw and raw["page_count"] is not None
    report: ParseReportDict = _normalize_report(raw)

    if had_page_count:
        print(f"  page_count   : {report['page_count']}  (present in source)")
    else:
        print(f"  page_count   : {report['page_count']}  (injected from max block page_no)")
    print(f"  blocks       : {len(report.get('blocks', []))}")
    print("  [OK] Report ready for CAPS")

    # ── Step 3: CAPS evaluation ────────────────────────────────────────────────
    _section("Step 3 — CAPS evaluation")

    try:
        caps_result = run_caps(
            report,
            input_source="anonymized",
            max_candidates=args.max_candidates,
        )
    except Exception as exc:
        msg = f"CAPS raised: {exc}"
        print(f"[FATAL] {msg}")
        if debug:
            traceback.print_exc()
        fatal_errors.append(msg)
        return 1

    _print_caps_result(caps_result)

    # Create the output directory here — before Step 4 — so we can write a
    # partial run_summary.json even when the LLM call fails.
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = pathlib.Path(args.output_dir) / f"{caps_result.doc_id}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 4: Feedback generation ───────────────────────────────────────────
    feedback_result: FeedbackResult | None = None

    if args.no_llm:
        _section("Step 4 — Feedback (skipped: --no-llm)")
        print("  Feedback generation skipped.")
    else:
        _section("Step 4 — Feedback generation")
        try:
            feedback_result = _generate_feedback_strict(
                caps_result, llm_base_url, llm_model,
                timeout=args.llm_timeout,
            )
        except RuntimeError as exc:
            msg = str(exc)
            print(f"\n[FATAL] {msg}")
            if debug:
                traceback.print_exc()
            fatal_errors.append(msg)
            _write_json(run_dir / "run_summary.json", {
                "input_path": str(input_path.resolve()),
                "input_type": args.input_type,
                "doc_id": caps_result.doc_id,
                "source_name": caps_result.source_name,
                "output_directory": str(run_dir),
                "stoplight": caps_result.overall_stoplight,
                "feedback_skipped": False,
                "feedback_is_fallback": None,
                "llm_base_url": llm_base_url,
                "llm_model": llm_model,
                "integration_checks": {},
                "integration_checks_all_passed": False,
                "errors": [msg],
                "warnings": warnings,
            })
            return 1
        print()
        _print_feedback_result(feedback_result)

    # ── Step 5: Integration checks ─────────────────────────────────────────────
    _section("Step 5 — Integration checks")

    caps_summary = _caps_result_to_dict(input_path.name, caps_result)
    all_checks: list[CheckResult] = []

    all_checks.extend(_check_caps_summary(caps_summary))

    if feedback_result is not None:
        all_checks.extend(_check_feedback(feedback_result, caps_result))
        all_checks.extend(_check_leaks(feedback_result))
    else:
        print("  (feedback checks skipped — no feedback result)")

    print()
    any_fail = False
    for name, passed, detail in all_checks:
        label = "PASS" if passed else "FAIL"
        if not passed:
            any_fail = True
        print(f"  [{label}] {name:<52}  {detail}")

    # ── Step 6: Write outputs ──────────────────────────────────────────────────
    _section("Step 6 — Writing outputs")

    # run_dir was created before Step 4 so error state could be written there.
    print(f"  Output directory: {run_dir}")
    print()

    # input_copy.json — original bytes, exactly as received
    (run_dir / "input_copy.json").write_text(raw_text, encoding="utf-8")
    size_kb = (run_dir / "input_copy.json").stat().st_size / 1024
    print(f"    saved: input_copy.json                    ({size_kb:.1f} KB)  [original, unmodified]")

    # anonymized_report.json — normalized report actually passed to CAPS
    _write_json(run_dir / "anonymized_report.json", dict(report))

    # caps_result_summary.json — same shape as caps_results.json
    _write_json(run_dir / "caps_result_summary.json", caps_summary)

    # feedback_result.json
    if feedback_result is not None:
        _write_json(run_dir / "feedback_result.json", dict(feedback_result))
    else:
        _write_json(run_dir / "feedback_result.json", {
            "skipped": True,
            "reason": "--no-llm flag set",
        })

    # run_summary.json — full machine-readable run metadata
    check_dict = {
        name: {"passed": passed, "detail": detail}
        for name, passed, detail in all_checks
    }
    run_summary = {
        "input_path": str(input_path.resolve()),
        "input_type": args.input_type,
        "doc_id": caps_result.doc_id,
        "source_name": caps_result.source_name,
        "output_directory": str(run_dir),
        "stoplight": caps_result.overall_stoplight,
        "hidden_score": caps_result.scorecard.hidden_score,
        "manual_review_required": caps_result.manual_review_required,
        "blockers_triggered": caps_result.blockers_triggered,
        "manual_review_flags": caps_result.manual_review_flags,
        "feedback_skipped": feedback_result is None,
        "feedback_is_fallback": (
            _is_fallback(feedback_result) if feedback_result is not None else None
        ),
        "llm_base_url": llm_base_url,
        "llm_model": llm_model,
        "integration_checks": check_dict,
        "integration_checks_all_passed": not any_fail,
        "errors": fatal_errors,
        "warnings": warnings,
    }
    _write_json(run_dir / "run_summary.json", run_summary)

    # ── Final summary ────────────────────────────────────────────────────────
    _section("Result")

    sym = _STOPLIGHT_SYMBOL.get(caps_result.overall_stoplight, "?")
    print(f"  doc_id    : {caps_result.doc_id}")
    print(f"  stoplight : {sym} {caps_result.overall_stoplight}")
    print(f"  output    : {run_dir}")
    print()

    if warnings:
        print("  Warnings:")
        for w in warnings:
            print(f"    - {w}")
        print()

    if any_fail:
        print("  [RESULT] ONE OR MORE INTEGRATION CHECKS FAILED  (exit code 1)")
    else:
        print("  [RESULT] ALL INTEGRATION CHECKS PASSED  (exit code 0)")
    print()

    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
