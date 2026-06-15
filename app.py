"""app.py — Streamlit frontend voor INFIRFS Formatieve Feedback Assistent.

Gebruik:
    python3 -m streamlit run app.py

Flow:
    Docent uploadt student-PDF + (optioneel) rubrics, klikt op
    "Genereer formatieve feedback", pipeline draait, resultaten verschijnen.
    Student kiest een bestaande run en bekijkt de feedback.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import time
from dataclasses import asdict

import streamlit as st

# ---------------------------------------------------------------------------
# Paginaconfiguratie
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="INFIRFS Feedback Assistent",
    layout="wide",
)
# ---------------------------------------------------------------------------
# Constanten
# ---------------------------------------------------------------------------

_PROJECT_ROOT = pathlib.Path(__file__).parent
_RUNS_DIR = _PROJECT_ROOT / "data" / "full_pipeline_runs"
_UPLOADS_DIR = _PROJECT_ROOT / "data" / "uploads"
_CATALOG_PATH = _PROJECT_ROOT / "data" / "reference" / "people_catalog.json"

sys.path.insert(0, str(_PROJECT_ROOT))

_CRITERION_LABELS: dict[str, str] = {
    "beperking":     "Beperking & deskresearch",
    "stakeholders":  "Stakeholders",
    "requirements":  "Requirements",
    "taalkeuze":     "Taalkeuze & consequenties",
    "security":      "Security",
}

_NIVEAU_LABEL: dict[str, str] = {
    "red":    "Onder niveau",
    "yellow": "Op niveau",
    "green":  "Boven niveau",
}

_STOPLIGHT_COLOR: dict[str, str] = {
    "red":    "#c62828",
    "yellow": "#e65100",
    "green":  "#2e7d32",
}

_STOPLIGHT_BADGE: dict[str, str] = {
    "red":    "🔴",
    "yellow": "🟡",
    "green":  "🟢",
}

_STOPLIGHT_NL: dict[str, str] = {
    "red":    "Rood",
    "yellow": "Geel",
    "green":  "Groen",
}

_SIGNAL_LABEL: dict[str, str] = {
    "positive":      "sterk signaal",
    "weak":          "zwak signaal",
    "absent_marker": "sectie zonder inhoud",
}

_LEVEL_COLOR: dict[str, str] = {
    "laag":   "#c62828",
    "middel": "#e65100",
    "hoog":   "#2e7d32",
}

_HS_GREEN = "#004438"
