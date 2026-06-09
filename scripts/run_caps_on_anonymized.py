"""Run the CAPS pipeline on all anonymized JSON reports in data/anonymized/.

Usage:
    py -3 scripts/run_caps_on_anonymized.py

Compatibility shim applied here (no production CAPS files changed):
    The anonymized JSON is missing `page_count` at the top level.
    _normalize_report() injects it from max(block.page_no) before
    passing the report to run_caps_with_artifacts().
"""

from __future__ import annotations

import json
import pathlib
import sys

# Ensure UTF-8 output on Windows terminals that default to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Allow running from the project root without installing the package.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.caps.caps import run_caps_with_artifacts
from src.caps.models import CapsRunResult, ParseReportDict
from src.feedback.packet_builder import build_evidence_packets

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "anonymized"
_RESULTS_FILE = _DATA_DIR / "caps_results.json"
_EVIDENCE_FILE = _DATA_DIR / "evidence_packets.json"


# ---------------------------------------------------------------------------
# Compatibility shim
# ---------------------------------------------------------------------------


def _normalize_report(raw: dict) -> ParseReportDict:
    """Inject page_count when absent (only mismatch between anonymized JSON and CAPS contract).

    All other extra fields (quality_label, mapping_count, mapping, per-block doc_id)
    are harmless at runtime — dicts may carry unknown keys without issue.
    """
    if "page_count" not in raw or raw["page_count"] is None:
        blocks = raw.get("blocks") or []
        raw["page_count"] = max((b["page_no"] for b in blocks), default=0)
    return raw  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

_STOPLIGHT_SYMBOL = {"green": "[GREEN]", "yellow": "[YELLOW]", "red": "[RED]"}


def _print_result(filename: str, result: CapsRunResult) -> None:
    sym = _STOPLIGHT_SYMBOL.get(result.overall_stoplight, "?")
    print(f"\n{'=' * 60}")
    print(f"  {filename}")
    print(f"{'=' * 60}")
    print(f"  doc_id       : {result.doc_id}")
    print(f"  stoplight    : {sym} {result.overall_stoplight}")
    print(f"  hidden_score : {result.scorecard.hidden_score} / 15")
    print(f"  manual_review: {result.manual_review_required}")
    blockers = result.blockers_triggered or ["none"]
    print(f"  blockers     : {', '.join(blockers)}")
    print()

    for key, cr in result.scorecard.results.items():
        sym_c = _STOPLIGHT_SYMBOL.get(cr.stoplight, "?")
        count_str = f"count={cr.count}  " if cr.count is not None else ""
        print(
            f"  {sym_c} {key:<14}  status={cr.status:<11} "
            f"{count_str}manual_review={cr.manual_review}"
        )
        for note in cr.notes[:2]:
            short = note[:100] + ("…" if len(note) > 100 else "")
            print(f"               -> {short}")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _result_to_dict(filename: str, result: CapsRunResult) -> dict:
    criteria = {}
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


def _packets_to_dict(filename: str, doc_id: str, packets: dict) -> dict:
    criteria = {}
    for key, pkt in packets.items():
        criteria[key] = {
            "manual_review": pkt.manual_review,
            "notes": pkt.notes,
            "missing_signals": pkt.missing_signals,
            "evidence_items": [
                {
                    "block_id": item.block_id,
                    "page_no": item.page_no,
                    "block_type": item.block_type,
                    "heading_path": item.heading_path,
                    "excerpt": item.excerpt,
                    "selection_reason": item.selection_reason,
                    "signal_class": item.signal_class,
                }
                for item in pkt.evidence_items
            ],
        }
    return {"file": filename, "doc_id": doc_id, "criteria": criteria}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    json_files = sorted(_DATA_DIR.rglob("*_anonymized.json"))
    if not json_files:
        print(f"No *_anonymized.json files found in {_DATA_DIR}")
        sys.exit(1)

    all_results = []
    all_evidence = []

    for path in json_files:
        filename = path.name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            report = _normalize_report(raw)
            artifacts = run_caps_with_artifacts(report, input_source="anonymized")
            result = artifacts.result
            packets = build_evidence_packets(artifacts)
            _print_result(filename, result)
            all_results.append(_result_to_dict(filename, result))
            all_evidence.append(_packets_to_dict(filename, result.doc_id, packets))
        except Exception as exc:  # noqa: BLE001
            print(f"\n[ERROR] {filename}: {exc}")

    # Write machine-readable summaries.
    _RESULTS_FILE.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n[saved] {_RESULTS_FILE}")
    _EVIDENCE_FILE.write_text(
        json.dumps(all_evidence, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[saved] {_EVIDENCE_FILE}")


if __name__ == "__main__":
    main()
