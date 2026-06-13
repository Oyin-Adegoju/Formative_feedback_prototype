#!/usr/bin/env python3
"""run_quality_diagnostics.py — Stage A runner (CAPS → handoff → Qwen quality).

Runs the quality-diagnosis stage for one anonymized document:
    anonymized JSON → CAPS → caps_handoff → Qwen quality → quality_diagnostics

────────────────────────────────────────────────────────────────────────────
Usage — server with Ollama / Qwen2.5-14B:
    LLM_BASE_URL=http://localhost:11434/v1 \\
    LLM_MODEL=qwen2.5:14b \\
    python scripts/run_quality_diagnostics.py \\
        --input data/anonymized/Goed/2e138dc4_anonymized.json

Debug mode (full stack traces on failure):
    python scripts/run_quality_diagnostics.py \\
        --input data/anonymized/Goed/2e138dc4_anonymized.json --debug

────────────────────────────────────────────────────────────────────────────
Output is written to:
    data/quality_runs/<doc_id>_<YYYYMMDD_HHMMSS>/
        input_copy.json          exact copy of the input file
        anonymized_report.json   normalized report passed to CAPS
        caps_handoff.json        pre-Qwen handoff (CAPS verdicts + shaped evidence)
        quality_diagnostics.json validated Qwen quality diagnosis
        run_summary.json         machine-readable run metadata + check results

Exit codes:
    0 — all steps completed and all integration checks passed
    1 — fatal error OR LLM call / validation failed OR a check failed

This stage has NO fallback. If the quality LLM call, timeout, or validation
fails the script stops with exit code 1.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import traceback
from typing import Final

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.caps.caps import run_caps_with_artifacts
from src.caps.criterion_specs import CRITERIA_KEYS
from src.caps.models import ParseReportDict
from src.feedback.handoff import build_handoff_from_artifacts, to_dict as handoff_to_dict
from src.quality.output_schema import QUALITY_DIMENSIONS, QUALITY_LEVELS
from src.quality.quality_builder import QualityGenerationError, generate_quality_diagnostics

_PROJECT_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIR: Final[pathlib.Path] = _PROJECT_ROOT / "data" / "quality_runs"
_EXPECTED_CRITERIA: Final[frozenset[str]] = frozenset(CRITERIA_KEYS)

# (check_name, passed, detail_message)
CheckResult = tuple[str, bool, str]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run CAPS + the Qwen quality-diagnosis stage on one anonymized JSON file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", required=True, metavar="PATH",
                        help="Path to the anonymized JSON input file.")
    parser.add_argument("--output-dir", default=str(_DEFAULT_OUTPUT_DIR), metavar="DIR",
                        help="Base output directory (default: data/quality_runs).")
    parser.add_argument("--max-candidates", type=int, default=20, metavar="N",
                        help="Max retrieval candidates per criterion (default: 20).")
    parser.add_argument("--llm-timeout", type=int, default=800, metavar="SECONDS",
                        help="LLM request timeout in seconds (default: 800).")
    parser.add_argument("--debug", action="store_true",
                        help="Print full stack traces on unexpected failures.")
    return parser.parse_args()


def _normalize_report(raw: dict) -> ParseReportDict:
    """Inject page_count when absent — the only anonymized↔CAPS contract gap."""
    if "page_count" not in raw or raw["page_count"] is None:
        blocks = raw.get("blocks") or []
        raw["page_count"] = max((b["page_no"] for b in blocks), default=0)
    return raw  # type: ignore[return-value]


def _write_json(path: pathlib.Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    size_kb = path.stat().st_size / 1024
    print(f"    saved: {path.name:<28}  ({size_kb:.1f} KB)")


def _section(title: str) -> None:
    print(f"\n{'─' * 64}\n  {title}\n{'─' * 64}")


def _check_quality(quality: dict) -> list[CheckResult]:
    """Structural sanity checks on the validated quality diagnosis."""
    results: list[CheckResult] = []

    actual = set(quality.get("criteria", {}).keys())
    ok = actual == _EXPECTED_CRITERIA
    results.append(("quality_criteria_keys", ok,
                    "all 5 present" if ok else f"got {sorted(actual)}"))

    bad_dims: list[str] = []
    bad_levels: list[str] = []
    for key, crit in quality.get("criteria", {}).items():
        diag = crit.get("diagnostics", {})
        if set(diag.keys()) != set(QUALITY_DIMENSIONS.get(key, ())):
            bad_dims.append(key)
        for dim, level in diag.items():
            if level not in QUALITY_LEVELS:
                bad_levels.append(f"{key}.{dim}={level}")
    results.append(("quality_diagnostics_dimensions", not bad_dims,
                    "all match" if not bad_dims else f"mismatch: {bad_dims}"))
    results.append(("quality_diagnostics_levels", not bad_levels,
                    "all valid" if not bad_levels else f"invalid: {bad_levels[:5]}"))

    bad_review = [
        key for key, crit in quality.get("criteria", {}).items()
        if not isinstance(crit.get("manual_review"), bool)
    ]
    results.append(("quality_manual_review_is_bool", not bad_review,
                    "all bool" if not bad_review else f"not bool: {bad_review}"))

    # The quality artifact must NOT carry a hidden_score anywhere.
    no_score = "hidden_score" not in json.dumps(quality)
    results.append(("quality_no_hidden_score", no_score,
                    "clean" if no_score else "FOUND hidden_score"))

    return results


def main() -> int:
    args = _parse_args()
    debug: bool = args.debug

    print("=" * 64)
    print("  Quality Stage: CAPS + Qwen quality diagnosis")
    print("=" * 64)
    print(f"  input : {args.input}")
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

    _section("Step 2 — Qwen quality diagnosis")
    try:
        quality = generate_quality_diagnostics(handoff_dict, timeout=args.llm_timeout)
    except QualityGenerationError as exc:
        print(f"\n[FATAL] Quality generation failed: {exc.reason}")
        if debug:
            traceback.print_exc()
        _write_json(run_dir / "run_summary.json", {
            "input_path": str(input_path.resolve()),
            "doc_id": caps_result.doc_id,
            "output_directory": str(run_dir),
            "overall_stoplight": caps_result.overall_stoplight,
            "quality_ok": False,
            "integration_checks": {},
            "integration_checks_all_passed": False,
            "errors": [exc.reason],
        })
        return 1

    review = [k for k, c in quality["criteria"].items() if c["manual_review"]]
    print(f"  manual_review criteria: {', '.join(review) or 'none'}")

    _section("Step 3 — Integration checks")
    checks = _check_quality(dict(quality))
    any_fail = False
    for name, passed, detail in checks:
        label = "PASS" if passed else "FAIL"
        any_fail = any_fail or not passed
        print(f"  [{label}] {name:<36}  {detail}")

    _section("Step 4 — Writing outputs")
    print(f"  Output directory: {run_dir}\n")
    (run_dir / "input_copy.json").write_text(raw_text, encoding="utf-8")
    print("    saved: input_copy.json          [original, unmodified]")
    _write_json(run_dir / "anonymized_report.json", dict(report))
    _write_json(run_dir / "caps_handoff.json", handoff_dict)
    _write_json(run_dir / "quality_diagnostics.json", dict(quality))
    _write_json(run_dir / "run_summary.json", {
        "input_path": str(input_path.resolve()),
        "doc_id": caps_result.doc_id,
        "source_name": caps_result.source_name,
        "output_directory": str(run_dir),
        "overall_stoplight": caps_result.overall_stoplight,
        "criteria_requiring_extra_review": review,
        "quality_ok": True,
        "integration_checks": {n: {"passed": p, "detail": d} for n, p, d in checks},
        "integration_checks_all_passed": not any_fail,
        "errors": [],
    })

    _section("Result")
    print(f"  doc_id : {caps_result.doc_id}")
    print(f"  output : {run_dir}")
    if any_fail:
        print("  [RESULT] ONE OR MORE INTEGRATION CHECKS FAILED  (exit code 1)\n")
    else:
        print("  [RESULT] ALL INTEGRATION CHECKS PASSED  (exit code 0)\n")
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
