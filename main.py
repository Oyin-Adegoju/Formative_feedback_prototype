#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import traceback
from typing import Final

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from scripts.generate_anonymized import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_INTERNAL_DIR,
    DEFAULT_PUBLIC_DIR,
    generate_one,
)
from scripts.run_full_pipeline_v2 import _check_feedback, _check_merged  # noqa: E402
from src.caps.caps import run_caps_with_artifacts  # noqa: E402
from src.feedback.feedback_builder import (  # noqa: E402
    FeedbackGenerationError,
    generate_feedback,
)
from src.feedback.full_content import build_full_content_handoff  # noqa: E402
from src.feedback.handoff import build_handoff_from_artifacts  # noqa: E402
from src.feedback.handoff import to_dict as handoff_to_dict  # noqa: E402
from src.feedback.merge_builder import build_merged_feedback_input  # noqa: E402
from src.privacy.catalog import load_catalog  # noqa: E402
from src.quality.quality_builder import (  # noqa: E402
    QualityGenerationError,
    generate_quality_diagnostics,
)

# ---------------------------------------------------------------------------
# Strategieën: model + hoe de handoff gebouwd wordt + timeout
# ---------------------------------------------------------------------------

STRATEGIES: Final[dict[str, dict]] = {
    "14b": {"model": "qwen2.5:14b",     "mode": "trimmed",     "timeout": 800},
    "32b": {"model": "qwen2.5-32b-ctx", "mode": "fullcontent", "timeout": 1800},
}

_DEFAULT_OUTPUT_DIR: Final[pathlib.Path] = _ROOT / "data" / "main_runs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(report: dict) -> dict:
    """Vul page_count aan wanneer het anonymized report het weglaat (CAPS-shim)."""
    if not report.get("page_count"):
        report["page_count"] = max(
            (b["page_no"] for b in report.get("blocks", [])), default=0
        )
    return report


def _write_json(path: pathlib.Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _build_handoff(report: dict, mode: str) -> dict:
    """Strategie-specifieke handoff: full-content (32B) of getrimde CAPS-evidence (14B)."""
    if mode == "fullcontent":
        return build_full_content_handoff(report)
    artifacts = run_caps_with_artifacts(report, input_source="anonymized")
    return handoff_to_dict(build_handoff_from_artifacts(artifacts))


def _section(title: str) -> None:
    print(f"\n{'─' * 64}\n  {title}\n{'─' * 64}")


# ---------------------------------------------------------------------------
# Eén strategie draaien
# ---------------------------------------------------------------------------


def run_strategy(report: dict, strategy: str, out_dir: pathlib.Path) -> dict:
    """Draai handoff → quality → merge → feedback voor één strategie.

    Schrijft alle tussenproducten weg en draait de integratie-checks. Vangt LLM-
    en validatiefouten zodat één falende strategie de andere niet meesleept; er
    wordt nooit een oordeel verzonnen (geen fallback).

    Returns een samenvattingsdict (status, final_stoplight, checks-overzicht).
    """
    cfg = STRATEGIES[strategy]
    os.environ["LLM_MODEL"] = cfg["model"]
    strat_dir = out_dir / strategy
    summary: dict = {"strategy": strategy, "model": cfg["model"], "mode": cfg["mode"]}

    _section(f"Strategie {strategy}  (model={cfg['model']}, mode={cfg['mode']})")

    # 1) Handoff (geen LLM) — mag altijd draaien.
    try:
        handoff = _build_handoff(report, cfg["mode"])
    except Exception as exc:  # noqa: BLE001 — handoff-bouw mag de run niet stilletjes breken
        print(f"  [FOUT] handoff-bouw faalde: {exc}")
        summary.update(status="failed", stage="handoff", error=str(exc))
        return summary
    _write_json(strat_dir / "handoff.json", handoff)
    n_items = sum(len(c["evidence_items"]) for c in handoff["criteria"].values())
    print(f"  handoff gebouwd: {n_items} evidence-items totaal")

    # 2) Quality (LLM — stage A)
    try:
        quality = dict(generate_quality_diagnostics(handoff, timeout=cfg["timeout"]))
    except QualityGenerationError as exc:
        print(f"  [FOUT] quality-stap faalde: {exc.reason}")
        summary.update(status="failed", stage="quality", error=exc.reason)
        return summary
    _write_json(strat_dir / "quality_diagnostics.json", quality)
    review = [k for k, c in quality["criteria"].items() if c["manual_review"]]
    print(f"  quality klaar — manual_review: {', '.join(review) or 'geen'}")

    # 3) Merge (deterministisch — bepaalt final_stoplight)
    merged = dict(build_merged_feedback_input(handoff, quality))
    _write_json(strat_dir / "merged_feedback_input.json", merged)
    print(f"  merge klaar — final_stoplight: {merged['final_stoplight']}")

    # 4) Feedback (LLM — stage B)
    try:
        feedback = generate_feedback(merged, timeout=cfg["timeout"])
    except FeedbackGenerationError as exc:
        print(f"  [FOUT] feedback-stap faalde: {exc.reason}")
        _write_json(strat_dir / "quality_diagnostics.json", quality)
        summary.update(status="failed", stage="feedback", error=exc.reason,
                       final_stoplight=merged["final_stoplight"])
        return summary
    _write_json(strat_dir / "feedback_result.json", dict(feedback))
    print(f"  feedback klaar — {len(feedback['feedback'])} criterium-entries")

    # 5) Integratie-checks (hergebruikt uit run_full_pipeline_v2.py)
    checks = list(_check_merged(merged)) + list(_check_feedback(feedback, merged))
    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name:<40} {detail}")

    summary.update(
        status="ok",
        final_stoplight=merged["final_stoplight"],
        criteria_requiring_extra_review=merged["criteria_requiring_extra_review"],
        checks_passed=len(checks) - len(failed),
        checks_total=len(checks),
        checks_failed=failed,
    )
    return summary


# ---------------------------------------------------------------------------
# Vergelijking printen
# ---------------------------------------------------------------------------


def _print_comparison(summaries: list[dict]) -> None:
    _section("Vergelijking")
    for s in summaries:
        if s["status"] != "ok":
            print(f"  {s['strategy']:>4}  [FAILED @ {s.get('stage', '?')}]  {s.get('error', '')[:80]}")
            continue
        review = ", ".join(s.get("criteria_requiring_extra_review") or []) or "geen"
        chk = f"{s['checks_passed']}/{s['checks_total']} checks"
        flag = "" if not s["checks_failed"] else f"  (FAIL: {', '.join(s['checks_failed'])})"
        print(f"  {s['strategy']:>4}  stoplicht={s['final_stoplight']:<6}  "
              f"extra-review={review:<28}  {chk}{flag}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pdf", required=True, type=pathlib.Path,
                        help="Pad naar de bron-PDF.")
    parser.add_argument("--strategy", choices=list(STRATEGIES) + ["both"], default="both",
                        help="Welke strategie(ën) draaien (default: both).")
    parser.add_argument("--label", default=None,
                        help="Optioneel kwaliteitslabel (Goed/Voldoende/onvoldoende); "
                             "bepaalt de anonimisatie-subfolder. Default: geen subfolder.")
    parser.add_argument("--catalog", type=pathlib.Path, default=DEFAULT_CATALOG,
                        help=f"People-catalogus (default: {DEFAULT_CATALOG}).")
    parser.add_argument("--output-dir", type=pathlib.Path, default=_DEFAULT_OUTPUT_DIR,
                        help=f"Basis-outputmap (default: {_DEFAULT_OUTPUT_DIR}).")
    parser.add_argument("--llm-base-url", default=None,
                        help="Override LLM_BASE_URL (default: env of http://localhost:11434/v1).")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"[FATAL] PDF niet gevonden: {args.pdf}")
        return 1

    os.environ.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
    if args.llm_base_url:
        os.environ["LLM_BASE_URL"] = args.llm_base_url

    strategies = list(STRATEGIES) if args.strategy == "both" else [args.strategy]

    print("=" * 64)
    print("  main.py — anonimiseer → 14B/32B → quality → merge → feedback")
    print("=" * 64)
    print(f"  pdf          : {args.pdf}")
    print(f"  strategieën  : {', '.join(strategies)}")
    print(f"  LLM base URL : {os.environ['LLM_BASE_URL']}")

    # ── Stap 1: één keer anonimiseren ────────────────────────────────────────
    _section("Stap 1 — anonimiseren (1×)")
    catalog = load_catalog(str(args.catalog))
    doc_id, mapping_count = generate_one(
        args.pdf, args.label, catalog, DEFAULT_PUBLIC_DIR, DEFAULT_INTERNAL_DIR,
    )
    sub = args.label or ""
    public_path = DEFAULT_PUBLIC_DIR / sub / f"{doc_id}_anonymized.json"
    report = _normalize(json.loads(public_path.read_text(encoding="utf-8")))
    print(f"  doc_id       : {doc_id}  (mapping={mapping_count})")
    print(f"  report       : {public_path}")

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = args.output_dir / f"{doc_id}_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "anonymized_report.json", report)

    # ── Stap 2: strategieën draaien op hetzelfde report ───────────────────────
    summaries: list[dict] = []
    for strategy in strategies:
        try:
            summaries.append(run_strategy(report, strategy, out_dir))
        except Exception as exc:  # noqa: BLE001 — één strategie mag de andere niet stilzetten
            print(f"  [FOUT] onverwachte fout in strategie {strategy}: {exc}")
            traceback.print_exc()
            summaries.append({"strategy": strategy, "status": "failed",
                              "stage": "unexpected", "error": str(exc)})

    # ── Stap 3: vergelijking + samenvatting wegschrijven ──────────────────────
    _print_comparison(summaries)
    _write_json(out_dir / "comparison.json", {
        "doc_id": doc_id,
        "pdf": str(args.pdf),
        "llm_base_url": os.environ["LLM_BASE_URL"],
        "strategies": summaries,
    })
    print(f"\n  output       : {out_dir}")

    all_ok = all(s["status"] == "ok" and not s.get("checks_failed") for s in summaries)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
