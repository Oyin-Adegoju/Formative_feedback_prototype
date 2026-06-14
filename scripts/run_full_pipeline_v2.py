#!/usr/bin/env python3
"""run_full_pipeline_v2.py — end-to-end staged pipeline (CAPS → Qwen → merge → feedback).

Runs the complete two-LLM-stage pipeline for one anonymized document:

    anonymized JSON
      → CAPS                    → caps_handoff.json
      → Stage A (Qwen quality)  → quality_diagnostics.json
      → merge                   → merged_feedback_input.json
      → Stage B (feedback)      → feedback_result.json

────────────────────────────────────────────────────────────────────────────
Usage — server with Ollama / Qwen2.5-14B:
    LLM_BASE_URL=http://localhost:11434/v1 \\
    LLM_MODEL=qwen2.5:14b \\
    python scripts/run_full_pipeline_v2.py \\
        --input data/anonymized/Goed/2e138dc4_anonymized.json

CAPS + handoff + merge only (no LLM; quality + feedback stages skipped):
    python scripts/run_full_pipeline_v2.py \\
        --input data/anonymized/Goed/2e138dc4_anonymized.json --no-llm

Debug mode (full stack traces on failure):
    python scripts/run_full_pipeline_v2.py \\
        --input data/anonymized/Goed/2e138dc4_anonymized.json --debug

────────────────────────────────────────────────────────────────────────────
Output is written to:
    data/full_pipeline_runs/<doc_id>_<YYYYMMDD_HHMMSS>/
        input_copy.json            exact copy of the input file
        anonymized_report.json     normalized report passed to CAPS
        caps_handoff.json          pre-Qwen CAPS handoff
        quality_diagnostics.json   Qwen quality stage output (skipped if --no-llm)
        merged_feedback_input.json merged CAPS + Qwen contract (sole feedback input)
        feedback_result.json       final feedback writer output (skipped if --no-llm)
        run_summary.json           machine-readable run metadata + check results

Exit codes:
    0 — all steps completed and all integration checks passed
    1 — fatal error OR any LLM call / validation failed OR a check failed

Neither LLM stage has a fallback. A failed quality or feedback call stops the
script with exit code 1. Use --no-llm to run CAPS + handoff + (empty) merge only.
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.caps.caps import run_caps_with_artifacts
from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import ParseReportDict
from src.feedback.feedback_builder import FeedbackGenerationError, generate_feedback
from src.feedback.handoff import build_handoff_from_artifacts, to_dict as handoff_to_dict
from src.feedback.merge_builder import build_merged_feedback_input, collect_known_block_ids
from src.feedback.output_schema import DISCLAIMER, FeedbackResult
from src.quality.quality_builder import QualityGenerationError, generate_quality_diagnostics

_PROJECT_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIR: Final[pathlib.Path] = _PROJECT_ROOT / "data" / "full_pipeline_runs"
_EXPECTED_CRITERIA: Final[frozenset[str]] = frozenset(CRITERIA_KEYS)

_FORBIDDEN_MERGE_KEYS: Final[frozenset[str]] = frozenset({
    "manual_review_required", "manual_review_flags", "hidden_score",
    # Removed pre-Qwen CAPS verdicts must not reappear in the merged contract.
    "overall_stoplight", "blockers_triggered", "caps_status", "caps_stoplight",
})

_SCORE_PATTERNS: Final[list[re.Pattern[str]]] = [
    re.compile(r"\b\d+\s*/\s*1[0-5]\b"),
    re.compile(r"\b(hidden[_\s])?score\s*[:=]\s*\d+", re.IGNORECASE),
    re.compile(r"\bcijfer\s*[:=]\s*\d+", re.IGNORECASE),
    re.compile(r"\b\d+\s*punt(?:en)?\b", re.IGNORECASE),
]
_STATUS_LABELS: Final[frozenset[str]] = frozenset({"missing", "partial", "sufficient", "strong"})

# (check_name, passed, detail_message)
CheckResult = tuple[str, bool, str]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full staged CAPS + Qwen + feedback pipeline on one anonymized JSON file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", required=True, metavar="PATH",
                        help="Path to the anonymized JSON input file.")
    parser.add_argument("--output-dir", default=str(_DEFAULT_OUTPUT_DIR), metavar="DIR",
                        help="Base output directory (default: data/full_pipeline_runs).")
    parser.add_argument("--max-candidates", type=int, default=20, metavar="N",
                        help="Max retrieval candidates per criterion (default: 20).")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip both LLM stages. Run CAPS + handoff + (empty) merge only.")
    parser.add_argument("--llm-timeout", type=int, default=800, metavar="SECONDS",
                        help="LLM request timeout in seconds (default: 800).")
    parser.add_argument("--debug", action="store_true",
                        help="Print full stack traces on unexpected failures.")
    return parser.parse_args()


def _normalize_report(raw: dict) -> ParseReportDict:
    if "page_count" not in raw or raw["page_count"] is None:
        blocks = raw.get("blocks") or []
        raw["page_count"] = max((b["page_no"] for b in blocks), default=0)
    return raw  # type: ignore[return-value]


def _write_json(path: pathlib.Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    size_kb = path.stat().st_size / 1024
    print(f"    saved: {path.name:<32}  ({size_kb:.1f} KB)")


def _section(title: str) -> None:
    print(f"\n{'─' * 64}\n  {title}\n{'─' * 64}")


# ---------------------------------------------------------------------------
# Integration checks
# ---------------------------------------------------------------------------


def _check_merged(merged: dict) -> list[CheckResult]:
    results: list[CheckResult] = []

    top_ok = {
        "document_id", "source_name", "final_stoplight",
        "criteria_requiring_extra_review", "criteria",
    } <= set(merged.keys())
    results.append(("merged_top_level_keys", top_ok,
                    "all present" if top_ok else f"got {sorted(merged.keys())}"))

    crit_ok = set(merged.get("criteria", {}).keys()) == _EXPECTED_CRITERIA
    results.append(("merged_criteria_keys", crit_ok,
                    "all 5 present" if crit_ok
                    else f"got {sorted(merged.get('criteria', {}).keys())}"))

    fs = merged.get("final_stoplight")
    results.append(("merged_final_stoplight_valid", fs in {"green", "yellow", "red"},
                    str(fs)))

    # No legacy CAPS manual_review fields and no hidden_score may leak in.
    blob = json.dumps(merged)
    leaked = sorted(k for k in _FORBIDDEN_MERGE_KEYS if f'"{k}"' in blob)
    results.append(("merged_no_forbidden_keys", not leaked,
                    "clean" if not leaked else f"forbidden: {leaked}"))

    # review list must be consistent with per-criterion manual_review flags.
    derived = sorted(
        k for k, c in merged.get("criteria", {}).items() if c.get("manual_review")
    )
    declared = sorted(merged.get("criteria_requiring_extra_review", []))
    results.append(("merged_review_list_consistent", derived == declared,
                    "consistent" if derived == declared else f"{declared} != {derived}"))

    return results


def _feedback_text(fb: FeedbackResult) -> str:
    parts = [
        fb.get("student_samenvatting") or "",
        fb.get("docent_toelichting") or "",
        fb.get("feed_up") or "",
        fb.get("taalgebruik") or "",
        " ".join(str(x) for x in (fb.get("feed_forward") or [])),
        " ".join(e.get("observatie") or "" for e in (fb.get("feedback") or [])
                 if isinstance(e, dict)),
    ]
    return " ".join(parts)


def _check_feedback(fb: FeedbackResult, merged: dict) -> list[CheckResult]:
    results: list[CheckResult] = []
    known_ids = collect_known_block_ids(merged)

    results.append(("feedback_document_id",
                    fb["document_id"] == merged["document_id"],
                    "match" if fb["document_id"] == merged["document_id"]
                    else f"{fb['document_id']} != {merged['document_id']}"))

    results.append(("feedback_stoplight_matches_merged",
                    fb["stoplight"] == merged["final_stoplight"],
                    "match" if fb["stoplight"] == merged["final_stoplight"]
                    else f"{fb['stoplight']} != {merged['final_stoplight']}"))

    results.append(("feedback_disclaimer_correct",
                    fb["disclaimer"] == DISCLAIMER,
                    "correct" if fb["disclaimer"] == DISCLAIMER else "modified"))

    bad_crit = [str(e.get("criterium")) for e in fb.get("feedback", [])
                if isinstance(e, dict) and e.get("criterium") not in _EXPECTED_CRITERIA]
    results.append(("feedback_criterium_keys_valid", not bad_crit,
                    "all valid" if not bad_crit else f"unknown: {bad_crit}"))

    bad_refs: list[str] = []
    for e in fb.get("feedback", []):
        if isinstance(e, dict):
            for ref in e.get("evidence_ref") or []:
                if ref not in known_ids:
                    bad_refs.append(ref)
    results.append(("feedback_evidence_ref_ids_valid", not bad_refs,
                    "all valid" if not bad_refs else f"unknown: {bad_refs[:5]}"))

    combined = _feedback_text(fb)
    fb_json = json.dumps(fb)
    results.append(("leak_no_hidden_score", "hidden_score" not in fb_json,
                    "clean" if "hidden_score" not in fb_json else "FOUND hidden_score"))
    score_leaks = [p.pattern for p in _SCORE_PATTERNS if p.search(combined)]
    results.append(("leak_no_numeric_score", not score_leaks,
                    "clean" if not score_leaks else f"matched: {score_leaks}"))
    label_leaks = sorted(l for l in _STATUS_LABELS if re.search(rf"\b{l}\b", combined, re.IGNORECASE))
    results.append(("leak_no_status_labels", not label_leaks,
                    "clean" if not label_leaks else f"labels: {label_leaks}"))

    return results


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------


def _print_feedback(fb: FeedbackResult) -> None:
    print(f"  document_id : {fb['document_id']}")
    print(f"  stoplight   : {fb['stoplight']}")
    print(f"  feedback entries ({len(fb['feedback'])}):")
    for entry in fb["feedback"]:
        refs = entry.get("evidence_ref") or []
        print(f"    [{entry.get('criterium', '?'):<14}] evidence_ref={refs}")


def main() -> int:
    args = _parse_args()
    debug: bool = args.debug

    llm_base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
    llm_model = os.environ.get("LLM_MODEL", "Qwen2.5-14B-Instruct")

    print("=" * 64)
    print("  Full Pipeline v2: CAPS → Qwen quality → merge → feedback")
    print("=" * 64)
    print(f"  input        : {args.input}")
    print(f"  LLM base URL : {llm_base_url}")
    print(f"  LLM model    : {llm_model}")
    print(f"  mode         : {'CAPS + merge only (--no-llm)' if args.no_llm else 'full pipeline'}")
    print()

    input_path = pathlib.Path(args.input)
    if not input_path.exists():
        print(f"[FATAL] Input file not found: {input_path}")
        return 1

    try:
        raw_text = input_path.read_text(encoding="utf-8")
        raw: dict = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"[FATAL] JSON decode error in {input_path.name}: {exc}")
        if debug:
            traceback.print_exc()
        return 1

    # ── Step 1: CAPS + handoff ────────────────────────────────────────────────
    _section("Step 1 — CAPS + handoff")
    report = _normalize_report(raw)
    try:
        artifacts = run_caps_with_artifacts(
            report, input_source="anonymized", max_candidates=args.max_candidates,
        )
        caps_result = artifacts.result
        handoff_dict = handoff_to_dict(build_handoff_from_artifacts(artifacts))
    except Exception as exc:
        print(f"[FATAL] CAPS/handoff raised: {exc}")
        if debug:
            traceback.print_exc()
        return 1
    print(f"  doc_id            : {caps_result.doc_id}")
    print(f"  overall_stoplight : {caps_result.overall_stoplight}")
    print(f"  blockers          : {', '.join(caps_result.blockers_triggered) or 'none'}")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = pathlib.Path(args.output_dir) / f"{caps_result.doc_id}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    quality: dict | None = None
    merged: dict | None = None
    feedback_result: FeedbackResult | None = None
    errors: list[str] = []

    def _write_summary(all_passed: bool, checks: dict) -> None:
        _write_json(run_dir / "run_summary.json", {
            "input_path": str(input_path.resolve()),
            "doc_id": caps_result.doc_id,
            "source_name": caps_result.source_name,
            "output_directory": str(run_dir),
            "overall_stoplight": caps_result.overall_stoplight,
            "final_stoplight": (merged or {}).get("final_stoplight"),
            "criteria_requiring_extra_review": (merged or {}).get("criteria_requiring_extra_review", []),
            "quality_skipped": quality is None,
            "feedback_skipped": feedback_result is None,
            "llm_base_url": llm_base_url,
            "llm_model": llm_model,
            "integration_checks": checks,
            "integration_checks_all_passed": all_passed,
            "errors": errors,
        })

    if args.no_llm:
        # ── Merge with empty quality so the contract shape stays stable ───────
        _section("Step 2 — Quality (skipped: --no-llm)")
        print("  Quality stage skipped; building merge with empty Qwen fields.")
        # No Qwen judgement available without the LLM. Use "mixed" (→ yellow) as a
        # neutral placeholder so the merge contract stays valid; the count floor
        # still applies on top of it.
        empty_quality = {
            "document_id": caps_result.doc_id,
            "criteria": {
                k: {"criterion_judgement": "mixed", "diagnostics": {},
                    "strengths": [], "weaknesses": [],
                    "manual_review": False, "manual_review_reason": [], "reason": ""}
                for k in CRITERIA_KEYS
            },
        }
        merged = dict(build_merged_feedback_input(handoff_dict, empty_quality))
    else:
        # ── Step 2: Qwen quality ──────────────────────────────────────────────
        _section("Step 2 — Qwen quality diagnosis")
        try:
            quality = dict(generate_quality_diagnostics(handoff_dict, timeout=args.llm_timeout))
        except QualityGenerationError as exc:
            print(f"\n[FATAL] Quality generation failed: {exc.reason}")
            if debug:
                traceback.print_exc()
            errors.append(f"quality: {exc.reason}")
            _write_summary(False, {})
            return 1
        review = [k for k, c in quality["criteria"].items() if c["manual_review"]]
        print(f"  manual_review criteria: {', '.join(review) or 'none'}")

        # ── Step 3: Merge ─────────────────────────────────────────────────────
        _section("Step 3 — Merge CAPS + Qwen")
        merged = dict(build_merged_feedback_input(handoff_dict, quality))
        print(f"  final_stoplight                 : {merged['final_stoplight']}")
        print(f"  criteria_requiring_extra_review : "
              f"{', '.join(merged['criteria_requiring_extra_review']) or 'none'}")

        # ── Step 4: Feedback ──────────────────────────────────────────────────
        _section("Step 4 — Feedback writer")
        try:
            feedback_result = generate_feedback(merged, timeout=args.llm_timeout)
        except FeedbackGenerationError as exc:
            print(f"\n[FATAL] Feedback generation failed: {exc.reason}")
            if debug:
                traceback.print_exc()
            errors.append(f"feedback: {exc.reason}")
            # Persist the artifacts produced so far for debugging.
            _write_json(run_dir / "quality_diagnostics.json", quality)
            _write_json(run_dir / "merged_feedback_input.json", merged)
            _write_summary(False, {})
            return 1
        print()
        _print_feedback(feedback_result)

    # ── Step 5: Integration checks ────────────────────────────────────────────
    _section("Step 5 — Integration checks")
    checks: list[CheckResult] = list(_check_merged(merged))
    if feedback_result is not None:
        checks.extend(_check_feedback(feedback_result, merged))
    else:
        print("  (feedback checks skipped — no feedback result)")
    print()
    any_fail = False
    for name, passed, detail in checks:
        any_fail = any_fail or not passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:<40}  {detail}")

    # ── Step 6: Write outputs ─────────────────────────────────────────────────
    _section("Step 6 — Writing outputs")
    print(f"  Output directory: {run_dir}\n")
    (run_dir / "input_copy.json").write_text(raw_text, encoding="utf-8")
    print("    saved: input_copy.json              [original, unmodified]")
    _write_json(run_dir / "anonymized_report.json", dict(report))
    _write_json(run_dir / "caps_handoff.json", handoff_dict)
    if quality is not None:
        _write_json(run_dir / "quality_diagnostics.json", quality)
    else:
        _write_json(run_dir / "quality_diagnostics.json",
                    {"skipped": True, "reason": "--no-llm flag set"})
    _write_json(run_dir / "merged_feedback_input.json", merged)
    if feedback_result is not None:
        _write_json(run_dir / "feedback_result.json", dict(feedback_result))
    else:
        _write_json(run_dir / "feedback_result.json",
                    {"skipped": True, "reason": "--no-llm flag set"})

    _write_summary(not any_fail, {n: {"passed": p, "detail": d} for n, p, d in checks})

    # ── Result ────────────────────────────────────────────────────────────────
    _section("Result")
    print(f"  doc_id    : {caps_result.doc_id}")
    print(f"  stoplight : {merged['final_stoplight']}")
    print(f"  output    : {run_dir}")
    if any_fail:
        print("  [RESULT] ONE OR MORE INTEGRATION CHECKS FAILED  (exit code 1)\n")
    else:
        print("  [RESULT] ALL INTEGRATION CHECKS PASSED  (exit code 0)\n")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
