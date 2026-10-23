"""Streamlit research prototype: SYNTAX-score category from retinal AVR, CRAE and CRVE.

Run locally:  streamlit run app.py
"""
import json
from pathlib import Path

import joblib
import numpy as np
import streamlit as st

MODELS = Path(__file__).parent / "models"
meta = json.loads((MODELS / "metadata.json").read_text())
models = {k: joblib.load(MODELS / f"{k}.joblib") for k in meta["models"]}

st.set_page_config(page_title="Retina → SYNTAX", page_icon="👁️", layout="centered")
st.title("Retinal vessel calibre → coronary severity")
st.caption("Research prototype · MS (Ophthalmology) thesis, MAMC / GNEC, New Delhi")

st.warning(
    f"**Not for clinical use.** Exploratory model trained on {meta['n_patients']} patients with acute coronary "
    "syndrome and evaluated only by internal cross-validation. Predictions are uncertain and have not been "
    "externally validated.",
    icon="⚠️",
)

st.subheader("Fundus measurements")
st.write("Enter CRAE and CRVE (in the same units used in the study) measured at one disc diameter from the optic disc.")
both = st.toggle("Both eyes available", value=True)
eyes = ["Right eye", "Left eye"] if both else ["Eye"]
cols = st.columns(len(eyes))
values = []
for col, eye in zip(cols, eyes):
    with col:
        st.markdown(f"**{eye}**")
        lo, med, hi = meta["eye_ranges"]["CRAE"]
        crae = st.number_input("CRAE", min_value=1.0, value=float(med), step=1.0, key=f"crae_{eye}")
        lo, med, hi = meta["eye_ranges"]["CRVE"]
        crve = st.number_input("CRVE", min_value=1.0, value=float(med), step=1.0, key=f"crve_{eye}")
        avr = crae / crve
        st.metric("AVR (CRAE / CRVE)", f"{avr:.2f}")
        values.append({"AVR": avr, "CRAE": crae, "CRVE": crve})

# The models were trained on the per-patient mean of both eyes
x = np.array([[np.mean([v[f] for v in values]) for f in meta["features"]]])

out_of_range = [f for f in ("CRAE", "CRVE") if not meta["eye_ranges"][f][0] <= x[0][meta["features"].index(f)]
                <= meta["eye_ranges"][f][2]]
if out_of_range:
    lims = ", ".join(f"{f} {meta['eye_ranges'][f][0]:.0f}–{meta['eye_ranges'][f][2]:.0f}" for f in out_of_range)
    st.info(f"Input outside the range seen in the study cohort ({lims}); the prediction is an extrapolation.")

st.subheader("Predicted coronary severity")
for key, m in meta["models"].items():
    p = float(models[key].predict_proba(x)[0, 1])
    with st.container(border=True):
        c1, c2 = st.columns([1, 2])
        c1.metric(m["label"], f"{p:.0%}", help="Model probability that the patient is in this SYNTAX category")
        c2.write(
            f"Cohort prevalence: **{m['prevalence']:.0%}**  \n"
            f"Model: {m['model']}  \n"
            f"Cross-validated AUC **{m['auc']:.2f}** (95% CI {m['auc_ci'][0]:.2f}–{m['auc_ci'][1]:.2f}); "
            f"sensitivity {m['sensitivity']:.0%}, specificity {m['specificity']:.0%} at 50 % threshold"
        )
        if m["auc_ci"][0] <= 0.5:
            c2.caption("The confidence interval includes 0.5 (chance), so this output should not be interpreted "
                       "as evidence about an individual patient.")

with st.expander("About the model"):
    st.markdown(
        "- **Inputs:** AVR, CRAE and CRVE averaged over both eyes.\n"
        "- **Outcomes:** SYNTAX score > 8 (cohort median) and ≥ 11 (upper quartile). Very few patients in the "
        "cohort had SYNTAX > 22, so the conventional low / intermediate / high categories could not be modelled.\n"
        "- **Validation:** repeated nested 5-fold cross-validation at patient level; full methods and results "
        "are in `outputs/REPORT.md` in the repository.\n"
        "- **Data:** no patient data is stored in this app or its repository."
    )
