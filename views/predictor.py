"""Predictor page: SYNTAX-score category from retinal AVR, CRAE and CRVE."""
import json
from pathlib import Path

import altair as alt
import joblib
import numpy as np
import pandas as pd
import streamlit as st

MODELS = Path(__file__).parent.parent / "models"
meta = json.loads((MODELS / "metadata.json").read_text())
models = {k: joblib.load(MODELS / f"{k}.joblib") for k in meta["models"]}

st.title("Retinal vessel calibre → coronary severity")
st.caption("Research prototype · MS (Ophthalmology) thesis, MAMC / GNEC, New Delhi")

st.warning(
    f"**Not for clinical use.** Exploratory model trained on {meta['n_patients']} patients with acute coronary "
    "syndrome and evaluated only by internal cross-validation. Predictions are uncertain and have not been "
    "externally validated.",
    icon="⚠️",
)
st.page_link("views/methodology.py", label="See how this tool was built and what the study found", icon="📖")

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

st.subheader("Explore the model")
st.caption("How the prediction changes as the vessel calibres change. The orange marker is the patient entered above.")
which = st.segmented_control("Outcome", list(meta["models"]), default=list(meta["models"])[0],
                             format_func=lambda k: meta["models"][k]["label"]) or list(meta["models"])[0]
model = models[which]
crae_now, crve_now, avr_now = (float(x[0][meta["features"].index(f)]) for f in ("CRAE", "CRVE", "AVR"))
lo_a, _, hi_a = meta["eye_ranges"]["CRAE"]
lo_v, _, hi_v = meta["eye_ranges"]["CRVE"]


def predict(crae, crve):
    crae, crve = np.asarray(crae, float), np.asarray(crve, float)
    feats = {"AVR": crae / crve, "CRAE": crae, "CRVE": crve}
    return model.predict_proba(np.column_stack([feats[f] for f in meta["features"]]))[:, 1]


tab1, tab2 = st.tabs(["What-if: change AVR", "Risk map: CRAE × CRVE"])
with tab1:
    crae_sweep = np.linspace(lo_a, hi_a, 80)
    curve = pd.DataFrame({"AVR": crae_sweep / crve_now, "CRAE": crae_sweep,
                          "Probability": predict(crae_sweep, np.full_like(crae_sweep, crve_now))})
    now = pd.DataFrame({"AVR": [avr_now], "Probability": [float(predict([crae_now], [crve_now])[0])]})
    st.altair_chart(
        (alt.Chart(curve).mark_line(color="#2a78d6", strokeWidth=2)
         .encode(x=alt.X("AVR:Q", scale=alt.Scale(zero=False)),
                 y=alt.Y("Probability:Q", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
                 tooltip=[alt.Tooltip("AVR:Q", format=".2f"), alt.Tooltip("CRAE:Q", format=".1f"),
                          alt.Tooltip("Probability:Q", format=".0%")])
         + alt.Chart(pd.DataFrame({"y": [meta["models"][which]["prevalence"]]}))
         .mark_rule(strokeDash=[4, 4], color="#8a8984").encode(y="y:Q")
         + alt.Chart(now).mark_point(size=140, filled=True, color="#eb6834", stroke="white", strokeWidth=2)
         .encode(x="AVR:Q", y="Probability:Q", tooltip=[alt.Tooltip("Probability:Q", format=".0%")]))
        .properties(height=300, title=f"CRVE fixed at {crve_now:.1f}; CRAE varied (dashed = cohort prevalence)"),
        width="stretch",
    )
with tab2:
    ga, gv = np.meshgrid(np.linspace(lo_a, hi_a, 30), np.linspace(lo_v, hi_v, 30))
    heat = pd.DataFrame({"CRAE": ga.ravel(), "CRVE": gv.ravel()})
    heat["AVR"] = heat.CRAE / heat.CRVE
    heat["Probability"] = predict(heat.CRAE, heat.CRVE)
    step_a, step_v = (hi_a - lo_a) / 29, (hi_v - lo_v) / 29
    heat["CRAE2"], heat["CRVE2"] = heat.CRAE + step_a, heat.CRVE + step_v
    st.altair_chart(
        (alt.Chart(heat).mark_rect()
         .encode(x=alt.X("CRAE:Q", scale=alt.Scale(domain=[lo_a, hi_a + step_a], nice=False)), x2="CRAE2:Q",
                 y=alt.Y("CRVE:Q", scale=alt.Scale(domain=[lo_v, hi_v + step_v], nice=False)), y2="CRVE2:Q",
                 color=alt.Color("Probability:Q", scale=alt.Scale(scheme="blues", domain=[0, 1]),
                                 legend=alt.Legend(format="%")),
                 tooltip=[alt.Tooltip("CRAE:Q", format=".1f"), alt.Tooltip("CRVE:Q", format=".1f"),
                          alt.Tooltip("AVR:Q", format=".2f"), alt.Tooltip("Probability:Q", format=".0%")])
         + alt.Chart(pd.DataFrame({"CRAE": [crae_now], "CRVE": [crve_now]}))
         .mark_point(size=160, filled=True, color="#eb6834", stroke="white", strokeWidth=2)
         .encode(x="CRAE:Q", y="CRVE:Q"))
        .properties(height=360, title="Predicted probability across the range of calibres seen in the study"),
        width="stretch",
    )
st.caption("A model with real predictive power would show a clear gradient. Patchy or flat maps mean the model "
           "has found little consistent pattern, which matches the cross-validation results.")

with st.expander("About the model"):
    st.markdown(
        "- **Inputs:** AVR, CRAE and CRVE averaged over both eyes.\n"
        "- **Outcomes:** SYNTAX score > 8 (cohort median) and ≥ 11 (upper quartile). Very few patients in the "
        "cohort had SYNTAX > 22, so the conventional low / intermediate / high categories could not be modelled.\n"
        "- **Validation:** repeated nested 5-fold cross-validation at patient level; see the "
        "*How we built it* page for the full process and results.\n"
        "- **Data:** no patient data is stored in this app or its repository."
    )
