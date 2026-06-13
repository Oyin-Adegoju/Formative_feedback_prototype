"""Run the CAPS pipeline on all anonymized JSON reports in data/anonymized/.

Usage:
    py -3 scripts/run_caps_on_anonymized.py

Emits a SINGLE artifact, data/anonymized/caps_handoff.json — an array with one
pre-Qwen handoff object per document (see src/feedback/handoff.py). This
consolidated file replaces the former parallel caps_results.json (CAPS
verdicts) and evidence_packets.json (evidence), both of which were strict
subsets of the handoff.

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
from src.feedback.handoff import build_handoff_from_artifacts, to_dict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "anonymized"
_HANDOFF_FILE = _DATA_DIR / "caps_handoff.json"


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
    blockers = result.blockers_triggered or ["none"]
    print(f"  blockers     : {', '.join(blockers)}")
    print()

    for key, cr in result.scorecard.results.items():
        sym_c = _STOPLIGHT_SYMBOL.get(cr.stoplight, "?")
        count_str = f"count={cr.count}  " if cr.count is not None else ""
        missing_str = ", ".join(cr.missing_signals) or "-"
        print(
            f"  {sym_c} {key:<14}  status={cr.status:<11} "
            f"{count_str}missing_signals={missing_str}"
        )
        for note in cr.notes[:2]:
            short = note[:100] + ("…" if len(note) > 100 else "")
            print(f"               -> {short}")


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _handoff_to_dict(filename: str, artifacts) -> dict:
    """Build the pre-Qwen handoff dict for one document, tagged with its filename.

    `file` is added purely as a batch-run convenience so each entry in the
    consolidated array is traceable to its source file. document_id /
    source_name remain the stable handoff identity.
    """
    handoff = build_handoff_from_artifacts(artifacts)
    data = to_dict(handoff)
    return {"file": filename, **data}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    json_files = sorted(_DATA_DIR.rglob("*_anonymized.json"))
    if not json_files:
        print(f"No *_anonymized.json files found in {_DATA_DIR}")
        sys.exit(1)

    all_handoffs = []

    for path in json_files:
        filename = path.name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            report = _normalize_report(raw)
            artifacts = run_caps_with_artifacts(report, input_source="anonymized")
            _print_result(filename, artifacts.result)
            all_handoffs.append(_handoff_to_dict(filename, artifacts))
        except Exception as exc:  # noqa: BLE001
            print(f"\n[ERROR] {filename}: {exc}")

    # Write the single consolidated pre-Qwen handoff artifact.
    _HANDOFF_FILE.write_text(
        json.dumps(all_handoffs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n[saved] {_HANDOFF_FILE}")


if __name__ == "__main__":
    main()
