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
# ---------------------------------------------------------------------------
# Huisstijl (Hogeschool Leiden #004438) — verbeterde opmaak
# ---------------------------------------------------------------------------

def _inject_css() -> None:
    st.markdown(
        f"""
        <style>
        /* ── Sidebar achtergrond ── */
        [data-testid="stSidebar"],
        [data-testid="stSidebar"] > div:first-child {{
            background-color: {_HS_GREEN} !important;
            padding-top: 0 !important;
        }}

        /* Alle tekst in sidebar wit */
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] div {{
            color: white !important;
        }}

        /* ── Sidebar header blok ── */
        .sidebar-header {{
            background: rgba(0,0,0,0.2);
            padding: 20px 16px 16px 16px;
            margin: -1rem -1rem 1rem -1rem;
            border-bottom: 1px solid rgba(255,255,255,0.12);
        }}
        .sidebar-logo {{
            font-size: 1.25rem;
            font-weight: 800;
            letter-spacing: 0.04em;
            color: white !important;
        }}
        .sidebar-subtitle {{
            font-size: 0.78rem;
            color: rgba(255,255,255,0.65) !important;
            margin-top: 2px;
            letter-spacing: 0.02em;
        }}

        /* ── Navigatie radio als menu-items ── */
        [data-testid="stSidebar"] [data-testid="stRadio"] > label {{
            font-size: 0.72rem !important;
            font-weight: 700 !important;
            letter-spacing: 0.08em !important;
            text-transform: uppercase !important;
            color: rgba(255,255,255,0.5) !important;
            margin-bottom: 4px !important;
        }}
        /* Verberg de radio-cirkel */
        [data-testid="stSidebar"] [data-testid="stRadio"] [data-testid="stMarkdownContainer"] + div,
        [data-testid="stSidebar"] [data-testid="stRadio"] label > div:first-child {{
            display: none !important;
        }}
        /* Elke radio-optie als menu-rij */
        [data-testid="stSidebar"] [data-testid="stRadio"] label {{
            display: flex !important;
            align-items: center !important;
            padding: 10px 14px !important;
            border-radius: 8px !important;
            margin-bottom: 4px !important;
            font-size: 0.94rem !important;
            font-weight: 600 !important;
            letter-spacing: 0 !important;
            text-transform: none !important;
            color: rgba(255,255,255,0.7) !important;
            transition: background 0.15s ease !important;
            cursor: pointer !important;
        }}
        [data-testid="stSidebar"] [data-testid="stRadio"] label:hover {{
            background: rgba(255,255,255,0.1) !important;
            color: white !important;
        }}
        /* Geselecteerde optie */
        [data-testid="stSidebar"] [data-testid="stRadio"] label[data-baseweb="radio"]:has(input:checked),
        [data-testid="stSidebar"] [data-testid="stRadio"] div[data-checked="true"] > label {{
            background: rgba(255,255,255,0.15) !important;
            color: white !important;
            border-left: 3px solid white !important;
        }}

        /* ── Selectbox in sidebar ── */
        [data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] {{
            background-color: rgba(255,255,255,0.1) !important;
            border-color: rgba(255,255,255,0.2) !important;
            border-radius: 8px !important;
        }}
        [data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] span {{
            color: white !important;
        }}

        /* ── Lijn in sidebar ── */
        [data-testid="stSidebar"] hr {{
            border-color: rgba(255,255,255,0.12) !important;
            margin: 12px 0 !important;
        }}

        /* ── Knop in sidebar ── */
        [data-testid="stSidebar"] .stButton > button {{
            background-color: rgba(255,255,255,0.12) !important;
            color: white !important;
            border: 1px solid rgba(255,255,255,0.25) !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            font-size: 0.88rem !important;
            transition: background 0.15s ease !important;
        }}
        [data-testid="stSidebar"] .stButton > button:hover {{
            background-color: rgba(255,255,255,0.22) !important;
        }}

        /* ── Sidebar sectielabel ── */
        .sidebar-section-label {{
            font-size: 0.7rem;
            font-weight: 700;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: rgba(255,255,255,0.45) !important;
            margin: 16px 0 6px 2px;
        }}

        /* ── Criterion cards ── */
        .criterion-card {{
            background: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 10px;
            padding: 18px 20px;
            margin-bottom: 16px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.06);
        }}
        .criterion-card-title {{
            font-size: 1rem;
            font-weight: 700;
            color: {_HS_GREEN};
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .criterion-card-attention {{
            color: #e65100 !important;
        }}

        /* ── Evidence blokken ── */
        .hs-evidence {{
            background: #f0f4f3;
            border-left: 4px solid {_HS_GREEN};
            padding: 8px 12px;
            margin: 6px 0;
            border-radius: 4px;
            font-size: 0.85rem;
        }}

        /* ── Stoplicht banner ── */
        .stoplight-banner {{
            border-radius: 10px;
            padding: 16px 22px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 14px;
            font-size: 1.1rem;
            font-weight: 600;
        }}
        .stoplight-green {{ background: #e8f5e9; border-left: 6px solid #2e7d32; color: #2e7d32; }}
        .stoplight-yellow {{ background: #fff3e0; border-left: 6px solid #e65100; color: #e65100; }}
        .stoplight-red {{ background: #ffebee; border-left: 6px solid #c62828; color: #c62828; }}

        /* ── Verzend knop ── */
        .send-btn-wrapper .stButton > button {{
            background-color: {_HS_GREEN} !important;
            color: white !important;
            border: none !important;
            border-radius: 6px !important;
            padding: 8px 20px !important;
            font-weight: 600 !important;
            width: 100%;
        }}
        .send-btn-wrapper .stButton > button:hover {{
            background-color: #006655 !important;
            box-shadow: 0 2px 8px rgba(0,68,56,0.3) !important;
        }}

        /* ── Denkende AI animatie ── */
        @keyframes thinking-pulse {{
            0%, 80%, 100% {{ opacity: 0.2; transform: scale(0.8); }}
            40% {{ opacity: 1; transform: scale(1); }}
        }}
        .thinking-indicator {{
            display: flex;
            align-items: center;
            gap: 12px;
            background: #f0f4f3;
            border: 1px solid #b2dfdb;
            border-radius: 12px;
            padding: 18px 24px;
            margin: 20px 0;
        }}
        .thinking-icon {{
            font-size: 1.8rem;
            animation: thinking-pulse 1.4s infinite ease-in-out;
        }}
        .thinking-text {{
            font-size: 1rem;
            color: {_HS_GREEN};
            font-weight: 600;
        }}
        .thinking-dots {{
            display: flex;
            gap: 5px;
            margin-left: 4px;
        }}
        .thinking-dots span {{
            width: 8px;
            height: 8px;
            background-color: {_HS_GREEN};
            border-radius: 50%;
            display: inline-block;
            animation: thinking-pulse 1.4s infinite ease-in-out;
        }}
        .thinking-dots span:nth-child(2) {{ animation-delay: 0.2s; }}
        .thinking-dots span:nth-child(3) {{ animation-delay: 0.4s; }}

        /* ── Sectiekopjes ── */
        .section-header {{
            font-size: 1.05rem;
            font-weight: 700;
            color: #1a1a2e;
            border-bottom: 2px solid {_HS_GREEN};
            padding-bottom: 6px;
            margin: 22px 0 12px 0;
        }}

        /* ── Expander styling ── */
        [data-testid="stExpander"] > details > summary {{
            font-weight: 700;
            color: {_HS_GREEN};
            font-size: 0.97rem;
        }}
        [data-testid="stExpander"] > details > summary p {{
            font-weight: 700 !important;
            color: {_HS_GREEN} !important;
        }}
        [data-testid="stExpander"] > details {{
            border: 1px solid #e0e0e0 !important;
            border-radius: 8px !important;
            margin-bottom: 10px !important;
            background: #fafafa;
        }}

        /* ── Primaire knop (Genereer feedback) HS Leiden groen ── */
        button[data-testid="baseButton-primary"],
        .stButton > button[kind="primary"] {{
            background-color: {_HS_GREEN} !important;
            color: white !important;
            border: none !important;
            border-radius: 6px !important;
            font-weight: 600 !important;
        }}
        button[data-testid="baseButton-primary"]:hover,
        .stButton > button[kind="primary"]:hover {{
            background-color: #006655 !important;
            box-shadow: 0 2px 8px rgba(0,68,56,0.3) !important;
        }}

        /* ── Download knoppen HS Leiden groen ── */
        [data-testid="stDownloadButton"] > button {{
            background-color: {_HS_GREEN} !important;
            color: white !important;
            border: none !important;
            border-radius: 6px !important;
            font-weight: 600 !important;
            width: 100%;
        }}
        [data-testid="stDownloadButton"] > button:hover {{
            background-color: #006655 !important;
            box-shadow: 0 2px 8px rgba(0,68,56,0.3) !important;
        }}

        /* ── Notities textarea ── */
        .notes-label {{
            font-size: 0.8rem;
            color: #666;
            margin-top: 8px;
            font-style: italic;
        }}

        /* ── Student feedback blok ── */
        .student-feedback-block {{
            background: #fafafa;
            border-radius: 8px;
            padding: 14px 16px;
            margin-bottom: 8px;
            border: 1px solid #eeeeee;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
# ---------------------------------------------------------------------------
# PDF-validatie (ongewijzigd)
# ---------------------------------------------------------------------------

_MIN_BYTES = 2_048
_MAX_BYTES = 50 * 1024 * 1024


def _validate_pdf(pdf_bytes: bytes, filename: str) -> str | None:
    if not filename.lower().endswith(".pdf"):
        return "Alleen PDF-bestanden worden ondersteund."
    if len(pdf_bytes) < _MIN_BYTES:
        return "Het bestand is te klein om een geldig PDF te zijn."
    if len(pdf_bytes) > _MAX_BYTES:
        return (
            f"Het bestand is groter dan 50 MB "
            f"({len(pdf_bytes) // (1024 * 1024)} MB). Upload een kleiner document."
        )
    if not pdf_bytes.startswith(b"%PDF"):
        return "Het geupload bestand is geen geldig PDF (ontbrekende PDF-header)."
    return None

# ---------------------------------------------------------------------------
# Pipeline helpers (ongewijzigd)
# ---------------------------------------------------------------------------

def _parse_and_anonymize(pdf_bytes: bytes, source_name: str) -> pathlib.Path:
    from src.parser import extract_raw_elements, build_blocks
    from src.parser.report import generate_report
    from src.privacy.anonymizer import anonymize_blocks
    from src.privacy.catalog import load_catalog, PeopleCatalog

    _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    doc_id = hashlib.sha256(pdf_bytes).hexdigest()[:8]
    temp_pdf = _UPLOADS_DIR / f"{doc_id}_upload.pdf"
    temp_pdf.write_bytes(pdf_bytes)

    raw = extract_raw_elements(str(temp_pdf))
    blocks = build_blocks(raw, doc_id=doc_id)

    if not blocks:
        raise ValueError(
            "Geen tekstblokken gevonden in het PDF. Mogelijk een gescand "
            "document zonder OCR, of een leeg bestand."
        )

    report = generate_report(blocks, str(temp_pdf), doc_id)

    try:
        catalog = load_catalog(_CATALOG_PATH)
    except Exception:
        catalog = PeopleCatalog(personen=[])

    anon_blocks, mapping = anonymize_blocks(blocks, catalog)

    anon_report = {
        **report,
        "source_name": source_name,
        "block_count": len(anon_blocks),
        "mapping_count": len(mapping),
        "mapping": {},
        "blocks": [asdict(b) for b in anon_blocks],
    }

    anon_path = _UPLOADS_DIR / f"{doc_id}_anonymized.json"
    anon_path.write_text(
        json.dumps(anon_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return anon_path


def _run_pipeline(anon_path: pathlib.Path) -> tuple[pathlib.Path | None, str]:
    result = None
    try:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/run_full_pipeline_v2.py",
                "--input", str(anon_path),
                "--llm-timeout", "300",
            ],
            capture_output=True, text=True,
            cwd=str(_PROJECT_ROOT), timeout=720,
        )
    except subprocess.TimeoutExpired:
        fout = "Pipeline gestopt na 12 minuten — mogelijke oorzaak: Ollama niet actief."
    except Exception as exc:
        return None, f"Pipeline kon niet worden gestart: {exc}"
    else:
        fout = ""

    if result is not None:
        for line in result.stdout.splitlines():
            if "Output directory:" in line:
                run_dir = pathlib.Path(line.split("Output directory:")[-1].strip())
                if run_dir.exists():
                    return run_dir, fout

        if result.returncode != 0 and not fout:
            for line in result.stdout.splitlines():
                if "[FATAL]" in line:
                    fout = line.strip()
                    break
            if not fout:
                fout = (result.stdout[-1500:] + "\n" + result.stderr[-300:]).strip()

    doc_id = anon_path.stem.replace("_anonymized", "")
    if _RUNS_DIR.exists():
        candidates = sorted(
            [d for d in _RUNS_DIR.iterdir()
             if d.is_dir() and d.name.startswith(doc_id)],
            reverse=True,
        )
        if candidates:
            return candidates[0], fout

    if not fout:
        fout = "Run-map niet aangemaakt — mogelijk is CAPS mislukt."
    return None, fout
# ---------------------------------------------------------------------------
# Data laden (ongewijzigd)
# ---------------------------------------------------------------------------

def _list_runs() -> list[pathlib.Path]:
    if not _RUNS_DIR.exists():
        return []
    dirs = sorted(
        (d for d in _RUNS_DIR.iterdir() if d.is_dir()),
        reverse=True,
    )
    valid = []
    for d in dirs:
        fb_path = d / "feedback_result.json"
        if not fb_path.exists():
            continue
        try:
            data = json.loads(fb_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and not data.get("skipped"):
                valid.append(d)
        except (OSError, json.JSONDecodeError):
            pass
    return valid


def _load_json(path: pathlib.Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return None if (isinstance(data, dict) and data.get("skipped")) else data
    except (OSError, json.JSONDecodeError):
        return None

# ---------------------------------------------------------------------------
# Gedeelde render-helpers (ongewijzigd)
# ---------------------------------------------------------------------------

def _render_evidence(items: list[dict]) -> None:
    for item in items[:3]:
        signal = item.get("signal_class", "")
        badge = _SIGNAL_LABEL.get(signal, signal)
        page = item.get("page_no", "?")
        focused = (item.get("focused_excerpt") or "").strip()
        excerpt = (item.get("excerpt") or "").strip()
        text = focused if focused else excerpt
        if not text:
            continue
        st.markdown(
            f"<div class='hs-evidence'>"
            f"<span style='color:#546e7a;'>p.{page} · {badge}</span><br>"
            f"<em>{text[:300]}</em></div>",
            unsafe_allow_html=True,
        )
