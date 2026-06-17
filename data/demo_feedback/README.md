# demo_feedback — vooraf gegenereerde feedback (GEEN pipeline, GEEN LLM)

De app (`app.py`) draait in **demo-modus**: als de docent een PDF uploadt en op
"Genereer formatieve feedback" klikt, wordt er **geen** pipeline gedraaid. In
plaats daarvan laadt de app de vooraf gegenereerde JSON's uit de submap die bij
de PDF-bestandsnaam hoort.

## Hoe de matching werkt
De PDF-bestandsnaam wordt gemapt op een submap via `_PDF_TO_DEMO` in `app.py`:

| Geüploade PDF                          | Submap hieronder                      |
|----------------------------------------|---------------------------------------|
| `Requirement_student1.pdf`             | `Requirement_student1/`               |
| `Requirements_Engineering_student6.pdf`| `Requirements_Engineering_student6/`  |

Staat een PDF niet in `_PDF_TO_DEMO`, dan zoekt de app een submap met dezelfde
naam als de PDF zonder `.pdf`.

## Wat moet in elke submap staan
De app leest deze bestandsnamen (exact zo benoemen):

```
<submap>/
    feedback_result.json          ← VERPLICHT (student- én docentweergave)
    merged_feedback_input.json    ← VERPLICHT (docentweergave: sterktes/evidence/stoplicht)
    quality_diagnostics.json      ← optioneel (alleen ter referentie)
    handoff.json                  ← optioneel (alleen ter referentie)
```

## Plaatsings-/hernoem-instructies

**Requirement_student1/** (rood — Eunice, doc_id 96f07675)
- `feedback_result_Eunice_red.json`  → hernoem naar `feedback_result.json`
- de 96f07675-merged                 → `merged_feedback_input.json`
- `quality_diagnostics (1).json`     → `quality_diagnostics.json`
- de 96f07675-handoff                → `handoff.json`

**Requirements_Engineering_student6/** (geel — doc_id 2f4d9d91)
- `feedback_result (3).json`         → `feedback_result.json`
- `merged_feedback_input (1).json`   → `merged_feedback_input.json`
- `quality_diagnostics (2).json`     → `quality_diagnostics.json`
- `handoff (1).json`                 → `handoff.json`

## Meer PDF's toevoegen (later)
1. Maak een nieuwe submap met de juiste JSON's (zelfde bestandsnamen).
2. Voeg een regel toe aan `_PDF_TO_DEMO` in `app.py`, of geef de submap dezelfde
   naam als de PDF (zonder `.pdf`).
