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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_caps_with_artifacts(
    report: ParseReportDict,
    input_source: InputSource = "parser_direct",
    max_candidates: int = 20,
) -> CapsPipelineArtifacts:
    """Evaluate one document through the full CAPS pipeline, retaining all intermediates.

    This is the canonical implementation of the pipeline. run_caps delegates
    here and returns only the final CapsRunResult.

    Args:
        report: A ParseReportDict from the parser or the anonymizer.
            Both satisfy the BlockDict contract without changes here.
        input_source: "parser_direct" (default) for raw parser output;
            "anonymized" when CAPS receives anonymizer output.
            Forwarded unchanged to score_document for auditability.
        max_candidates: Upper bound on retrieval hits per criterion.
            Passed through to retrieve_all_criteria.

    Returns:
        CapsPipelineArtifacts with candidates, criterion_results, and result.

    Raises:
        ValueError: if the report is missing required top-level keys, or if
            criterion_results keys do not match the expected set (raised by
            score_document's internal validation).
    """
    _validate_report(report)

    candidates = retrieve_all_criteria(
        report,
        criteria=INFIRFS_REQUIREMENTS_CRITERIA,
        max_candidates=max_candidates,
    )
    criterion_results = run_all_checks(candidates)
    result = score_document(report, criterion_results, input_source)

    return CapsPipelineArtifacts(
        candidates=candidates,
        criterion_results=criterion_results,
        result=result,
    )


def run_caps(
    report: ParseReportDict,
    input_source: InputSource = "parser_direct",
    max_candidates: int = 20,
) -> CapsRunResult:
    """Evaluate one document through the full CAPS pipeline.

    Thin wrapper around run_caps_with_artifacts that discards intermediates.
    Use this in production; use run_caps_with_artifacts for debugging.

    Args:
        report: A ParseReportDict from the parser or the anonymizer.
        input_source: Forwarded to score_document for auditability.
        max_candidates: Maximum retrieval hits per criterion.

    Returns:
        CapsRunResult — the complete, document-level scoring output.
    """
    return run_caps_with_artifacts(report, input_source, max_candidates).result
