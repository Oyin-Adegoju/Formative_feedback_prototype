"""Quality-diagnosis stage (Stage A).

Consumes the pre-Qwen CAPS handoff (src/feedback/handoff.py) and produces a
per-criterion content-quality diagnosis via the local LLM. This stage is the
single owner of:

  - content-quality judgement (diagnostics: laag / middel / hoog),
  - per-criterion strengths / weaknesses,
  - manual_review flagging (deferred here from CAPS — CAPS no longer owns it).

Architecture position:
    caps_handoff.json → quality_builder → [llm_client] → quality_validator
                                                          → QualityDiagnostics
                                                          → quality_diagnostics.json
"""
