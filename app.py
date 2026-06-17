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

# Demo-feedback: vooraf gegenereerde output per PDF (GEEN pipeline, GEEN LLM).
# Elke submap onder _DEMO_DIR bevat minimaal feedback_result.json en
# merged_feedback_input.json. _PDF_TO_DEMO mapt de geüploade PDF-bestandsnaam op
# de bijbehorende submap, zodat de juiste feedback verschijnt bij "Genereer".
_DEMO_DIR = _PROJECT_ROOT / "data" / "demo_feedback"
_PDF_TO_DEMO: dict[str, str] = {
    "Requirement_student1.pdf":              "Requirement_student1",
    "Requirements_Engineering_student6.pdf": "Requirements_Engineering_student6",
}

# Map met de geanonimiseerde documenten (per doc_id), voor de download-knop.
_ANONYMIZED_DIR = _PROJECT_ROOT / "data" / "anonymized"

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
    """Beschikbare feedback-runs voor de studentweergave.

    Scant zowel de echte pipeline-runs (data/full_pipeline_runs/) als de
    vooraf gegenereerde demo-feedback (data/demo_feedback/), zodat de student de
    feedback ziet die de docent net via een upload toonde. Een map telt mee als
    die een bruikbare feedback_result.json bevat.
    """
    valid = []
    for base in (_RUNS_DIR, _DEMO_DIR):
        if not base.exists():
            continue
        for d in sorted((d for d in base.iterdir() if d.is_dir()), reverse=True):
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


def _anonymized_path(doc_id: str) -> pathlib.Path | None:
    """Zoek het geanonimiseerde document <doc_id>_anonymized.json onder data/anonymized/.

    De bestanden staan in subfolders (Goed/Voldoende/onvoldoende), dus we zoeken
    recursief. Geeft None wanneer er niets matcht.
    """
    if not doc_id or not _ANONYMIZED_DIR.exists():
        return None
    return next(_ANONYMIZED_DIR.rglob(f"{doc_id}_anonymized.json"), None)

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


# Studentweergave — verbeterde opmaak
# ---------------------------------------------------------------------------

def _render_student_view(fb: dict, run_dir: pathlib.Path) -> None:
    stoplight = fb.get("stoplight", "green")
    niveau = _NIVEAU_LABEL.get(stoplight, stoplight)
    badge = _STOPLIGHT_BADGE.get(stoplight, "")
    css_class = f"stoplight-{stoplight}"

    st.markdown(
        f"<div class='stoplight-banner {css_class}'>"
        f"<span style='font-size:1.6rem;'>{badge}</span>"
        f"<span>{niveau}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("<div class='section-header'>Samenvatting</div>", unsafe_allow_html=True)
    st.write(fb.get("student_samenvatting", ""))

    st.markdown("<div class='section-header'>Wat wordt er verwacht?</div>", unsafe_allow_html=True)
    st.write(fb.get("feed_up", ""))

    st.markdown("<div class='section-header'>Feedback per criterium</div>", unsafe_allow_html=True)

    feedback_entries = fb.get("feedback", [])
    for entry in feedback_entries:
        key = entry.get("criterium", "")
        label = _CRITERION_LABELS.get(key, key)
        observatie = entry.get("observatie", "")

        # Haal eventueel aangepaste feedback en opmerkingen van de docent op
        edit_key = f"edit_{key}_{run_dir.name}"
        notes_key = f"notes_{key}_{run_dir.name}"

        feedback_tekst = st.session_state.get(edit_key, observatie)
        opmerking = st.session_state.get(notes_key, "")

        with st.expander(f"{label}", expanded=False):
            st.markdown(
                f"<div class='student-feedback-block'>{feedback_tekst}</div>",
                unsafe_allow_html=True,
            )

            if opmerking:
                st.markdown(
                    f"<div style='margin-top:10px;padding:10px 14px;"
                    f"background:#fff8e1;border-left:3px solid #f9a825;"
                    f"border-radius:6px;font-size:0.88rem;color:#555;'>"
                    f"<strong>Opmerking van de docent:</strong><br>"
                    f"{opmerking}</div>",
                    unsafe_allow_html=True,
                )

    st.markdown(
        "<div class='section-header'>Aanbevelingen voor verbetering</div>",
        unsafe_allow_html=True,
    )

    for tip in fb.get("feed_forward", []):
        st.markdown(f"- {tip}")

    taalgebruik = fb.get("taalgebruik", "")
    if taalgebruik:
        st.markdown("<div class='section-header'>Taalgebruik</div>", unsafe_allow_html=True)
        st.write(taalgebruik)

    st.divider()
    st.caption(fb.get("disclaimer", ""))



# Docentweergave — verbeterde opmaak
# Logica ongewijzigd, alleen layout & stijl aangepast
# ---------------------------------------------------------------------------

def _render_docent_view(
    fb: dict,
    merged: dict | None,
    run_dir: pathlib.Path,
) -> None:
    stoplight = fb.get("stoplight", "green")
    badge = _STOPLIGHT_BADGE.get(stoplight, "")
    css_class = f"stoplight-{stoplight}"

    stoplight_nl = _STOPLIGHT_NL.get(stoplight, stoplight.capitalize())

    st.markdown(
        f"<div class='stoplight-banner {css_class}'>"
        f"<span style='font-size:1.6rem;'>{badge}</span>"
        f"<span>Stoplicht: {stoplight_nl}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.write(fb.get("docent_toelichting", ""))

    if merged:
        extra = merged.get("criteria_requiring_extra_review", [])
        if extra:
            extra_labels = [_CRITERION_LABELS.get(k, k) for k in extra]
            st.warning(f"**Extra docentfocus:** {', '.join(extra_labels)}")

    # ── Feedback per criterium ──────────────────────────────────────────────
    st.markdown(
        "<div class='section-header'>Feedback per criterium</div>",
        unsafe_allow_html=True,
    )

    feedback_by_key = {
        entry.get("criterium"): entry
        for entry in fb.get("feedback", [])
        if isinstance(entry, dict)
    }

    merged_criteria = (merged or {}).get("criteria", {})
    criteria_keys = list(_CRITERION_LABELS.keys())

    for key in criteria_keys:
        crit_fb = feedback_by_key.get(key, {})
        crit_merged = merged_criteria.get(key, {})

        label = _CRITERION_LABELS.get(key, key)
        manual = crit_merged.get("manual_review", False)
        observatie = crit_fb.get("observatie", "")

        prefix = "! " if manual else ""

        with st.expander(f"{prefix}{label}", expanded=True):

            # Aandachtswaarschuwing
            if manual:
                reasons = crit_merged.get("manual_review_reason", [])
                if reasons:
                    st.markdown(
                        f"<span style='color:#e65100;font-size:0.82rem;'>"
                        f"{' · '.join(reasons)}</span>",
                        unsafe_allow_html=True,
                    )

            # Sterktes / verbeterpunten
            strengths = crit_merged.get("qwen_strengths", [])
            weaknesses = crit_merged.get("qwen_weaknesses", [])

            if strengths or weaknesses:
                col_strengths, col_weaknesses = st.columns(2)

                if strengths:
                    with col_strengths:
                        st.markdown("**Sterktes**")
                        for strength in strengths:
                            st.markdown(
                                f"<span style='font-size:0.85rem;'>+ {strength}</span>",
                                unsafe_allow_html=True,
                            )

                if weaknesses:
                    with col_weaknesses:
                        st.markdown("**Verbeterpunten**")
                        for weakness in weaknesses:
                            st.markdown(
                                f"<span style='font-size:0.85rem;'>- {weakness}</span>",
                                unsafe_allow_html=True,
                            )

            # Ontbrekende signalen
            missing = crit_merged.get("missing_signals", [])
            if missing:
                st.markdown(
                    f"<span style='font-size:0.82rem;color:#c62828;'>"
                    f"<strong>Ontbrekende signalen:</strong> {', '.join(missing)}"
                    f"</span>",
                    unsafe_allow_html=True,
                )

            # Evidence
            evidence = crit_merged.get("evidence_items", [])
            if evidence:
                st.markdown("**Evidence**")
                _render_evidence(evidence)

            st.divider()

            # ── Bewerkbaar feedbackveld ─────────────────────────────────────
            edit_key = f"edit_{key}_{run_dir.name}"

            if edit_key not in st.session_state:
                st.session_state[edit_key] = observatie

            st.markdown("**Feedback (aanpasbaar)**")
            st.text_area(
                label="feedback_text",
                value=st.session_state[edit_key],
                key=edit_key,
                height=130,
                label_visibility="collapsed",
            )

            # ── Opmerkingenveld ─────────────────────────────────────────────
            notes_key = f"notes_{key}_{run_dir.name}"

            st.markdown(
                "<div class='notes-label'>Eigen opmerkingen voor de student</div>",
                unsafe_allow_html=True,
            )

            st.text_area(
                label="notes_text",
                key=notes_key,
                height=70,
                placeholder="Voeg hier aanvullende opmerkingen toe...",
                label_visibility="collapsed",
            )

    # ── Aanbevelingen ───────────────────────────────────────────────────────
    st.markdown(
        "<div class='section-header'>Aanbevelingen voor verbetering</div>",
        unsafe_allow_html=True,
    )

    for tip in fb.get("feed_forward", []):
        st.markdown(f"- {tip}")

    taalgebruik = fb.get("taalgebruik", "")
    if taalgebruik:
        st.markdown("<div class='section-header'>Taalgebruik</div>", unsafe_allow_html=True)
        st.write(taalgebruik)

    st.divider()
    st.caption(fb.get("disclaimer", ""))

    # ── Verstuur alle feedback naar student ─────────────────────────────────
    sent_all_key = f"sent_all_{run_dir.name}"

    st.markdown("<div class='send-btn-wrapper'>", unsafe_allow_html=True)

    if st.button(
        "Verstuur feedback naar student",
        key=sent_all_key,
        type="primary",
        use_container_width=True,
    ):
        st.session_state[f"confirm_{run_dir.name}"] = True
        # Maak deze run pas zichtbaar in de studentweergave na het versturen.
        st.session_state.setdefault("sent_runs", set()).add(str(run_dir))

    st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.get(f"confirm_{run_dir.name}"):
        st.success("✓ Feedback verstuurd naar student")

    st.divider()

    # ── Downloads ───────────────────────────────────────────────────────────
    st.markdown("<div class='section-header'>Downloads</div>", unsafe_allow_html=True)

    col_a, col_b = st.columns(2)

    # Geanonimiseerd document: haal het echte <doc_id>_anonymized.json uit
    # data/anonymized/. Valt terug op een eventueel input_copy.json in de run-map.
    doc_id = fb.get("document_id") or run_dir.name
    anon_path = _anonymized_path(doc_id) or (run_dir / "input_copy.json")
    anon_data = anon_path.read_bytes() if anon_path.exists() else b"{}"

    with col_a:
        st.download_button(
            label="Geanonimiseerd document (JSON)",
            data=anon_data,
            file_name=f"{doc_id}_anonymized.json",
            mime="application/json",
        )

    with col_b:
        fb_bytes = json.dumps(fb, indent=2, ensure_ascii=False).encode("utf-8")

        st.download_button(
            label="Feedback resultaat (JSON)",
            data=fb_bytes,
            file_name=f"{doc_id}_feedback_result.json",
            mime="application/json",
        )


# ---------------------------------------------------------------------------
# Demo-modus helpers


_PREFERRED_DEMO_DOC_ID = "35baf7d3"


def _find_demo_run() -> pathlib.Path | None:
    if not _RUNS_DIR.exists():
        return None

    all_runs = sorted(_RUNS_DIR.iterdir(), reverse=True)
    preferred: list[pathlib.Path] = []
    fallback: list[pathlib.Path] = []

    for run_dir in all_runs:
        if not run_dir.is_dir():
            continue

        fb_path = run_dir / "feedback_result.json"
        mg_path = run_dir / "merged_feedback_input.json"

        if not (fb_path.exists() and mg_path.exists()):
            continue

        try:
            fb = json.loads(fb_path.read_text(encoding="utf-8"))

            if isinstance(fb, dict) and not fb.get("skipped"):
                if run_dir.name.startswith(_PREFERRED_DEMO_DOC_ID):
                    preferred.append(run_dir)
                else:
                    fallback.append(run_dir)

        except (OSError, json.JSONDecodeError):
            pass

    if preferred:
        return preferred[0]

    if fallback:
        return fallback[0]

    return None


def _resolve_demo_run(pdf_name: str | None) -> pathlib.Path | None:
    """Map een geüploade PDF-bestandsnaam op de bijbehorende demo-map.

    Eerst exact op bestandsnaam via _PDF_TO_DEMO; valt anders terug op de
    PDF-stam als gelijknamige submap onder _DEMO_DIR. Een map telt alleen mee als
    die een feedback_result.json bevat. Vindt niets passends, dan None (geen
    stille terugval op een willekeurige run).
    """
    if not pdf_name:
        return None
    name = pathlib.Path(pdf_name).name
    folder = _PDF_TO_DEMO.get(name) or pathlib.Path(name).stem
    candidate = _DEMO_DIR / folder
    if (candidate / "feedback_result.json").exists():
        return candidate
    return None

# ---------------------------------------------------------------------------
# Upload-formulier — verbeterde opmaak
# ---------------------------------------------------------------------------

def _render_upload_form() -> None:
    st.title("Genereer formatieve feedback")

    st.divider()

    uploaded_pdf = st.file_uploader(
        "Student document (PDF)",
        type=["pdf"],
        help="Selecteer een PDF om te uploaden.",
    )

    st.divider()

    if uploaded_pdf is None:
        return

    pdf_name = uploaded_pdf.name
    anonymized = (
        st.session_state.get("anon_pdf") == pdf_name
        and st.session_state.get("anon_run")
    )

    # Stap 1 — uploaden + anonimiseren.
    if not anonymized:
        if st.button("Upload document", type="primary"):
            _anonymize_demo(pdf_name)
        return

    # Stap 2 — anonimisatie klaar: toon hoeveel namen zijn verwijderd + genereer-knop.
    count = st.session_state.get("anon_count", 0)
    st.success(
        f"✓ Document geanonimiseerd — {count} "
        f"{'naam' if count == 1 else 'namen'} verwijderd. "
        "Je kunt het geanonimiseerde document straks downloaden onder 'Downloads'."
    )
    if st.button("Genereer feedback", type="primary"):
        _show_feedback_demo()


def _removed_names_count(doc_id: str) -> int:
    """Aantal vervangen namen (mapping_count) uit het geanonimiseerde document."""
    path = _anonymized_path(doc_id)
    if path is None:
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("mapping_count") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0


def _anonymize_demo(pdf_name: str) -> None:
    """Stap 1 — toon de anonimisatie-animatie en bewaar het resultaat in de sessie.

    Geen echte pipeline: de bijbehorende demo-map wordt opgezocht en het aantal
    verwijderde namen uit het geanonimiseerde document gelezen.
    """
    anon_placeholder = st.empty()

    anon_placeholder.markdown(
        """
        <div class="thinking-indicator">
            <span class="thinking-icon">🔒</span>
            <span class="thinking-text">Document wordt geanonimiseerd</span>
            <div class="thinking-dots">
                <span></span>
                <span></span>
                <span></span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    run_dir = _resolve_demo_run(pdf_name)

    time.sleep(4)
    anon_placeholder.empty()

    if run_dir is None:
        for key in ("anon_pdf", "anon_count", "anon_run"):
            st.session_state.pop(key, None)
        st.error(
            f"Geen demo-feedback gevonden voor '{pdf_name}'. "
            "Voeg een map toe onder `data/demo_feedback/<pdf-naam-zonder-extensie>/` "
            "met daarin `feedback_result.json` en `merged_feedback_input.json`, "
            "of registreer de PDF in `_PDF_TO_DEMO` bovenin app.py."
        )
        return

    fb = _load_json(run_dir / "feedback_result.json") or {}
    st.session_state["anon_pdf"] = pdf_name
    st.session_state["anon_count"] = _removed_names_count(fb.get("document_id", ""))
    st.session_state["anon_run"] = str(run_dir)
    st.rerun()


def _show_feedback_demo() -> None:
    """Stap 2 — toon de AI-feedback-animatie en open de geanonimiseerde run."""
    run_dir = st.session_state.get("anon_run")
    if not run_dir:
        return

    thinking_placeholder = st.empty()

    thinking_placeholder.markdown(
        """
        <div class="thinking-indicator">
            <span class="thinking-icon">🤖</span>
            <span class="thinking-text">AI is aan het denken</span>
            <div class="thinking-dots">
                <span></span>
                <span></span>
                <span></span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    time.sleep(10)
    thinking_placeholder.empty()

    st.session_state.run_dir = run_dir
    for key in ("anon_pdf", "anon_count", "anon_run"):
        st.session_state.pop(key, None)
    st.rerun()

# ---------------------------------------------------------------------------
# Sidebar en hoofdloop


def main() -> None:
    _inject_css()

    if "run_dir" not in st.session_state:
        st.session_state.run_dir = None

    with st.sidebar:
        # ── Header ────
        st.markdown(
            "<div class='sidebar-header'>"
            "<div class='sidebar-logo'>INFIRFS</div>"
            "<div class='sidebar-subtitle'>Formatieve Feedback Assistent</div>"
            "</div>",
            unsafe_allow_html=True,
        )

        # ── Navigatie ───
        st.markdown(
            "<div class='sidebar-section-label'>Omgeving</div>",
            unsafe_allow_html=True,
        )

        role = st.radio(
            "Omgeving",
            options=["🎓  Docent", "📖  Student"],
            index=0,
            label_visibility="collapsed",
        )

        role = "Docent" if role.startswith("🎓") else "Student"

        # ── Docentacties ───
        if role == "Docent" and st.session_state.run_dir is not None:
            st.markdown(
                "<div class='sidebar-section-label'>Acties</div>",
                unsafe_allow_html=True,
            )

            if st.button("＋  Nieuw document", use_container_width=True):
                st.session_state.run_dir = None
                st.rerun()

        # ── Studentkeuze ──
        if role == "Student":
            st.markdown(
                "<div class='sidebar-section-label'>Feedback selecteren</div>",
                unsafe_allow_html=True,
            )

            # Toon alleen feedback die de docent deze sessie heeft verstuurd.
            sent_runs = st.session_state.get("sent_runs", set())
            runs = [r for r in _list_runs() if str(r) in sent_runs]

            if not runs:
                st.warning(
                    "Nog geen feedback beschikbaar.\n\n"
                    "De docent moet de feedback eerst versturen "
                    "(knop 'Verstuur feedback naar student')."
                )
                st.stop()

            runs_by_label = {run.name: run for run in runs}

            selected_label = st.selectbox(
                "Run",
                options=list(runs_by_label),
                index=0,
                label_visibility="collapsed",
            )

            st.session_state.run_dir = str(runs_by_label[selected_label])

        # ── Footer ───
        st.markdown(
            "<div style='position:fixed;bottom:20px;font-size:0.72rem;"
            "color:rgba(255,255,255,0.35);'>"
            "© Hogeschool Leiden — PROTOTYPE"
            "</div>",
            unsafe_allow_html=True,
        )

    # Docentflow
    if role == "Docent":
        if st.session_state.run_dir is None:
            _render_upload_form()
            return

        run_dir = pathlib.Path(st.session_state.run_dir)

        feedback = _load_json(run_dir / "feedback_result.json")
        merged = _load_json(run_dir / "merged_feedback_input.json")

        source_name = (
            (merged or {}).get("source_name")
            or (feedback or {}).get("document_id")
            or run_dir.name
        )

        st.title(f"Feedback — {source_name}")

        if feedback is None and merged is None:
            st.error("De pipeline heeft geen bruikbare output geproduceerd.")

            summary = _load_json(run_dir / "run_summary.json")

            if summary:
                st.json(summary)

            if st.button("Nieuw document uploaden"):
                st.session_state.run_dir = None
                st.rerun()

            return

        if feedback is None:
            st.warning(
                "De feedbacktekst kon niet worden gegenereerd. "
                "Hieronder staat de CAPS- en Qwen-diagnose."
            )
            _render_docent_view({}, merged, run_dir)
            return

        _render_docent_view(feedback, merged, run_dir)
        return

    # Studentflow
    run_dir = pathlib.Path(st.session_state.run_dir)

    feedback = _load_json(run_dir / "feedback_result.json")

    if feedback is None:
        st.error("Feedback niet gevonden.")
        return

    merged = _load_json(run_dir / "merged_feedback_input.json")

    source_name = (
        (merged or {}).get("source_name")
        or feedback.get("document_id", run_dir.name)
    )

    st.title(f"Feedback — {source_name}")

    _render_student_view(feedback, run_dir)


if __name__ == "__main__":
    main()