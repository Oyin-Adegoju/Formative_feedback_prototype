#!/usr/bin/env python3
"""run_full_pipeline_with_feedback.py — DEPRECATED shim.

This script ran the old single-stage flow:
    anonymized JSON → CAPS → caps_handoff → feedback_writer prompt → feedback

That flow no longer matches the architecture. Feedback is now written from a
MERGED CAPS + Qwen contract produced by a separate quality-diagnosis stage.

Use instead:
    scripts/run_full_pipeline_v2.py    — full pipeline
        anonymized JSON → CAPS → caps_handoff → Qwen quality
        → quality_diagnostics → merge → merged_feedback_input → feedback
    scripts/run_quality_diagnostics.py — quality stage only

All flags from this script are supported by run_full_pipeline_v2.py
(--input, --output-dir, --max-candidates, --no-llm, --llm-timeout, --debug).
This shim forwards to it so existing invocations keep working.
"""

from __future__ import annotations

import pathlib
import runpy
import sys

_V2 = pathlib.Path(__file__).resolve().parent / "run_full_pipeline_v2.py"


def main() -> None:
    sys.stderr.write(
        "[DEPRECATED] run_full_pipeline_with_feedback.py now forwards to "
        "run_full_pipeline_v2.py (staged CAPS → Qwen → merge → feedback).\n"
    )
    # Re-exec the v2 runner as __main__ so its argparse + sys.exit handle argv.
    runpy.run_path(str(_V2), run_name="__main__")


if __name__ == "__main__":
    main()
