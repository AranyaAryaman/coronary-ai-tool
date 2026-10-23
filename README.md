# Coronary AI tool — retinal microvasculature and ACS severity

AI component of the MS (Ophthalmology) thesis *"Evaluation of retinal microvasculature and its association
with severity of disease in acute coronary syndrome"* (Maulana Azad Medical College & Guru Nanak Eye Centre,
with GIPMER Cardiology and IIIT-Delhi).

**Question:** do the retinal arteriolar-to-venular ratio (AVR), central retinal arteriolar equivalent (CRAE) and
central retinal venular equivalent (CRVE) predict the angiographic severity of acute coronary syndrome
(SYNTAX score)?

> Research prototype. Not a medical device and not for clinical decision-making.

## Contents

| Path | What it is |
|---|---|
| `acs_ai.py` | Data cleaning (eye → patient level), feature definitions, model grid, nested cross-validation |
| `run_analysis.py` | Full analysis: descriptive tables, correlations, model comparison, permutation test, SYNTAX regression, SHAP / permutation / LASSO-stability feature ranking, figures, and the app models |
| `outputs/REPORT.md` | Generated results report with tables, figures and draft methods text |
| `outputs/tables/`, `outputs/figures/` | Thesis-ready CSV tables and 300-dpi PNG figures |
| `models/` | Models served by the app (trained on AVR, CRAE, CRVE) and their cross-validated performance |
| `app.py` | Streamlit app: enter CRAE / CRVE per eye → predicted SYNTAX category |

## Design choices

- **Patient-level analysis.** The workbook has one row per eye. Both eyes are averaged so each patient is one
  observation; treating eyes as independent patients leaks information between training and test folds and
  inflates accuracy (demonstrated in section 6 of the report).
- **Outcomes.** Almost all patients have SYNTAX ≤ 22, so the conventional low/intermediate/high categories cannot be
  used. Primary outcome: SYNTAX > 8 (cohort median); sensitivity analysis: SYNTAX ≥ 11 (upper quartile);
  SYNTAX is also modelled as a continuous score.
- **Predictor sets.** AVR/CRAE/CRVE (primary) compared with all retinal parameters including OCTA, clinical
  risk factors alone, and AVR/CRAE/CRVE + clinical risk factors. Medications, ECG, echo and angiography are not
  used as predictors.
- **Validation.** Repeated nested cross-validation (outer 5-fold × 10, inner 5-fold grid search), bootstrap 95 % CI
  for AUC, label-permutation test. Reporting follows TRIPOD+AI.

## Reproducing the analysis

The study workbook contains identifiable patient data and is **not** in this repository (see `.gitignore`).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-analysis.txt
.venv/bin/python run_analysis.py "path/to/study_workbook.xlsx"   # ~15 min on a laptop
.venv/bin/streamlit run app.py
```

## Deploying the app (Streamlit Community Cloud, free)

1. Sign in at <https://share.streamlit.io> with the GitHub account that owns this repository.
2. **Create app** → choose this repository, branch `main`, main file `app.py`, Python 3.12.
3. Deploy. The app installs only `requirements.txt` and loads the pre-trained models in `models/`.
