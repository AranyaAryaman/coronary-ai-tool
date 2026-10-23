"""Data preparation and modelling for the retinal microvasculature – ACS severity study.

The raw workbook has one row per eye (two consecutive rows per patient). All
modelling is done at the patient level so that the two eyes of one patient can
never fall on opposite sides of a train/test split.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GridSearchCV, RepeatedStratifiedKFold, StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

SEED = 2025

# Primary predictors of the study (fundus photograph, one disc diameter from the disc)
CORE = ["AVR", "CRAE", "CRVE"]

# Per-eye retinal parameters: model name -> workbook column
RETINAL = {
    "CRAE": "CRAE",
    "CRVE": "CRVE",
    "AVR": "AVR",
    "AVR_fundoscopy": "A-V ratio NAKED EYE",
    "FAZ_area": "FAZ area",
    "FAZ_perimeter": "FAZ perimeter",
    "FAZ_circularity": "FAZ Circularity",
    "VD_SCP": "SCP Whole",
    "VD_DCP": "DCP Whole",
    "VD_OR": "OR Whole",
    "VD_ORCC": "ORCC Whole",
    "VD_CC": "CC Whole",
    "VD_Choroid": "Ch Whole",
    "CFT": "CFT",
    "SFCT": "SFCT",
}

# Patient-level clinical predictors. Medications, ECG/echo and angiography are
# deliberately excluded: they are given or measured because of disease severity.
CLINICAL = {
    "Age": "Age",
    "Male": "Gender",
    "Family_history": "Family History",
    "Diabetes": "DM",
    "Hypertension": "HT",
    "Dyslipidaemia": "Hyperlipid",
    "Smoking": "Smoking",
    "Alcohol": "Drinking",
    "Heart_rate": "HR",
    "SBP": "BP Systolic",
    "DBP": "BP Diastolic",
    "Total_cholesterol": "Cholesterol",
    "Triglycerides": "TG level",
    "HDL": "HDL",
}
BINARY_CLINICAL = ["Family_history", "Diabetes", "Hypertension", "Dyslipidaemia", "Smoking", "Alcohol"]

LABELS = {
    "CRAE": "CRAE", "CRVE": "CRVE", "AVR": "AVR (CRAE/CRVE)",
    "AVR_fundoscopy": "A-V ratio (fundoscopy)", "FAZ_area": "FAZ area (mm²)",
    "FAZ_perimeter": "FAZ perimeter (mm)", "FAZ_circularity": "FAZ circularity",
    "VD_SCP": "VD superficial plexus (%)", "VD_DCP": "VD deep plexus (%)",
    "VD_OR": "VD outer retina (%)", "VD_ORCC": "VD outer retina–CC (%)",
    "VD_CC": "VD choriocapillaris (%)", "VD_Choroid": "VD choroid (%)",
    "CFT": "Central foveal thickness (µm)", "SFCT": "Subfoveal choroidal thickness (µm)",
    "Age": "Age (years)", "Male": "Male sex", "Family_history": "Family history of CAD",
    "Diabetes": "Diabetes", "Hypertension": "Hypertension", "Dyslipidaemia": "Dyslipidaemia",
    "Smoking": "Smoking", "Alcohol": "Alcohol", "Heart_rate": "Heart rate (bpm)",
    "SBP": "Systolic BP (mmHg)", "DBP": "Diastolic BP (mmHg)",
    "Total_cholesterol": "Total cholesterol (mg/dL)", "Triglycerides": "Triglycerides (mg/dL)",
    "HDL": "HDL (mg/dL)",
}

# Thresholds for the binary outcomes
SYNTAX_MEDIAN_CUT = 8      # SYNTAX > 8 (cohort median) = "higher anatomical burden"
SYNTAX_Q3_CUT = 11         # SYNTAX >= 11 (≈ upper quartile), sensitivity analysis

OUTCOMES = {
    "syntax_gt8": "SYNTAX > 8 (median split)",
    "syntax_ge11": "SYNTAX ≥ 11 (upper quartile)",
}

FEATURE_SETS = {
    "AVR/CRAE/CRVE": CORE,
    "All retinal (+ OCTA)": list(RETINAL),
    "Clinical": list(CLINICAL),
    "AVR/CRAE/CRVE + Clinical": CORE + list(CLINICAL),
}

# Provisional corrections for data-entry discrepancies between the two eye rows of a
# patient. TO BE CONFIRMED against the case record before final analysis.
PROVISIONAL_FIXES = {
    56: {"SYNTAX": 6.0},  # SNO 111 = 6, SNO 112 = 1
}


def load_eyes(path) -> pd.DataFrame:
    """Read the workbook and return a tidy one-row-per-eye table (no names)."""
    raw = pd.read_excel(path)
    raw.columns = [str(c).strip() for c in raw.columns]
    raw = raw.dropna(subset=["SNO"])
    eyes = pd.DataFrame({
        "patient_id": ((raw["SNO"].astype(int) - 1) // 2 + 1).values,
        "eye": np.where(raw["SNO"].astype(int) % 2 == 1, "first", "second"),
    })
    for name, col in {**RETINAL, **CLINICAL}.items():
        eyes[name] = pd.to_numeric(raw[col], errors="coerce").values
    eyes["Male"] = (eyes["Male"] == 1).astype(int)
    for name in BINARY_CLINICAL:  # workbook codes 1 = yes, 2 = no
        eyes[name] = eyes[name].map({1: 1, 2: 0})
    eyes["SYNTAX"] = pd.to_numeric(raw["SYNTAX"], errors="coerce").values
    eyes["LVEF"] = pd.to_numeric(raw["LVEF"], errors="coerce").values
    for pid, fixes in PROVISIONAL_FIXES.items():
        for col, value in fixes.items():
            eyes.loc[eyes.patient_id == pid, col] = value
    return eyes


def to_patients(eyes: pd.DataFrame) -> pd.DataFrame:
    """Average the two eyes of each patient; patient-level fields must agree between eyes."""
    per_patient = list(CLINICAL) + ["SYNTAX", "LVEF"]
    inconsistent = eyes.groupby("patient_id")[per_patient].nunique().gt(1).any(axis=1)
    if inconsistent.any():
        raise ValueError(f"Eye pairs disagree on patient-level data: {list(inconsistent[inconsistent].index)}")
    g = eyes.groupby("patient_id")
    pts = pd.concat([g[list(RETINAL)].mean(), g[per_patient].first()], axis=1).reset_index()
    pts["syntax_gt8"] = (pts["SYNTAX"] > SYNTAX_MEDIAN_CUT).astype(int)
    pts["syntax_ge11"] = (pts["SYNTAX"] >= SYNTAX_Q3_CUT).astype(int)
    return pts


def model_grid() -> dict[str, tuple[Pipeline, dict]]:
    """Candidate classifiers with their (small) hyper-parameter grids."""
    pre = [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
    return {
        "LASSO logistic": (
            Pipeline(pre + [("clf", LogisticRegression(l1_ratio=1.0, solver="saga", max_iter=10000,
                                                       class_weight="balanced", random_state=SEED))]),
            {"clf__C": [0.05, 0.1, 0.3, 1.0]},
        ),
        "Random forest": (
            Pipeline(pre + [("clf", RandomForestClassifier(n_estimators=300, max_features="sqrt",
                                                           class_weight="balanced_subsample",
                                                           random_state=SEED, n_jobs=1))]),
            {"clf__max_depth": [3, None], "clf__min_samples_leaf": [3, 6]},
        ),
        "Gradient boosting": (
            Pipeline(pre + [("clf", HistGradientBoostingClassifier(max_iter=100, class_weight="balanced",
                                                                   l2_regularization=1.0, random_state=SEED))]),
            {"clf__max_depth": [2, 3], "clf__learning_rate": [0.03, 0.1], "clf__min_samples_leaf": [5]},
        ),
        "SVM (RBF)": (
            Pipeline(pre + [("clf", SVC(kernel="rbf", probability=True, class_weight="balanced",
                                        random_state=SEED))]),
            {"clf__C": [0.3, 1.0, 3.0], "clf__gamma": ["scale", 0.01]},
        ),
    }


def _fit_fold(est, grid, X, y, train, test, seed):
    inner = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    search = GridSearchCV(clone(est), grid, cv=inner, scoring="roc_auc", n_jobs=1).fit(X[train], y[train])
    return test, search.predict_proba(X[test])[:, 1], search.best_params_


def nested_cv(est, grid, X, y, n_repeats=10, n_jobs=-1):
    """Repeated nested CV. Returns (oof probabilities [repeats x n], AUC per repeat, chosen params)."""
    outer = RepeatedStratifiedKFold(n_splits=5, n_repeats=n_repeats, random_state=SEED)
    folds = list(outer.split(X, y))
    results = Parallel(n_jobs=n_jobs)(
        delayed(_fit_fold)(est, grid, X, y, tr, te, SEED + i) for i, (tr, te) in enumerate(folds)
    )
    oof = np.full((n_repeats, len(y)), np.nan)
    for i, (test, prob, _) in enumerate(results):
        oof[i // 5, test] = prob
    aucs = np.array([roc_auc_score(y, row) for row in oof])
    return oof, aucs, [r[2] for r in results]


def bootstrap_auc_ci(y, prob, n_boot=2000, seed=SEED):
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].min() != y[idx].max():
            stats.append(roc_auc_score(y[idx], prob[idx]))
    return np.percentile(stats, [2.5, 97.5])


def threshold_metrics(y, prob, threshold=0.5):
    """Sensitivity, specificity, PPV, NPV, balanced accuracy at a probability threshold."""
    pred = (prob >= threshold).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum()); tn = int(((pred == 0) & (y == 0)).sum())
    fp = int(((pred == 1) & (y == 0)).sum()); fn = int(((pred == 0) & (y == 1)).sum())
    sens = tp / (tp + fn) if tp + fn else np.nan
    spec = tn / (tn + fp) if tn + fp else np.nan
    return {
        "Sensitivity": sens, "Specificity": spec,
        "PPV": tp / (tp + fp) if tp + fp else np.nan,
        "NPV": tn / (tn + fn) if tn + fn else np.nan,
        "Balanced accuracy": (sens + spec) / 2,
    }


def most_common_params(params: list[dict]) -> dict:
    """Hyper-parameters chosen most often across outer folds (used for the final refit)."""
    keys = pd.Series([tuple(sorted(p.items())) for p in params]).value_counts()
    return dict(keys.index[0])
