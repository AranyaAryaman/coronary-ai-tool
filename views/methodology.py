"""'How we built it' page: the study question, data, pipeline, results and conclusions.

Everything shown here is read from the aggregate outputs in outputs/ — no patient-level data.
"""
import json
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent.parent
TAB, FIG = ROOT / "outputs" / "tables", ROOT / "outputs" / "figures"
summary = json.loads((TAB / "cohort_summary.json").read_text())
perf = pd.read_csv(TAB / "table3_model_performance.csv")
perm = pd.read_csv(TAB / "table4_permutation_test.csv")
corr = pd.read_csv(TAB / "table2_correlation_with_syntax.csv")
reg = pd.read_csv(TAB / "table5_syntax_regression.csv")
domain = pd.read_csv(TAB / "table7_ai_vs_domain_knowledge.csv")

OUTCOMES = {"syntax_gt8": "SYNTAX > 8 (median split)", "syntax_ge11": "SYNTAX ≥ 11 (upper quartile)"}
SET_COLORS = {"AVR/CRAE/CRVE": "#2a78d6", "All retinal (+ OCTA)": "#eb6834", "Clinical": "#1baf7a",
              "AVR/CRAE/CRVE + Clinical": "#eda100"}
n = summary["n_patients"]
core = perf[perf["Feature set"] == "AVR/CRAE/CRVE"]
best_core = core.loc[core.AUC.idxmax()]
min_perm_p = perm["Permutation p"].min()

st.title("How we built it")
st.caption("From a clinical question to a validated (and honestly reported) AI model")

st.info(
    f"**In one sentence:** in {n} patients with acute coronary syndrome, the retinal vessel calibre measures "
    f"AVR, CRAE and CRVE pointed in the direction the literature predicts, but they could not predict the "
    f"angiographic severity (SYNTAX score) better than chance (best cross-validated AUC {best_core.AUC:.2f}).",
    icon="💡",
)

# ------------------------------------------------------------------ 1
st.header("1 · The question")
c1, c2 = st.columns([3, 2])
c1.markdown(
    "The retina is the only place in the body where small arteries and veins can be seen directly and "
    "without an invasive procedure. Retinal vessels are about the same size as the coronary "
    "microvasculature (≈100–250 µm) and are exposed to the same risk factors, so narrowing of retinal "
    "arterioles and widening of venules have been linked to coronary heart disease in large cohorts "
    "(ARIC, the McGeechan meta-analysis).\n\n"
    "**Can the retina tell us how severe a patient's coronary disease is?**"
)
c2.markdown(
    "| | |\n|---|---|\n"
    "| **Predictors** | AVR, CRAE, CRVE |\n"
    "| **Outcome** | SYNTAX score (angiography) |\n"
    "| **Design** | Cross-sectional |\n"
    "| **Setting** | GNEC / MAMC & GIPMER, New Delhi |"
)
with st.expander("What do AVR, CRAE, CRVE and SYNTAX mean?"):
    st.markdown(
        "- **CRAE** — central retinal arteriolar equivalent: the summarised calibre of the six largest arterioles "
        "measured one disc diameter from the optic disc.\n"
        "- **CRVE** — central retinal venular equivalent: the same for the six largest venules.\n"
        "- **AVR** — arteriolar-to-venular ratio, CRAE ÷ CRVE. A lower AVR (narrow arterioles, wide venules) "
        "is the classic retinal marker of cardiovascular risk.\n"
        "- **SYNTAX score** — an angiographic score of how extensive and complex coronary artery lesions are. "
        "Conventionally ≤ 22 is low, 23–32 intermediate and ≥ 33 high."
    )

# ------------------------------------------------------------------ 2
st.header("2 · The data")
m = st.columns(4)
m[0].metric("Patients", n)
m[1].metric("Eyes", summary["n_eyes"])
m[2].metric("Age (mean ± SD)", f"{summary['age'][0]:.0f} ± {summary['age'][1]:.0f}")
m[3].metric("Male", f"{summary['male'] / n:.0%}")
m = st.columns(3)
for col, f in zip(m, ["AVR", "CRAE", "CRVE"]):
    mu, sd = summary["core_means"][f]
    col.metric(f"{f} (mean ± SD)", f"{mu:.2f} ± {sd:.2f}" if f == "AVR" else f"{mu:.1f} ± {sd:.1f}")

hist = pd.DataFrame({"SYNTAX score": list(summary["syntax"]["histogram"]),
                     "Patients": list(summary["syntax"]["histogram"].values())})
st.altair_chart(
    alt.Chart(hist, title="Distribution of SYNTAX scores").mark_bar(color="#2a78d6", cornerRadiusEnd=4)
    .encode(x=alt.X("SYNTAX score:N", sort=None), y="Patients:Q", tooltip=["SYNTAX score", "Patients"])
    .properties(height=220),
    width="stretch",
)
s = summary["syntax"]
st.markdown(
    f"Median SYNTAX score was **{s['median']:.0f}** (IQR {s['q1']:.0f}–{s['q3']:.1f}) and only "
    f"**{s['n_gt22']}** patient scored above 22. Almost the whole cohort sits in the conventional *low* "
    "category, so the standard low / intermediate / high groups could not be used. Instead we asked whether "
    f"the retina can separate patients **above vs below the cohort median** (SYNTAX > 8, "
    f"{summary['outcomes']['syntax_gt8']} patients) and in the **upper quartile** (SYNTAX ≥ 11, "
    f"{summary['outcomes']['syntax_ge11']} patients), and also tried to predict the score itself."
)

st.subheader("Cleaning and quality control")
st.markdown(
    "- The workbook has **one row per eye**. Both eyes share one SYNTAX score, so eyes were averaged into "
    "**one row per patient**; otherwise the model could 'recognise' a patient's second eye in the test set.\n"
    "- Yes/no fields coded 1/2 were recoded to 1/0; names were removed before any analysis.\n"
    "- An automatic consistency check compares the two eye rows of every patient. It flagged one patient "
    "whose two rows carried different SYNTAX scores; this is corrected provisionally and is being verified "
    "against the case record.\n"
    "- A duplicate scan found a few patients with identical vital signs and laboratory values, consistent "
    "with copy-and-paste during data entry; these are being re-checked. The retinal measurements had no "
    "duplicates."
)

# ------------------------------------------------------------------ 3
st.header("3 · The pipeline")
st.graphviz_chart("""
digraph {
  rankdir=LR; bgcolor="transparent";
  node [shape=box style="rounded,filled" fillcolor="#eef4fc" color="#2a78d6" fontname="Helvetica" fontsize=11];
  edge [color="#52514e"];
  a [label="Excel workbook\\n160 eyes"];
  b [label="Clean & check\\nrecode, de-identify"];
  c [label="Eye → patient\\nn = 80"];
  d [label="Statistics\\ncorrelations, group tests"];
  e [label="Nested CV\\n4 models × 4 predictor sets"];
  f [label="Chance check\\npermutation test"];
  g [label="Explain\\nSHAP · permutation · LASSO"];
  h [label="Compare with\\nliterature"];
  i [label="This app"];
  a -> b -> c -> d; c -> e -> f; e -> g -> h; e -> i;
}
""")
with st.expander("Why nested cross-validation?"):
    st.markdown(
        f"With only {n} patients there is no room for a separate test set, and choosing model settings on "
        "the same data used to measure accuracy makes results look better than they are. Nested "
        "cross-validation keeps these apart:\n\n"
        "1. **Outer loop** — split patients into 5 folds; each fold in turn is held out as unseen test data.\n"
        "2. **Inner loop** — on the remaining 4 folds only, a grid search chooses the model's settings.\n"
        "3. The tuned model predicts the held-out fold. Repeating the whole procedure 10 times with different "
        "random splits gives 50 test folds and a stable estimate.\n\n"
        "All splits are by patient, never by eye."
    )

st.subheader("Try it: how patients are split")
st.caption("Each square is one patient (illustration — positions are not real patients). Move the sliders to "
           "see which patients the model trains on, tunes on, and is tested on.")
c1, c2, c3 = st.columns(3)
repeat = c1.slider("Repeat", 1, 10, 1)
outer_fold = c2.slider("Outer fold (test)", 1, 5, 1)
inner_fold = c3.slider("Inner fold (tuning)", 1, 5, 1)
rng = np.random.default_rng(repeat)
outer = rng.permutation(n) % 5
role = np.where(outer == outer_fold - 1, "Test (held out)", "")
train_idx = np.where(role == "")[0]
inner = np.random.default_rng(100 + repeat * 10 + outer_fold).permutation(len(train_idx)) % 5
role[train_idx] = np.where(inner == inner_fold - 1, "Inner validation (tuning)", "Training")
grid_df = pd.DataFrame({"col": np.arange(n) % 16, "row": np.arange(n) // 16, "Role": role})
ROLE_COLORS = {"Training": "#2a78d6", "Inner validation (tuning)": "#eda100", "Test (held out)": "#eb6834"}
st.altair_chart(
    alt.Chart(grid_df).mark_square(size=380, opacity=1)
    .encode(x=alt.X("col:O", axis=None), y=alt.Y("row:O", axis=None),
            color=alt.Color("Role:N", scale=alt.Scale(domain=list(ROLE_COLORS), range=list(ROLE_COLORS.values())),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Role"])
    .properties(height=200),
    width="stretch",
)
counts = grid_df.Role.value_counts()
st.caption(" · ".join(f"{k}: {counts.get(k, 0)} patients" for k in ROLE_COLORS) +
           f" — over 10 repeats × 5 outer folds the model is tested **50 times**, every patient 10 times.")
st.markdown(
    "**Models compared:** LASSO logistic regression · random forest · gradient boosting · support vector "
    "machine.  \n**Predictor sets:** AVR/CRAE/CRVE (primary) · all retinal incl. OCTA · clinical risk factors "
    "only · AVR/CRAE/CRVE + clinical. Medications, ECG, echo and angiography were *not* used as inputs "
    "because they are consequences of severity."
)

# ------------------------------------------------------------------ 4
st.header("4 · What we found")
st.subheader("Simple correlation")
st.image(str(FIG / "scatter_core_vs_syntax.png"), width="stretch")
st.markdown("Lower AVR and CRAE went with higher SYNTAX scores, as expected, but the associations were weak "
            "and not statistically significant.")

c1, c2 = st.columns([2, 1])
method = c1.radio("Correlation", ["Spearman", "Pearson"], horizontal=True)
only_core = c2.toggle("Primary parameters only", value=False)
cd = corr[corr.Role == "Primary"] if only_core else corr
cd = cd.assign(r=cd["Spearman rho" if method == "Spearman" else "Pearson r"],
               p=cd[f"p ({method})"])
cd["Significance"] = np.where(cd.p < 0.05, "p < 0.05", "Not significant")
st.altair_chart(
    alt.Chart(cd, title=f"{method} correlation of each retinal parameter with SYNTAX")
    .mark_bar(cornerRadiusEnd=4, height=14)
    .encode(y=alt.Y("Retinal parameter:N", sort=alt.EncodingSortField("r", order="ascending"), title=None),
            x=alt.X("r:Q", title="Correlation coefficient", scale=alt.Scale(domain=[-0.4, 0.4])),
            color=alt.Color("Significance:N", scale=alt.Scale(domain=["p < 0.05", "Not significant"],
                                                              range=["#2a78d6", "#a8c8ef"]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Retinal parameter", "Role", alt.Tooltip("r:Q", format=".3f", title="r"),
                     alt.Tooltip("p:Q", format=".3f"),
                     alt.Tooltip("q (FDR, Spearman):Q", format=".3f", title="q (FDR)")])
    .properties(height=max(120, 26 * len(cd))),
    width="stretch",
)
st.caption("Bars left of zero: lower values with higher SYNTAX. After correcting for testing 15 parameters "
           "(false-discovery rate), no parameter remained significant.")

st.subheader("Machine-learning models")
outcome = st.segmented_control("Outcome", list(OUTCOMES), format_func=OUTCOMES.get, default="syntax_gt8")
outcome = outcome or "syntax_gt8"
sub = perf[perf.Outcome == outcome].copy()
sub["label"] = sub["Feature set"] + " · " + sub["Model"]
base = alt.Chart(sub).encode(
    y=alt.Y("label:N", sort=list(sub.label), title=None),
    color=alt.Color("Feature set:N", scale=alt.Scale(domain=list(SET_COLORS), range=list(SET_COLORS.values())),
                    legend=alt.Legend(orient="top", title=None, columns=2)),
    tooltip=["Feature set", "Model", alt.Tooltip("AUC:Q", format=".2f"),
             alt.Tooltip("AUC 95% CI low:Q", format=".2f", title="CI low"),
             alt.Tooltip("AUC 95% CI high:Q", format=".2f", title="CI high"),
             alt.Tooltip("Sensitivity:Q", format=".0%"), alt.Tooltip("Specificity:Q", format=".0%")],
)
chance = alt.Chart(pd.DataFrame({"x": [0.5]})).mark_rule(strokeDash=[4, 4], color="#8a8984").encode(x="x:Q")
chart = (chance + base.mark_rule(strokeWidth=2).encode(x=alt.X("AUC 95% CI low:Q", scale=alt.Scale(domain=[0.1, 0.9]),
                                                                title="AUC (95% CI) — dashed line = chance"),
                                                         x2="AUC 95% CI high:Q")
         + base.mark_circle(size=90, opacity=1, stroke="white", strokeWidth=1.5).encode(x="AUC:Q"))
st.altair_chart(chart.properties(height=420), width="stretch")
st.caption("Hover over a point for details. AUC 0.5 = coin toss, 1.0 = perfect separation.")

pc = perm[perm.Outcome == OUTCOMES[outcome]]
st.markdown(
    "**Is any model better than chance?** We shuffled the outcome labels 200 times and refitted the best "
    "model each time. If the real model is no better than models trained on shuffled labels, it has not "
    f"learned anything. Smallest permutation p-value across all models: **{min_perm_p:.2f}**, so none of "
    "the models beat chance."
)
nulls = [d for d in json.loads((TAB / "permutation_null.json").read_text()) if d["outcome"] == outcome]
pick = st.selectbox("Model", range(len(nulls)), format_func=lambda i: f"{nulls[i]['feature_set']} · {nulls[i]['model']}")
d = nulls[pick]
null_df = pd.DataFrame({"AUC": d["null"]})
hist_chart = alt.Chart(null_df).mark_bar(color="#c3c2b7", cornerRadiusEnd=3).encode(
    x=alt.X("AUC:Q", bin=alt.Bin(step=0.025), title="AUC", scale=alt.Scale(domain=[0.2, 0.85])),
    y=alt.Y("count():Q", title="Shuffled-label models"),
    tooltip=[alt.Tooltip("count():Q", title="Models")])
real = alt.Chart(pd.DataFrame({"AUC": [d["auc"]], "label": [f"Real model: AUC {d['auc']:.2f}, p = {d['p']:.2f}"]}))
st.altair_chart(
    (hist_chart + real.mark_rule(color="#eb6834", strokeWidth=3).encode(x="AUC:Q", tooltip=["label"])
     + real.mark_text(align="left", dx=6, dy=-120, color="#eb6834", fontWeight="bold").encode(x="AUC:Q", text="label"))
    .properties(height=260, title="Real model vs 200 models trained on shuffled labels"),
    width="stretch",
)
st.caption("If the orange line sat far to the right of the grey bars, the model would have learned something "
           "real. It sits inside them.")
with st.expander("Permutation test table"):
    st.dataframe(pc.drop(columns="Outcome"), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.3f")
                                for c in ["AUC (fixed params)", "Mean AUC, permuted labels", "Permutation p"]})

st.subheader("ROC curves")
roc = [r for r in json.loads((TAB / "roc_curves.json").read_text()) if r["outcome"] == outcome]
roc_df = pd.concat([pd.DataFrame({"1 − specificity": r["fpr"], "Sensitivity": r["tpr"],
                                  "Feature set": r["feature_set"],
                                  "Model": f"{r['feature_set']}: {r['model']} (AUC {r['auc']:.2f})"}) for r in roc])
sel = alt.selection_point(fields=["Feature set"], bind="legend")
st.altair_chart(
    (alt.Chart(pd.DataFrame({"x": [0, 1], "y": [0, 1]})).mark_line(strokeDash=[4, 4], color="#8a8984")
     .encode(x="x:Q", y="y:Q")
     + alt.Chart(roc_df).mark_line(strokeWidth=2, interpolate="step-after")
     .encode(x="1 − specificity:Q", y="Sensitivity:Q",
             color=alt.Color("Feature set:N", scale=alt.Scale(domain=list(SET_COLORS), range=list(SET_COLORS.values())),
                             legend=alt.Legend(orient="bottom", title="Click to highlight", columns=2)),
             opacity=alt.condition(sel, alt.value(1), alt.value(0.15)), tooltip=["Model"])
     .add_params(sel))
    .properties(height=380, title="Best model per predictor set (dashed = chance)"),
    width="stretch",
)

with st.expander("Predicting the SYNTAX score as a number"):
    st.markdown("Regression models were no better than predicting the cohort average for everyone "
                "(R² ≤ 0, mean absolute error not lower than the baseline):")
    st.dataframe(reg, hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="%.2f") for c in reg.columns[2:]})

st.subheader("What did the AI pay attention to?")
rank = pd.read_csv(TAB / f"table6_feature_ranking_{outcome}.csv")
RANKERS = {"Mean |SHAP|": "SHAP — average impact on each prediction",
           "Permutation ΔAUC": "Permutation — AUC lost when the feature is scrambled",
           "LASSO selection freq.": "LASSO stability — % of 500 resamples where the feature was kept"}
ranker = st.radio("Ranking method", list(RANKERS), format_func=lambda k: RANKERS[k].split(" — ")[0], horizontal=True)
st.caption(RANKERS[ranker])
st.altair_chart(
    alt.Chart(rank).mark_bar(cornerRadiusEnd=4, height=14)
    .encode(y=alt.Y("Feature:N", sort=alt.EncodingSortField(ranker, order="descending"), title=None),
            x=alt.X(f"{ranker}:Q", title=ranker),
            color=alt.Color("Type:N", scale=alt.Scale(domain=["Retinal", "Clinical"], range=["#2a78d6", "#1baf7a"]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Feature", "Type", alt.Tooltip(f"{ranker}:Q", format=".3f"), "SHAP direction", "Rank (SHAP)"])
    .properties(height=26 * len(rank)),
    width="stretch",
)
with st.expander("SHAP beeswarm (each dot is a patient)"):
    st.markdown("How much each input pushed a prediction up or down; colour shows whether the value was high "
                "(red) or low (blue).")
    st.image(str(FIG / f"shap_{outcome}.png"), width="stretch")

st.subheader("Does the AI agree with the literature?")
st.dataframe(domain, hide_index=True, width="stretch",
             column_config={"Spearman rho with SYNTAX": st.column_config.NumberColumn(format="%.3f")})
avr_rank = int(domain.loc[domain["Retinal parameter"].str.startswith("AVR"), domain.columns[4]].iloc[0])
st.success(
    f"The model ranked **AVR #{avr_rank}** of {len(rank)} inputs, and the direction of all three retinal "
    "measures matched published studies: lower AVR and CRAE, and higher CRVE, with more severe disease. "
    "The pattern is biologically plausible; the cohort is too small and too homogeneous for it to be "
    "predictive.",
    icon="✅",
)

# ------------------------------------------------------------------ 5
st.header("5 · Why the model is not predictive, and what next")
c1, c2 = st.columns(2)
c1.markdown(
    "**Likely reasons**\n"
    f"- Narrow severity range: {s['n_gt22']} of {n} patients had SYNTAX > 22.\n"
    f"- Small sample ({n} patients) relative to the subtle effect sizes reported in the literature.\n"
    "- Retinal calibre reflects long-term microvascular change; SYNTAX measures large-artery lesions.\n"
    "- Measurement noise and data-entry issues still under verification."
)
c2.markdown(
    "**Next steps**\n"
    "- Verify flagged records and re-run (the pipeline is fully reproducible).\n"
    "- Recruit patients across the full SYNTAX range, including intermediate and high scores.\n"
    "- External validation in a second cohort.\n"
    "- Deep learning directly on fundus photographs once larger image sets are available."
)
st.markdown(
    "**Reporting standard:** TRIPOD+AI. **Code and full report:** the project repository "
    "(`outputs/REPORT.md`)."
)
st.page_link("views/predictor.py", label="Try the predictor", icon="🔬")
