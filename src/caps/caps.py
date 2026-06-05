"""caps.py — CAPS document evaluation orchestrator.

Single entry point for evaluating one document through the full CAPS pipeline:

    ParseReportDict
    → retrieval.retrieve_all_criteria
    → checks.run_all_checks
    → scoring.score_document
    → CapsRunResult

Contains no rubric logic, no scoring formulae, and no retrieval heuristics —
those live in their respective modules. This file only wires them together
in the correct order.

Architecture position:
    parser output → [anonymizer] → CAPS retrieval → CAPS checks → CAPS scoring
                                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                   orchestrated here via run_caps / run_caps_with_artifacts
"""

from __future__ import annotations

from dataclasses import dataclass

from src.caps.checks import run_all_checks
from src.caps.criterion_specs import INFIRFS_REQUIREMENTS_CRITERIA
from src.caps.models import CapsRunResult, CriterionResult, InputSource, ParseReportDict
from src.caps.retrieval import CriterionCandidates, retrieve_all_criteria
from src.caps.scoring import score_document

# ---------------------------------------------------------------------------
# Required top-level keys on any valid ParseReportDict
# ---------------------------------------------------------------------------

_REQUIRED_REPORT_KEYS: frozenset[str] = frozenset(
    {"doc_id", "source_name", "page_count", "block_count", "blocks"}
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_report(report: ParseReportDict) -> None:
    """Raise ValueError when the report envelope is missing required keys.

    Checks only the five keys that CAPS reads at every call site.
    Works identically for parser-direct and anonymized reports because
    both preserve the same top-level envelope structure.
    """
    missing = _REQUIRED_REPORT_KEYS - set(report.keys())
    if missing:
        raise ValueError(
            f"ParseReportDict is missing required keys: {sorted(missing)}"
        )


# ---------------------------------------------------------------------------
# Intermediate-output container
# ---------------------------------------------------------------------------


@dataclass
class CapsPipelineArtifacts:
    """All intermediate and final outputs of one CAPS evaluation.

    Returned by run_caps_with_artifacts for debugging, testing individual
    pipeline layers, or inspecting retrieval quality without re-running.

    candidates:        retrieval hits per criterion — output of retrieve_all_criteria.
    criterion_results: per-criterion verdicts      — output of run_all_checks.
    result:            final scored document result — output of score_document.
    """

    candidates: CriterionCandidates
    criterion_results: dict[str, CriterionResult]
    result: CapsRunResult
