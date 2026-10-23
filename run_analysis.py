"""Full analysis pipeline: descriptive statistics, nested-CV model comparison,
feature ranking (SHAP / permutation / LASSO stability) and export of the models used by the app.

Usage:  python run_analysis.py "MC ANANYA 6.10.26.xlsx"
Outputs go to outputs/ (tables, figures, REPORT.md) and models/ (app models).
The input workbook and the patient-level CSV stay local (see .gitignore).
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import joblib
import matplotlib
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.base import clone
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LassoCV, LogisticRegression
from sklearn.metrics import brier_score_loss, mean_absolute_error, r2_score, roc_auc_score, roc_curve
from sklearn.model_selection import (GroupKFold, RepeatedKFold, RepeatedStratifiedKFold, StratifiedKFold,
                                     cross_val_predict, permutation_test_score)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import acs_ai as A

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import shap  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)

OUT = Path("outputs"); FIG = OUT / "figures"; TAB = OUT / "tables"; MODELS = Path("models"); DATA = Path("data")
for d in (FIG, TAB, MODELS, DATA):
    d.mkdir(parents=True, exist_ok=True)

# Validated categorical slots 1-3 (blue, orange, aqua) + neutral ink for reference marks
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
C4 = "#eda100"
SET_COLORS = {"AVR/CRAE/CRVE": C1, "All retinal (+ OCTA)": C2, "Clinical": C3, "AVR/CRAE/CRVE + Clinical": C4}
RANK_SET = "AVR/CRAE/CRVE + Clinical"
plt.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 300, "font.size": 9, "axes.edgecolor": INK2,
    "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "legend.frameon": False, "lines.linewidth": 2,
})

# Direction of association reported in the literature (protocol review):
# + = higher value with more severe disease, - = lower value with more severe disease
EXPECTED = {"AVR": "-", "CRAE": "-", "CRVE": "+"}

report: list[str] = []


def md_table(df: pd.DataFrame, floatfmt=".2f") -> str:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]):
            df[c] = df[c].map(lambda v: "" if pd.isna(v) else f"{v:{floatfmt}}")
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join(lines)


def fmt_p(p):
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def bh_fdr(p):
    p = np.asarray(p); n = len(p); order = np.argsort(p)
    q = np.empty(n); q[order] = np.minimum.accumulate((p[order] * n / np.arange(1, n + 1))[::-1])[::-1]
    return np.minimum(q, 1)


# ---------------------------------------------------------------- data
src = Path(sys.argv[1] if len(sys.argv) > 1 else "MC ANANYA 6.10.26.xlsx")
eyes = A.load_eyes(src)
pts = A.to_patients(eyes)
pts.to_csv(DATA / "patients_deidentified.csv", index=False)
features_all = list(A.RETINAL) + list(A.CLINICAL)
bins = [0, 5, 10, 15, 20, 25, 30]
summary = {
    "n_eyes": len(eyes), "n_patients": len(pts),
    "syntax": {"median": float(pts.SYNTAX.median()), "q1": float(pts.SYNTAX.quantile(.25)),
               "q3": float(pts.SYNTAX.quantile(.75)), "max": float(pts.SYNTAX.max()),
               "n_gt22": int((pts.SYNTAX > 22).sum()),
               "histogram": {f"{lo}–{hi}": int(((pts.SYNTAX >= lo) & (pts.SYNTAX < hi)).sum())
                             for lo, hi in zip(bins[:-1], bins[1:])}},
    "outcomes": {k: int(pts[k].sum()) for k in A.OUTCOMES},
    "core_means": {f: [float(pts[f].mean()), float(pts[f].std())] for f in A.CORE},
    "male": int(pts.Male.sum()), "age": [float(pts.Age.mean()), float(pts.Age.std())],
    "diabetes": int(pts.Diabetes.sum()), "hypertension": int(pts.Hypertension.sum()),
    "smoking": int(pts.Smoking.sum()), "provisional_fixes": len(A.PROVISIONAL_FIXES),
}
(TAB / "cohort_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
n = len(pts)
print(f"{len(eyes)} eyes, {n} patients")

report += [
    "# Retinal microvasculature and ACS severity — AI analysis report", "",
    "_Generated automatically by `run_analysis.py`. All numbers come from the study workbook._", "",
    "## 1. Cohort", "",
    f"- {len(eyes)} eyes from **{n} patients**; eye-level retinal parameters were averaged per patient "
    "so that each patient contributes one observation.",
    f"- SYNTAX score: median {pts.SYNTAX.median():.1f} (IQR {pts.SYNTAX.quantile(.25):.1f}–"
    f"{pts.SYNTAX.quantile(.75):.1f}), range {pts.SYNTAX.min():.0f}–{pts.SYNTAX.max():.1f}. "
    f"Only {(pts.SYNTAX > 22).sum()} patient(s) exceeded 22, so the conventional low/intermediate/high "
    "SYNTAX categories (≤22 / 23–32 / ≥33) could not be used.",
    f"- Outcomes modelled: SYNTAX > {A.SYNTAX_MEDIAN_CUT} (median split, n = {pts.syntax_gt8.sum()}/{n}), "
    f"SYNTAX ≥ {A.SYNTAX_Q3_CUT} (upper quartile, n = {pts.syntax_ge11.sum()}/{n}); SYNTAX was also "
    "modelled as a continuous score.",
    "- Primary predictors: **AVR, CRAE and CRVE**. OCTA parameters and clinical risk factors were analysed "
    "as comparison predictor sets.",
    *[f"- ⚠️ Provisional data correction, patient {pid}: {fx} (eye rows disagreed; confirm against case record)."
      for pid, fx in A.PROVISIONAL_FIXES.items()], "",
]

# ---------------------------------------------------------------- Table 1
rows = []
lo, hi = pts[pts.syntax_gt8 == 0], pts[pts.syntax_gt8 == 1]
for f in features_all:
    if f in A.BINARY_CLINICAL or f == "Male":
        a, b = int(lo[f].sum()), int(hi[f].sum())
        p = stats.fisher_exact([[a, len(lo) - a], [b, len(hi) - b]])[1]
        rows.append([A.LABELS[f], f"{a} ({a / len(lo):.0%})", f"{b} ({b / len(hi):.0%})", fmt_p(p), "Fisher"])
    else:
        def mi(s): return f"{s.median():.2f} [{s.quantile(.25):.2f}–{s.quantile(.75):.2f}]"
        p = stats.mannwhitneyu(lo[f].dropna(), hi[f].dropna()).pvalue
        rows.append([A.LABELS[f], mi(lo[f]), mi(hi[f]), fmt_p(p), "Mann–Whitney"])
t1 = pd.DataFrame(rows, columns=["Variable", f"SYNTAX ≤ 8 (n={len(lo)})", f"SYNTAX > 8 (n={len(hi)})", "p", "Test"])
t1.to_csv(TAB / "table1_by_syntax.csv", index=False)
report += ["## 2. Patient characteristics by SYNTAX group", "",
           "Continuous variables: median [IQR]; categorical: n (%).", "", md_table(t1), ""]

# ---------------------------------------------------------------- correlations
rows = []
for f in A.RETINAL:
    r1, p1 = stats.spearmanr(pts[f], pts.SYNTAX); r2, p2 = stats.pearsonr(pts[f], pts.SYNTAX)
    rows.append([f, A.LABELS[f], "Primary" if f in A.CORE else "OCTA / other", r1, p1, r2, p2])
corr = pd.DataFrame(rows, columns=["key", "Retinal parameter", "Role", "Spearman rho", "p (Spearman)",
                                   "Pearson r", "p (Pearson)"])
corr["q (FDR, Spearman)"] = bh_fdr(corr["p (Spearman)"])
corr.drop(columns="key").to_csv(TAB / "table2_correlation_with_syntax.csv", index=False)
fig, axes = plt.subplots(1, 3, figsize=(10, 3.2), sharey=True)
for ax, f in zip(axes, A.CORE):
    r = corr.set_index("key").loc[f]
    ax.scatter(pts[f], pts.SYNTAX, s=28, color=C1, edgecolor="white", linewidth=1, alpha=.9)
    b1, b0 = np.polyfit(pts[f], pts.SYNTAX, 1); xs = np.linspace(pts[f].min(), pts[f].max(), 50)
    ax.plot(xs, b0 + b1 * xs, color=INK2, lw=1.5)
    ax.set_xlabel(A.LABELS[f]); ax.set_title(f"rho = {r['Spearman rho']:.2f}, p = {fmt_p(r['p (Spearman)'])}",
                                             loc="left", fontsize=9, color=INK2)
axes[0].set_ylabel("SYNTAX score")
fig.suptitle("SYNTAX score against the primary retinal parameters (one dot per patient)", x=0.01, ha="left", fontsize=11)
fig.tight_layout(); fig.savefig(FIG / "scatter_core_vs_syntax.png", bbox_inches="tight"); plt.close(fig)
report += ["## 3. Correlation of retinal parameters with SYNTAX score", "",
           "Patient level; q = Benjamini–Hochberg false-discovery-rate-adjusted p across all 15 retinal parameters.", "",
           md_table(corr.drop(columns="key"), ".3f"), "", "![Scatter](figures/scatter_core_vs_syntax.png)", ""]

# ---------------------------------------------------------------- nested CV
grid = A.model_grid()
records, oof_store, params_store = [], {}, {}
for outcome in A.OUTCOMES:
    y = pts[outcome].values
    for set_name, feats in A.FEATURE_SETS.items():
        X = pts[feats].values
        for model_name, (est, g) in grid.items():
            oof, aucs, params = A.nested_cv(est, g, X, y)
            # Every metric is computed per repeat and then averaged, so point estimate and CI agree
            ci = np.mean([A.bootstrap_auc_ci(y, row, n_boot=300, seed=A.SEED + i) for i, row in enumerate(oof)], axis=0)
            thr = pd.DataFrame([A.threshold_metrics(y, row) for row in oof]).mean().to_dict()
            rec = {"Outcome": outcome, "Feature set": set_name, "Model": model_name,
                   "AUC": aucs.mean(), "AUC SD (repeats)": aucs.std(),
                   "AUC 95% CI low": ci[0], "AUC 95% CI high": ci[1],
                   "Brier": np.mean([brier_score_loss(y, row) for row in oof]), **thr}
            records.append(rec)
            oof_store[(outcome, set_name, model_name)] = oof[int(np.argsort(aucs)[len(aucs) // 2])]
            params_store[(outcome, set_name, model_name)] = A.most_common_params(params)
            print(f"{outcome:12s} {set_name:20s} {model_name:18s} AUC {aucs.mean():.3f} [{ci[0]:.2f}-{ci[1]:.2f}]")
res = pd.DataFrame(records)
res.to_csv(TAB / "table3_model_performance.csv", index=False)

best = res.loc[res.groupby(["Outcome", "Feature set"])["AUC"].idxmax()]
report += ["## 4. Model performance (repeated nested cross-validation)", "",
           "Outer loop: 5-fold stratified CV repeated 10 times; inner loop: 5-fold grid search on AUC. "
           "Each metric is computed on the out-of-fold predictions of each repeat and averaged over the 10 "
           "repeats; the 95% CI is the average of per-repeat patient-level bootstrap intervals. "
           "Sensitivity/specificity at probability 0.5 (all models are class-balanced). ROC curves show the "
           "median-AUC repeat.", "",
           "> With n = 80 and little true signal, cross-validated AUC can fall below 0.5 by chance alone "
           "(a known small-sample artefact); values below 0.5 should be read as 'no discrimination', not as an "
           "inverse association.", ""]
for outcome, label in A.OUTCOMES.items():
    sub = res[res.Outcome == outcome].copy()
    sub["AUC (95% CI)"] = sub.apply(lambda r: f"{r.AUC:.2f} ({r['AUC 95% CI low']:.2f}–{r['AUC 95% CI high']:.2f})", axis=1)
    report += [f"### {label}", "", md_table(sub[["Feature set", "Model", "AUC (95% CI)", "Sensitivity",
                                                 "Specificity", "Balanced accuracy", "Brier"]]), ""]

# ---------------------------------------------------------------- permutation test (is it better than chance?)
rows, nulls = [], []
for _, r in best.iterrows():
    if r["Feature set"] == "Clinical":
        continue
    y = pts[r.Outcome].values; X = pts[A.FEATURE_SETS[r["Feature set"]]].values
    est = clone(grid[r.Model][0]).set_params(**params_store[(r.Outcome, r["Feature set"], r.Model)])
    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=2, random_state=A.SEED)
    score, perm, p = permutation_test_score(est, X, y, cv=cv, scoring="roc_auc",
                                            n_permutations=200, random_state=A.SEED, n_jobs=-1)
    rows.append([A.OUTCOMES[r.Outcome], r["Feature set"], r.Model, score, np.mean(perm), p])
    nulls.append({"outcome": r.Outcome, "feature_set": r["Feature set"], "model": r.Model,
                  "auc": float(score), "p": float(p), "null": [round(float(v), 4) for v in perm]})
(TAB / "permutation_null.json").write_text(json.dumps(nulls, ensure_ascii=False))
perm_tab = pd.DataFrame(rows, columns=["Outcome", "Feature set", "Best model", "AUC (fixed params)",
                                       "Mean AUC, permuted labels", "Permutation p"])
perm_tab.to_csv(TAB / "table4_permutation_test.csv", index=False)
report += ["## 5. Is the best model better than chance?", "",
           "Label-permutation test (200 permutations, hyper-parameters fixed at the most frequently chosen "
           "values). A small p means the model found signal that disappears when outcome labels are shuffled.",
           "", md_table(perm_tab, ".3f"), ""]

# ---------------------------------------------------------------- leakage demonstration
rf = Pipeline([("impute", SimpleImputer()), ("clf", RandomForestClassifier(300, min_samples_leaf=3,
                                                                          random_state=A.SEED, n_jobs=-1))])
ey = eyes.merge(pts[["patient_id", "syntax_gt8"]], on="patient_id")
Xe, ye, ge = ey[A.CORE].values, ey.syntax_gt8.values, ey.patient_id.values
naive = roc_auc_score(ye, cross_val_predict(rf, Xe, ye, cv=StratifiedKFold(5, shuffle=True, random_state=A.SEED),
                                            method="predict_proba")[:, 1])
grouped = roc_auc_score(ye, cross_val_predict(rf, Xe, ye, cv=GroupKFold(5), groups=ge, method="predict_proba")[:, 1])
report += ["## 6. Why patient-level validation matters", "",
           f"Random forest, AVR/CRAE/CRVE, SYNTAX > 8, eye-level rows: AUC **{naive:.2f}** when the two eyes "
           f"of a patient may fall in different folds (leakage) versus **{grouped:.2f}** when folds are split "
           "by patient. " + ("Treating eyes as independent patients inflated apparent accuracy here. "
                             if naive - grouped >= 0.03 else
                             "Here the leak did not inflate accuracy, because there is little signal to memorise; "
                             "it can do so whenever a real association exists. ") +
           "The two eyes of a patient share the same SYNTAX score, so all models are trained and tested at "
           "patient level.", ""]

# ---------------------------------------------------------------- continuous SYNTAX
y = pts.SYNTAX.values; rows = []
rkf = RepeatedKFold(n_splits=5, n_repeats=10, random_state=A.SEED)
regs = {
    "LASSO regression": Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler()),
                                  ("reg", LassoCV(cv=5, random_state=A.SEED, max_iter=20000))]),
    "Random forest": Pipeline([("impute", SimpleImputer(strategy="median")),
                               ("reg", RandomForestRegressor(300, min_samples_leaf=5, random_state=A.SEED, n_jobs=-1))]),
}
for set_name, feats in A.FEATURE_SETS.items():
    X = pts[feats].values
    for name, reg in regs.items():
        preds = np.zeros((10, n))
        for i, (tr, te) in enumerate(rkf.split(X)):
            preds[i // 5, te] = clone(reg).fit(X[tr], y[tr]).predict(X[te])
        base = np.zeros((10, n))
        for i, (tr, te) in enumerate(rkf.split(X)):
            base[i // 5, te] = y[tr].mean()
        p = preds.mean(0)
        rows.append([set_name, name, r2_score(y, p), mean_absolute_error(y, p), mean_absolute_error(y, base.mean(0)),
                     stats.spearmanr(p, y)[0]])
reg_tab = pd.DataFrame(rows, columns=["Feature set", "Model", "R² (out-of-fold)", "MAE", "MAE, predict-the-mean baseline",
                                      "Spearman rho (pred vs true)"])
reg_tab.to_csv(TAB / "table5_syntax_regression.csv", index=False)
report += ["## 7. Predicting the SYNTAX score as a number", "",
           "5-fold CV repeated 10 times. An R² ≤ 0 or MAE no better than the baseline means the model does not "
           "predict the score better than assigning every patient the cohort mean.", "", md_table(reg_tab), ""]

# ---------------------------------------------------------------- feature ranking
report += ["## 8. AI feature ranking", "",
           f"Three complementary rankings on the {RANK_SET} feature set: (a) mean |SHAP| from a random "
           "forest fitted to all patients, (b) permutation importance (drop in AUC on held-out folds, 5×5 CV) "
           "and (c) LASSO stability selection (fraction of 500 bootstrap samples in which the feature kept a "
           "non-zero coefficient).", ""]
rank_tables = {}
rank_feats = A.FEATURE_SETS[RANK_SET]
for outcome, label in A.OUTCOMES.items():
    y = pts[outcome].values; X = pts[rank_feats]
    rf_est = clone(grid["Random forest"][0]).set_params(**params_store[(outcome, RANK_SET, "Random forest")])
    rf_est.set_params(clf__n_estimators=500).fit(X.values, y)
    expl = shap.TreeExplainer(rf_est.named_steps["clf"])
    Xt = rf_est.named_steps["scale"].transform(rf_est.named_steps["impute"].transform(X.values))
    sv = expl.shap_values(Xt)
    sv = sv[:, :, 1] if np.ndim(sv) == 3 else (sv[1] if isinstance(sv, list) else sv)
    mean_abs = np.abs(sv).mean(0)
    direction = [np.sign(stats.spearmanr(X.values[:, j], sv[:, j])[0]) if X.iloc[:, j].nunique() > 1 else 0
                 for j in range(len(rank_feats))]

    pi = np.zeros(len(rank_feats)); k = 0
    for tr, te in RepeatedStratifiedKFold(n_splits=5, n_repeats=5, random_state=A.SEED).split(X, y):
        m = clone(rf_est).fit(X.values[tr], y[tr])
        pi += permutation_importance(m, X.values[te], y[te], scoring="roc_auc", n_repeats=10,
                                     random_state=A.SEED, n_jobs=-1).importances_mean
        k += 1
    pi /= k

    rng = np.random.default_rng(A.SEED); sel = np.zeros(len(rank_feats))
    lasso = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler()),
                      ("clf", LogisticRegression(l1_ratio=1.0, solver="saga", C=0.3, max_iter=10000,
                                                 class_weight="balanced"))])
    for _ in range(500):
        idx = rng.integers(0, n, n)
        if y[idx].min() == y[idx].max():
            continue
        sel += (np.abs(clone(lasso).fit(X.values[idx], y[idx]).named_steps["clf"].coef_[0]) > 1e-8)
    sel /= 500

    rk = pd.DataFrame({"key": rank_feats, "Feature": [A.LABELS[f] for f in rank_feats],
                       "Type": ["Retinal" if f in A.RETINAL else "Clinical" for f in rank_feats],
                       "Mean |SHAP|": mean_abs, "Permutation ΔAUC": pi, "LASSO selection freq.": sel,
                       "SHAP direction": ["+" if d > 0 else "−" if d < 0 else "0" for d in direction]})
    rk["Rank (SHAP)"] = rk["Mean |SHAP|"].rank(ascending=False).astype(int)
    rk = rk.sort_values("Rank (SHAP)")
    rk.drop(columns="key").to_csv(TAB / f"table6_feature_ranking_{outcome}.csv", index=False)
    rank_tables[outcome] = rk
    report += [f"### {label}", "", md_table(rk.drop(columns="key").head(12), ".3f"), "",
               f"![SHAP summary](figures/shap_{outcome}.png)", ""]

    # SHAP beeswarm
    plt.figure()
    shap.summary_plot(sv, pd.DataFrame(X.values, columns=[A.LABELS[f] for f in rank_feats]), max_display=17,
                      show=False, plot_size=(7, 5.5))
    plt.title(f"SHAP feature impact — {label}", fontsize=10, loc="left", color=INK)
    plt.xlabel("SHAP value (impact on predicted probability)")
    plt.tight_layout(); plt.savefig(FIG / f"shap_{outcome}.png", bbox_inches="tight"); plt.close("all")

# ---------------------------------------------------------------- AI ranking vs domain knowledge
rk = rank_tables["syntax_gt8"].set_index("key")
rows = []
for f, exp in EXPECTED.items():
    obs = rk.loc[f, "SHAP direction"]
    rho = corr.set_index("key").loc[f, "Spearman rho"]
    rows.append([A.LABELS[f], exp.replace("-", "−"), obs, "Yes" if obs == exp.replace("-", "−") else "No",
                 int(rk.loc[f, "Rank (SHAP)"]), rho])
dk = pd.DataFrame(rows, columns=["Retinal parameter", "Expected direction (literature)", "AI (SHAP) direction",
                                 "Agrees", f"AI rank (of {len(rank_feats)})", "Spearman rho with SYNTAX"])
dk.to_csv(TAB / "table7_ai_vs_domain_knowledge.csv", index=False)
report += ["## 9. Does the AI ranking match domain knowledge?", "",
           "Protocol: _\"It will be examined if AI based ranking matches with domain knowledge.\"_ "
           "Expected direction: + = higher value with more severe disease (SYNTAX > 8). "
           "AI direction = sign of the correlation between a feature's value and its SHAP contribution.", "",
           md_table(dk, ".3f"), ""]

# ---------------------------------------------------------------- figures: ROC + AUC comparison
fig, axes = plt.subplots(1, 2, figsize=(9, 4.6), sharey=True)
for ax, (outcome, label) in zip(axes, A.OUTCOMES.items()):
    y = pts[outcome].values
    ax.plot([0, 1], [0, 1], color=INK2, lw=1, ls="--", label="Chance")
    for set_name in A.FEATURE_SETS:
        b = best[(best.Outcome == outcome) & (best["Feature set"] == set_name)].iloc[0]
        fpr, tpr, _ = roc_curve(y, oof_store[(outcome, set_name, b.Model)])
        ax.plot(fpr, tpr, color=SET_COLORS[set_name], label=f"{set_name}: {b.Model}, AUC {b.AUC:.2f}")
    ax.set_title(label, loc="left", fontsize=10, color=INK); ax.set_xlabel("1 − specificity")
    ax.set_aspect("equal"); ax.legend(loc="lower right", fontsize=6.5)
axes[0].set_ylabel("Sensitivity")
roc_export = []
for outcome in A.OUTCOMES:
    for set_name in A.FEATURE_SETS:
        b = best[(best.Outcome == outcome) & (best["Feature set"] == set_name)].iloc[0]
        fpr, tpr, _ = roc_curve(pts[outcome].values, oof_store[(outcome, set_name, b.Model)])
        roc_export.append({"outcome": outcome, "feature_set": set_name, "model": b.Model, "auc": round(b.AUC, 3),
                           "fpr": [round(float(v), 4) for v in fpr], "tpr": [round(float(v), 4) for v in tpr]})
(TAB / "roc_curves.json").write_text(json.dumps(roc_export, ensure_ascii=False))
fig.suptitle("ROC curves, best model per feature set (nested CV, out-of-fold)", x=0.01, ha="left", fontsize=11)
fig.tight_layout(); fig.savefig(FIG / "roc_curves.png", bbox_inches="tight"); plt.close(fig)

fig, axes = plt.subplots(1, 2, figsize=(9, 5), sharey=True)
order = [(s, m) for s in A.FEATURE_SETS for m in grid]
for ax, (outcome, label) in zip(axes, A.OUTCOMES.items()):
    sub = res[res.Outcome == outcome].set_index(["Feature set", "Model"]).loc[order]
    ypos = np.arange(len(order))[::-1]
    cols = [SET_COLORS[s] for s, _ in order]
    ax.axvline(0.5, color=INK2, lw=1, ls="--")
    ax.hlines(ypos, sub["AUC 95% CI low"], sub["AUC 95% CI high"], colors=cols, lw=2)
    ax.scatter(sub["AUC"], ypos, s=36, color=cols, edgecolor="white", linewidth=1.5, zorder=3)
    ax.set_xlim(0.2, 1.0); ax.set_title(label, loc="left", fontsize=10, color=INK); ax.set_xlabel("AUC (95% CI)")
    ax.grid(axis="y", visible=False)
axes[0].set_yticks(np.arange(len(order))[::-1], [f"{s} · {m}" for s, m in order], fontsize=7.5)
handles = [plt.Line2D([], [], color=c, marker="o", lw=2, label=s) for s, c in SET_COLORS.items()]
fig.legend(handles=handles, loc="upper right", ncol=2, fontsize=7.5)
fig.suptitle("Discrimination of every model (dashed line = chance)", x=0.01, ha="left", fontsize=11)
fig.tight_layout(rect=(0, 0, 1, 0.9)); fig.savefig(FIG / "auc_comparison.png", bbox_inches="tight"); plt.close(fig)

report[report.index("## 5. Is the best model better than chance?"):report.index("## 5. Is the best model better than chance?")] = \
    ["![ROC curves](figures/roc_curves.png)", "", "![AUC comparison](figures/auc_comparison.png)", ""]

# ---------------------------------------------------------------- app models (AVR/CRAE/CRVE)
meta = {"features": A.CORE, "labels": {f: A.LABELS[f] for f in A.CORE},
        "eye_ranges": {f: [float(eyes[f].min()), float(eyes[f].median()), float(eyes[f].max())] for f in A.CORE},
        "n_patients": n, "models": {}}
for outcome, label in A.OUTCOMES.items():
    b = best[(best.Outcome == outcome) & (best["Feature set"] == "AVR/CRAE/CRVE")].iloc[0]
    est = clone(grid[b.Model][0]).set_params(**params_store[(outcome, "AVR/CRAE/CRVE", b.Model)])
    est.fit(pts[A.CORE].values, pts[outcome].values)
    joblib.dump(est, MODELS / f"{outcome}.joblib")
    meta["models"][outcome] = {"label": label, "model": b.Model, "auc": round(b.AUC, 3),
                               "auc_ci": [round(b["AUC 95% CI low"], 3), round(b["AUC 95% CI high"], 3)],
                               "sensitivity": round(b.Sensitivity, 3), "specificity": round(b.Specificity, 3),
                               "prevalence": round(float(pts[outcome].mean()), 3)}
(MODELS / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

# ---------------------------------------------------------------- methods paragraph
report += ["## 10. Draft methods text (for the thesis)", "",
           f"Retinal parameters from both eyes were averaged to give one observation per patient (n = {n}). "
           "Two binary outcomes were defined: SYNTAX score above the cohort median (> 8) and SYNTAX score in the "
           "upper quartile (≥ 11); the SYNTAX score was also modelled as a continuous variable. Four supervised "
           "classifiers (L1-penalised logistic regression, random forest, gradient boosting and a support "
           "vector machine with radial-basis kernel) were trained on four predictor sets: the primary retinal "
           "parameters (AVR, CRAE, CRVE), all retinal parameters including OCTA (15 variables), clinical risk "
           "factors only (14 variables), and AVR/CRAE/CRVE combined with clinical risk factors. Medications, "
           "ECG, echocardiography and angiographic findings were excluded as predictors because they are "
           "consequences of disease severity. Missing values were imputed with the training-fold median and "
           "variables standardised within each training fold. Performance was estimated by repeated nested "
           "cross-validation (outer 5-fold × 10 repeats, inner 5-fold grid search optimising the area under "
           "the ROC curve), with all splits made at patient level. Discrimination is reported as AUC with "
           "patient-level bootstrap 95 % confidence intervals; calibration as the Brier score. A label-"
           "permutation test (200 permutations) assessed whether performance exceeded chance. Feature "
           "importance was ranked with SHAP values, out-of-fold permutation importance and LASSO stability "
           "selection, and the resulting ranking was compared with the direction of association reported in "
           "the literature. Analyses used Python 3 with scikit-learn and SHAP. Reporting follows TRIPOD+AI.", ""]

(OUT / "REPORT.md").write_text("\n".join(report))
print("done ->", OUT / "REPORT.md")
