"""Train ``pd_vowel_audio_v2``: the Parkinson's sustained-vowel classifier on REAL audio.

Why a v2: ``pd_vowel_experimental_v1`` was trained on UCI's pre-computed MDVP
feature table, but the app computes its own features from audio with
``app/ml/features/vowel.py`` (Praat/parselmouth + re-implemented RPDE/DFA/PPE/
spread/D2), and several of those come out on a different scale from MDVP.
This script closes that train/serve gap: every training feature is produced by
``extract_vowel_features`` — the exact function the app calls at serving — on
real sustained /a/ recordings, after the app's own ``assess_quality`` gate.

Dataset (default): Italian Parkinson's Voice and Speech (Dimauro & Girardi,
IEEE DataPort 10.21227/aw6b-tg17, CC BY 4.0), see
``data/raw/italian_pd_voice/SOURCE.md``. Only the sustained /a/ files (``VA1``,
``VA2``) of the 22 elderly controls and the 28 PD entries are used; subjects are
re-identified across the two PD recording sessions so grouping is per person.

Evaluation (everything subject-grouped, reusing ``ml_training/train_pd_vowel.py``):

* Outer ``StratifiedGroupKFold(5, shuffle)`` by subject; inside each training
  fold a grouped ``GridSearchCV`` then sigmoid ``CalibratedClassifierCV``
  (``fit_selected_calibrated``). Median imputation is prepended to each v1
  pipeline because real audio can yield ``nan`` features.
* Repeated shuffled outer CV, subject-cluster bootstrap 95% CI, threshold at
  80% specificity on pooled out-of-fold probabilities, subject-level AUC on
  the median probability per subject.
* Confounding checks: AUC without the F0 features, AUC within each sex, AUC on
  the single-sample-rate subset (the 2016 PD session is 44.1 kHz, everything
  else 16 kHz), effect of the quality gate, mean out-of-fold probability per
  recording session, and a feature-scale table comparing this dataset's
  healthy controls with the UCI feature medians that v1 was trained on.

Usage (from backend/)::

    .venv/bin/python -m ml_training.train_pd_vowel_audio [--fast] [--repeats 10]
    .venv/bin/python -m ml_training.train_pd_vowel_audio --data manifest.csv --out /tmp/v2

``--data`` is either the extracted dataset directory (Italian layout) or a CSV
manifest with columns ``path, subject_id, label[, sex, take, session]``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:  # allow `python ml_training/train_pd_vowel_audio.py`
    sys.path.insert(0, str(BACKEND_DIR))

from app.ml.features.quality import assess_quality, load_audio  # noqa: E402
from app.ml.features.vowel import FEATURE_NOTES, UCI_FEATURES, extract_vowel_features, feature_notes_summary  # noqa: E402
from ml_training.train_pd_vowel import (  # noqa: E402
    _fmt,
    _json_default,
    fit_selected_calibrated,
    grouped_oof,
    model_specs,
    subject_bootstrap_auc,
    summarize,
    threshold_at_specificity,
)

logger = logging.getLogger("train_pd_vowel_audio")

DATASET_SLUG = "italian_pd_voice"
DATASET_NAME = "Italian Parkinson's Voice and Speech (Dimauro & Girardi, IEEE DataPort 10.21227/aw6b-tg17)"
DATASET_LICENCE = "CC BY 4.0"
DATASET_LANGUAGE = "Italian"
DEFAULT_DATA = BACKEND_DIR / "data" / "raw" / DATASET_SLUG / "extracted"
DEFAULT_CACHE = BACKEND_DIR / "data" / "processed" / f"{DATASET_SLUG}_features.csv"
DEFAULT_OUT = BACKEND_DIR / "models" / "pd_vowel_audio_v2"
V1_DIR = BACKEND_DIR / "models" / "pd_vowel_experimental_v1"
UCI_CSV = BACKEND_DIR / "data" / "raw" / "uci_parkinsons" / "parkinsons.data"
CARD_TEMPLATE = Path(__file__).resolve().parent / "model_card_pd_vowel_audio.md"
MODEL_VERSION = "pd_vowel_audio_v2"
F0_FEATURES = ["MDVP:Fo(Hz)", "MDVP:Fhi(Hz)", "MDVP:Flo(Hz)"]
JITTER_FEATURES = ["MDVP:Jitter(%)", "MDVP:Jitter(Abs)", "MDVP:RAP", "MDVP:PPQ", "Jitter:DDP"]
# Named feature subsets for --features. "channel_robust" drops every amplitude/noise measure
# (shimmer family, NHR, HNR, DFA, D2) that a microphone / high-pass channel can shift, keeping
# period-based measures (jitter family, RPDE) and pitch-contour measures (PPE, spread1/2).
FEATURE_SUBSETS: dict[str, list[str]] = {
    "all": list(UCI_FEATURES),
    "channel_robust": JITTER_FEATURES + ["RPDE", "PPE", "spread1", "spread2"],
    "channel_robust_f0": JITTER_FEATURES + ["RPDE", "PPE", "spread1", "spread2"] + F0_FEATURES,
}
MIN_SUBJECTS_PER_CLASS = 2  # below this an AUC block is reported as None


def resolve_features(spec: str | list[str] | None) -> list[str]:
    """``None``/"all" -> all 22; a FEATURE_SUBSETS name; or a comma list of UCI names (kept in UCI order)."""
    if spec is None:
        return list(UCI_FEATURES)
    if isinstance(spec, str):
        if spec in FEATURE_SUBSETS:
            return list(FEATURE_SUBSETS[spec])
        spec = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [s for s in spec if s not in UCI_FEATURES]
    if unknown:
        raise ValueError(f"unknown features {unknown}; choose from {UCI_FEATURES} or a subset name {list(FEATURE_SUBSETS)}")
    return [f for f in UCI_FEATURES if f in set(spec)]

# Italian file code: VA<take><name-derived letters><yy of birth><M|F><ddmmyy|ddmmyyyy><hhmm>.wav
_CODE_RE = re.compile(r"^VA(?P<take>\d)(?P<code>.+?)(?P<yob>\d{2})(?P<sex>[MF])(?P<stamp>\d{10,12})\.wav$", re.IGNORECASE)


# --------------------------------------------------------------------------- data discovery

def discover_italian(data_dir: Path | str = DEFAULT_DATA) -> pd.DataFrame:
    """Manifest of sustained-/a/ recordings in the extracted Italian dataset.

    Label 1 for files under the "People with Parkinson's disease" folder, 0 for
    "Elderly Healthy Control" (the young controls have no vowel recordings and
    are skipped). The subject key is the sorted letters of the name-derived code
    plus birth year and sex, which re-identifies the three PD people recorded
    in both the 2016 (44.1 kHz) and 2017 (16 kHz) sessions. Subject ids are
    anonymous (``hc_NN`` / ``pd_NN``); folder names (which carry first names)
    are never copied out.
    """
    data_dir = Path(data_dir)
    rows: list[dict[str, Any]] = []
    for p in sorted(data_dir.rglob("*.wav")):
        m = _CODE_RE.match(p.name)
        if not m:
            continue
        parts = [s.lower() for s in p.relative_to(data_dir).parts]
        if any("parkinson" in s for s in parts):
            label = 1
        elif any("elderly healthy" in s for s in parts):
            label = 0
        else:
            continue
        letters = "".join(sorted(c for c in m["code"].lower() if c.isalpha()))
        stamp = m["stamp"]
        year = ("20" + stamp[4:6]) if len(stamp) == 10 else stamp[4:8]
        rows.append(
            {
                "path": str(p),
                "file": p.name,
                "subject_key": f"{label}|{letters}|{m['yob']}|{m['sex']}",
                "label": label,
                "sex": m["sex"].upper(),
                "birth_year": 1900 + int(m["yob"]),
                "take": int(m["take"]),
                "session": f"{'pd' if label else 'hc'}_{year}",
            }
        )
    if not rows:
        raise FileNotFoundError(f"no VA*.wav sustained-vowel files found under {data_dir}")
    df = pd.DataFrame(rows)
    ids: dict[str, str] = {}
    for label, prefix in ((0, "hc"), (1, "pd")):
        keys = sorted(df.loc[df.label == label, "subject_key"].unique())
        ids.update({k: f"{prefix}_{i + 1:02d}" for i, k in enumerate(keys)})
    df["subject_id"] = df["subject_key"].map(ids)
    return df.drop(columns="subject_key")


def load_manifest(path: Path | str) -> pd.DataFrame:
    """CSV manifest with ``path, subject_id, label`` (+ optional ``sex, take, session, birth_year``)."""
    df = pd.read_csv(path)
    missing = {"path", "subject_id", "label"} - set(df.columns)
    if missing:
        raise ValueError(f"manifest {path} lacks columns {sorted(missing)}")
    base = Path(path).resolve().parent
    df["path"] = [str(p) if Path(p).is_absolute() else str(base / p) for p in df["path"].astype(str)]
    df["file"] = [Path(p).name for p in df["path"]]
    df["label"] = df["label"].astype(int)
    for col, default in (("sex", None), ("take", 1), ("session", "manifest"), ("birth_year", None)):
        if col not in df.columns:
            df[col] = default
    return df


def load_recordings(data: Path | str = DEFAULT_DATA) -> pd.DataFrame:
    data = Path(data)
    return load_manifest(data) if data.is_file() else discover_italian(data)


# --------------------------------------------------------------------------- feature table

def _channel_stats(y: np.ndarray, sr: int) -> dict[str, float]:
    """Cheap recording-channel descriptors (level, low-frequency share) for confound checks."""
    from scipy.signal import welch

    rms = float(np.sqrt(np.mean(y**2))) if len(y) else 0.0
    level_db = 20.0 * np.log10(rms) if rms > 0 else float("nan")
    try:
        f, p = welch(y, sr, nperseg=min(4096, len(y)))
        total = float(p[f < 8000].sum())
        low = float(p[f < 300].sum())
        lf_ratio_db = 10.0 * np.log10(low / total) if total > 0 and low > 0 else float("nan")
    except Exception:  # noqa: BLE001
        lf_ratio_db = float("nan")
    return {"level_db": float(level_db), "lf_ratio_db": float(lf_ratio_db)}


def extract_one(row: dict[str, Any]) -> dict[str, Any]:
    """Quality gate + app feature extractor for one manifest row (never raises)."""
    out: dict[str, Any] = {k: row.get(k) for k in ("file", "subject_id", "label", "sex", "birth_year", "take", "session")}
    out.update({"quality_ok": False, "quality_reasons": "", "extract_error": ""})
    out.update({k: float("nan") for k in UCI_FEATURES})
    try:
        y, sr = load_audio(row["path"])
        q = assess_quality(y, sr)
        out.update(
            duration_s=q.duration_s, sr=int(sr), voiced_s=q.voiced_s, snr_db=q.snr_db, clip_frac=q.clip_frac,
            quality_ok=bool(q.ok), quality_reasons=";".join(q.reasons),
        )
        out.update(_channel_stats(y, sr))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out.update(extract_vowel_features(y, sr))
    except Exception as exc:  # noqa: BLE001 - a corrupt file must not kill the run
        out["extract_error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("feature extraction failed for %s: %s", row.get("file"), exc)
    return out


def build_feature_table(
    manifest: pd.DataFrame,
    cache: Path | str | None = DEFAULT_CACHE,
    force: bool = False,
    n_jobs: int = 1,
) -> pd.DataFrame:
    """One row per recording: metadata, quality gate, channel stats and the 22 app features.

    Cached to ``cache`` (CSV); reused when it covers exactly the manifest's files.
    """
    if cache is not None and not force and Path(cache).exists():
        cached = pd.read_csv(cache)
        if set(cached.get("file", [])) == set(manifest["file"]):
            logger.info("using cached features %s (%d rows)", cache, len(cached))
            return cached
    rows = manifest.to_dict(orient="records")
    t0 = time.time()
    if n_jobs != 1 and len(rows) >= 8:
        from joblib import Parallel, delayed

        results = Parallel(n_jobs=n_jobs)(delayed(extract_one)(r) for r in rows)
    else:
        results = [extract_one(r) for r in rows]
    table = pd.DataFrame(results)
    logger.info("extracted features for %d recordings in %.1f s", len(table), time.time() - t0)
    if cache is not None:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(cache, index=False)
    return table


# --------------------------------------------------------------------------- evaluation helpers

def specs_with_imputer(fast: bool = False) -> dict[str, tuple[Any, dict[str, list]]]:
    """v1 candidate pipelines with median imputation prepended (real audio can give nan features)."""
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline

    out = {}
    for name, (pipe, grid) in model_specs(fast=fast).items():
        out[name] = (Pipeline([("impute", SimpleImputer(strategy="median"))] + list(pipe.steps)), grid)
    return out


def _outer_splits(X: pd.DataFrame, y: np.ndarray, g: np.ndarray, n_splits: int, seed: int) -> list:
    from sklearn.model_selection import StratifiedGroupKFold

    n_splits = max(2, min(n_splits, len(np.unique(g[y == 0])), len(np.unique(g[y == 1]))))
    return list(StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=seed).split(X, y, g))


def _enough(y: np.ndarray, g: np.ndarray) -> bool:
    return all(len(np.unique(g[y == c])) >= MIN_SUBJECTS_PER_CLASS for c in (0, 1))


def auc_block(y: np.ndarray, p: np.ndarray, g: np.ndarray, n_boot: int, seed: int) -> dict[str, Any] | None:
    """Recording AUC + subject-bootstrap CI, subject-level AUC and sens@80%spec for a subset."""
    from sklearn.metrics import roc_auc_score

    y, p, g = np.asarray(y), np.asarray(p, dtype=float), np.asarray(g)
    ok = np.isfinite(p)
    y, p, g = y[ok], p[ok], g[ok]
    if len(y) == 0 or not _enough(y, g):
        return None
    try:
        lo, hi = subject_bootstrap_auc(y, p, g, n_boot, seed)
    except (ValueError, IndexError):
        lo = hi = None
    subj = pd.DataFrame({"g": g, "y": y, "p": p}).groupby("g").agg(y=("y", "first"), p=("p", "median"))
    thr, sens, spec = threshold_at_specificity(y, p)
    return {
        "n_recordings": int(len(y)),
        "n_subjects": int(len(np.unique(g))),
        "n_pd_subjects": int(len(np.unique(g[y == 1]))),
        "n_healthy_subjects": int(len(np.unique(g[y == 0]))),
        "auc": float(roc_auc_score(y, p)),
        "auc_ci95_subject_bootstrap": [lo, hi],
        "subject_auc_median_prob": float(roc_auc_score(subj["y"], subj["p"])),
        "threshold_at_80_specificity": thr,
        "sensitivity_at_80_specificity": sens,
        "achieved_specificity": spec,
    }


def evaluate_candidates(
    X: pd.DataFrame, y: np.ndarray, g: np.ndarray, specs: dict, n_splits: int, n_repeats: int,
    n_boot: int, seed: int, n_inner: int, tag: str = "",
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Grouped nested CV for every candidate (v1 ``summarize`` layout + repeated CV)."""
    from sklearn.metrics import roc_auc_score

    outer = _outer_splits(X, y, g, n_splits, seed)
    results: dict[str, Any] = {}
    oofs: dict[str, np.ndarray] = {}
    for name, spec in specs.items():
        oof, folds = grouped_oof(f"{name}{tag}", spec, X, y, g, outer, seed, n_inner)
        oofs[name] = oof
        res = summarize(y, oof, g, folds, n_boot, seed)
        rep_aucs = []
        for r in range(n_repeats):
            rs = _outer_splits(X, y, g, n_splits, seed + 100 + r)
            rp, _ = grouped_oof(f"{name}{tag}-rep{r}", spec, X, y, g, rs, seed + 100 + r, n_inner)
            rep_aucs.append(float(roc_auc_score(y, rp)))
        if rep_aucs:
            res["repeated_cv"] = {
                "n_repeats": n_repeats,
                "aucs": rep_aucs,
                "mean": float(np.mean(rep_aucs)),
                "sd": float(np.std(rep_aucs, ddof=1)) if len(rep_aucs) > 1 else 0.0,
                "range": [float(min(rep_aucs)), float(max(rep_aucs))],
            }
        results[name] = res
    return results, oofs


def oof_for_subset(
    spec: tuple[Any, dict], X: pd.DataFrame, y: np.ndarray, g: np.ndarray, n_splits: int, seed: int, n_inner: int, n_boot: int, tag: str,
) -> dict[str, Any] | None:
    """Retrain grouped CV on a subset/feature set and return an ``auc_block``."""
    if not _enough(y, g):
        return None
    outer = _outer_splits(X, y, g, n_splits, seed)
    oof, _ = grouped_oof(tag, spec, X, y, g, outer, seed, n_inner)
    return auc_block(y, oof, g, n_boot, seed)


def v1_on_features(table: pd.DataFrame, v1_dir: Path = V1_DIR) -> dict[str, Any] | None:
    """Score the served v1 (UCI-table) model on this dataset's app-extracted features.

    v1 never saw these recordings, so no CV is needed; this measures what the
    train/serve skew costs at its own 80%-specificity threshold.
    """
    if not (v1_dir / "model.joblib").exists():
        return None
    from sklearn.metrics import roc_auc_score

    from app.ml.pd_vowel_model import PdVowelModel

    try:
        v1 = PdVowelModel.load(v1_dir)
        p = np.array([v1.predict_from_features(r).probability for r in table[UCI_FEATURES].to_dict(orient="records")])
    except Exception as exc:  # noqa: BLE001 - version skew in the pickle must not kill training
        logger.warning("could not score v1 on this data: %s", exc)
        return None
    y = table["label"].to_numpy(dtype=int)
    g = table["subject_id"].astype(str).to_numpy()
    if not _enough(y, g):
        return None
    block = auc_block(y, p, g, 200, 0) or {}
    return {
        "model_version": v1.model_version,
        "auc": float(roc_auc_score(y, p)),
        "subject_auc_median_prob": block.get("subject_auc_median_prob"),
        "v1_threshold": v1.threshold,
        "sensitivity_at_v1_threshold": float(np.mean(p[y == 1] >= v1.threshold)),
        "specificity_at_v1_threshold": float(np.mean(p[y == 0] < v1.threshold)),
        "mean_prob_healthy": float(np.mean(p[y == 0])),
        "mean_prob_pd": float(np.mean(p[y == 1])),
    }


def channel_proxy(X: pd.DataFrame, sess: np.ndarray, y: np.ndarray, g: np.ndarray, n_splits: int, seed: int) -> dict[str, Any] | None:
    """How well the feature set predicts the recording session (channel), subject-grouped CV.

    Reports the multiclass macro one-vs-rest AUC and balanced accuracy (chance =
    1/n_sessions), plus pairwise AUCs between sessions that share a label (e.g.
    the two PD sessions) — those pairs are pure channel discrimination.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.impute import SimpleImputer
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score
    from sklearn.model_selection import StratifiedGroupKFold, cross_val_predict
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import LabelEncoder, StandardScaler

    sessions = sorted(set(sess))
    if len(sessions) < 2:
        return None
    pipe = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler()), ("clf", LogisticRegression(max_iter=5000, C=1.0))])

    def _cv_proba(Xs: pd.DataFrame, target: np.ndarray, gs: np.ndarray) -> np.ndarray | None:
        n = max(2, min(n_splits, *[len(np.unique(gs[target == c])) for c in np.unique(target)]))
        if any(len(np.unique(gs[target == c])) < 2 for c in np.unique(target)):
            return None
        cv = StratifiedGroupKFold(n_splits=n, shuffle=True, random_state=seed)
        return cross_val_predict(pipe, Xs, target, groups=gs, cv=cv, method="predict_proba")

    enc = LabelEncoder().fit(sessions)
    t = enc.transform(sess)
    out: dict[str, Any] = {"sessions": sessions, "chance_balanced_accuracy": 1.0 / len(sessions), "pairwise_same_label": {}}
    proba = _cv_proba(X, t, g)
    if proba is not None:
        out["macro_ovr_auc"] = float(roc_auc_score(t, proba, multi_class="ovr", average="macro")) if len(sessions) > 2 else float(roc_auc_score(t, proba[:, 1]))
        out["balanced_accuracy"] = float(balanced_accuracy_score(t, proba.argmax(axis=1)))
    for i, a in enumerate(sessions):
        for b in sessions[i + 1 :]:
            m = (sess == a) | (sess == b)
            if len(set(y[m])) != 1:
                continue  # different labels: that pair is label, not channel
            pb = _cv_proba(X[m].reset_index(drop=True), (sess[m] == b).astype(int), g[m])
            if pb is not None:
                out["pairwise_same_label"][f"{a}_vs_{b}"] = {
                    "auc": float(roc_auc_score((sess[m] == b).astype(int), pb[:, 1])),
                    "n_recordings": int(m.sum()), "n_subjects": int(len(np.unique(g[m]))),
                }
    return out


def leave_one_session_out(
    spec: tuple[Any, dict], X: pd.DataFrame, y: np.ndarray, g: np.ndarray, sess: np.ndarray, n_inner: int, seed: int,
) -> dict[str, Any] | None:
    """Train on healthy half A + one PD session, test on healthy half B + the other PD session (and vice versa).

    Healthy subjects are split into two halves by subject so the test AUC is
    computable; subjects recorded in more than one PD session are excluded to
    avoid leakage. Sensitivity on the held-out PD session uses the threshold at
    80% specificity on the training set's own fitted probabilities.
    """
    from sklearn.metrics import roc_auc_score

    pd_sessions = sorted(set(sess[y == 1]))
    hc_sessions = set(sess[y == 0])
    if len(pd_sessions) < 2 or len(hc_sessions) != 1:
        return None
    subj_sess = pd.DataFrame({"g": g, "s": sess}).groupby("g")["s"].nunique()
    multi = set(subj_sess[subj_sess > 1].index)
    hc_subjects = sorted(set(g[y == 0]))
    half_a = set(hc_subjects[0::2])
    out: dict[str, Any] = {"excluded_multi_session_subjects": sorted(multi), "splits": {}}
    for s_train, s_test in [(a, b) for a in pd_sessions for b in pd_sessions if a != b]:
        for hc_train_half, name in ((half_a, "hcA"), (set(hc_subjects) - half_a, "hcB")):
            keep = ~np.isin(g, list(multi))
            tr = keep & (((y == 0) & np.isin(g, list(hc_train_half))) | (sess == s_train))
            te = keep & (((y == 0) & ~np.isin(g, list(hc_train_half))) | (sess == s_test))
            if not (_enough(y[tr], g[tr]) and _enough(y[te], g[te])):
                continue
            model, _ = fit_selected_calibrated(spec[0], spec[1], X[tr].reset_index(drop=True), y[tr], g[tr], n_inner, seed)
            p_tr = model.predict_proba(X[tr])[:, 1]
            p_te = model.predict_proba(X[te])[:, 1]
            thr, _, _ = threshold_at_specificity(y[tr], p_tr)
            subj = pd.DataFrame({"g": g[te], "y": y[te], "p": p_te}).groupby("g").agg(y=("y", "first"), p=("p", "median"))
            out["splits"][f"train_{name}+{s_train}__test_{s_test}"] = {
                "n_train": int(tr.sum()), "n_test": int(te.sum()),
                "auc": float(roc_auc_score(y[te], p_te)),
                "subject_auc_median_prob": float(roc_auc_score(subj["y"], subj["p"])),
                "sensitivity_heldout_pd_session": float(np.mean(p_te[y[te] == 1] >= thr)),
                "specificity_heldout_hc_half": float(np.mean(p_te[y[te] == 0] < thr)),
                "train_threshold_80spec": thr,
            }
    if out["splits"]:
        out["mean_auc"] = float(np.mean([v["auc"] for v in out["splits"].values()]))
        out["mean_subject_auc"] = float(np.mean([v["subject_auc_median_prob"] for v in out["splits"].values()]))
    return out


# --------------------------------------------------------------------------- feature-scale sanity table

def _uci_healthy_medians() -> dict[str, float] | None:
    if not UCI_CSV.exists():
        return None
    u = pd.read_csv(UCI_CSV)
    return {k: float(u.loc[u["status"] == 0, k].median()) for k in UCI_FEATURES}


def _v1_train_medians() -> dict[str, float] | None:
    p = V1_DIR / "feature_order.json"
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("train_medians")


def feature_scale_comparison(table: pd.DataFrame) -> list[dict[str, Any]]:
    """Per feature: this dataset's healthy-control median/IQR vs the UCI medians v1 was trained on.

    ``ratio_hc_to_uci_healthy`` = our control median / UCI healthy median (falls
    back to v1's all-subject training median when the UCI CSV is absent).
    ``scale_mismatch`` flags ratios outside [0.5, 2]. ``univariate_auc`` is the
    recording-level AUC of the raw feature on this dataset (>0.5: higher in PD).
    """
    from sklearn.metrics import roc_auc_score

    uci_h = _uci_healthy_medians()
    v1 = _v1_train_medians()
    hc = table[table["label"] == 0]
    pdx = table[table["label"] == 1]
    rows = []
    for k in UCI_FEATURES:
        hc_med = float(hc[k].median()) if len(hc) else float("nan")
        q25, q75 = (float(hc[k].quantile(0.25)), float(hc[k].quantile(0.75))) if len(hc) else (float("nan"),) * 2
        ref = (uci_h or {}).get(k, (v1 or {}).get(k))
        ratio = (hc_med / ref) if ref not in (None, 0) and np.isfinite(hc_med) else None
        col = table[k]
        auc = None
        if col.notna().sum() >= 4 and table["label"].nunique() == 2:
            auc = float(roc_auc_score(table.loc[col.notna(), "label"], col.dropna()))
        rows.append(
            {
                "feature": k,
                "this_hc_median": hc_med,
                "this_hc_iqr": [q25, q75],
                "this_pd_median": float(pdx[k].median()) if len(pdx) else None,
                "uci_healthy_median": (uci_h or {}).get(k),
                "v1_train_median_uci_all": (v1 or {}).get(k),
                "ratio_hc_to_uci_healthy": ratio,
                "scale_mismatch": bool(ratio is not None and not (0.5 <= ratio <= 2.0)),
                "univariate_auc": auc,
                "approximate_in_extractor": bool(FEATURE_NOTES[k]["approximate"]),
                "nan_count": int(col.isna().sum()),
            }
        )
    return rows


def scale_table_text(rows: list[dict[str, Any]]) -> str:
    """Plain-text version of :func:`feature_scale_comparison` for the console/report."""
    lines = [f"{'feature':18s} {'HC median':>11s} {'HC IQR':>23s} {'PD median':>11s} {'UCI healthy':>11s} {'v1 median':>11s} {'ratio':>7s} {'uni-AUC':>7s} flag"]
    for r in rows:
        iqr = f"[{_g(r['this_hc_iqr'][0])}, {_g(r['this_hc_iqr'][1])}]"
        lines.append(
            f"{r['feature']:18s} {_g(r['this_hc_median']):>11s} {iqr:>23s} {_g(r['this_pd_median']):>11s} "
            f"{_g(r['uci_healthy_median']):>11s} {_g(r['v1_train_median_uci_all']):>11s} {_g(r['ratio_hc_to_uci_healthy'], 2):>7s} "
            f"{_g(r['univariate_auc'], 2):>7s} {'MISMATCH' if r['scale_mismatch'] else ''}"
        )
    return "\n".join(lines)


def _g(x: Any, nd: int = 4) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:.{nd}g}"


# --------------------------------------------------------------------------- driver

def library_versions() -> dict[str, str]:
    import platform

    out = {"python": platform.python_version()}
    for mod in ("numpy", "scipy", "sklearn", "xgboost", "joblib", "parselmouth", "librosa", "soundfile", "pandas"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001
            out[mod] = "n/a"
    return out


def run_training(
    data: Path | str = DEFAULT_DATA,
    out_dir: Path | str | None = DEFAULT_OUT,
    n_splits: int = 5,
    n_repeats: int = 10,
    n_boot: int = 2000,
    fast: bool = False,
    seed: int = 42,
    cache: Path | str | None = DEFAULT_CACHE,
    force_features: bool = False,
    n_jobs: int = 1,
    include_gate_failures: bool = False,
    features: str | list[str] | None = None,
) -> dict[str, Any]:
    """Extract features with the app's extractor, run grouped nested CV, refit, save artefacts.

    Args:
        data: extracted dataset directory (Italian layout) or a manifest CSV.
        features: subset name from ``FEATURE_SUBSETS``, comma list, or ``None`` for all 22.
        out_dir: artefact directory (same layout as v1); ``None`` skips saving and the final refit.
        n_splits / n_repeats / n_boot / fast / seed: as in ``train_pd_vowel.run_training``.
        cache: feature-table CSV (``None`` disables caching).
        force_features: recompute features even if the cache matches.
        n_jobs: parallel workers for feature extraction.
        include_gate_failures: train on recordings that fail ``assess_quality`` too
            (by default they are excluded, as the app would not score them).

    Returns:
        metrics dict (also written to metrics.json).
    """
    from sklearn.metrics import roc_auc_score

    warnings.filterwarnings("ignore", category=UserWarning)
    t0 = time.time()
    manifest = load_recordings(data)
    table = build_feature_table(manifest, cache=cache, force=force_features, n_jobs=n_jobs)
    table["quality_ok"] = table["quality_ok"].astype(bool)
    table["extract_error"] = table["extract_error"].fillna("").astype(str)
    usable = table[table["extract_error"] == ""]
    train_df = usable if include_gate_failures else usable[usable["quality_ok"]]
    train_df = train_df.reset_index(drop=True)
    if train_df.empty or train_df["label"].nunique() < 2:
        raise RuntimeError("not enough usable recordings of both classes after the quality gate")

    feat = resolve_features(features)
    X = train_df[feat].astype(float)
    y = train_df["label"].to_numpy(dtype=int)
    g = train_df["subject_id"].astype(str).to_numpy()
    specs = specs_with_imputer(fast=fast)
    n_inner = 3 if fast else 4

    results, oofs = evaluate_candidates(X, y, g, specs, n_splits, n_repeats, n_boot, seed, n_inner)
    chosen = max(results, key=lambda k: results[k]["auc_pooled_oof"])
    f0_present = [f for f in F0_FEATURES if f in feat]
    X_nof0 = X.drop(columns=f0_present) if f0_present and len(feat) > len(f0_present) else None

    # --- confounding / sensitivity analyses
    analyses: dict[str, Any] = {"without_f0_features": {}, "by_sex": {}, "mean_oof_prob_by_session": {}}
    for name, spec in specs.items():
        analyses["without_f0_features"][name] = (
            oof_for_subset(spec, X_nof0, y, g, n_splits, seed, n_inner, n_boot, f"{name}-noF0") if X_nof0 is not None else None
        )
        sex = train_df["sex"].astype(str).str.upper().to_numpy() if train_df["sex"].notna().any() else None
        analyses["by_sex"][name] = {}
        if sex is not None:
            for s in sorted(set(sex) - {"NAN", "NONE", ""}):
                m = sex == s
                analyses["by_sex"][name][s] = auc_block(y[m], oofs[name][m], g[m], n_boot, seed)
        sess = train_df["session"].astype(str).to_numpy()
        analyses["mean_oof_prob_by_session"][name] = {
            s: {"n": int((sess == s).sum()), "mean_prob": float(np.mean(oofs[name][sess == s]))} for s in sorted(set(sess))
        }
    spec = specs[chosen]
    srs = train_df["sr"].dropna().astype(int)
    modal_sr = int(srs.mode().iloc[0]) if len(srs) else None
    if modal_sr is not None and srs.nunique() > 1:
        m = (train_df["sr"] == modal_sr).to_numpy()
        analyses["single_sample_rate_subset"] = {
            "sample_rate": modal_sr, "model": chosen, "dropped_sessions": sorted(set(train_df.loc[~m, "session"].astype(str))),
            "result": oof_for_subset(spec, X[m].reset_index(drop=True), y[m], g[m], n_splits, seed, n_inner, n_boot, f"{chosen}-sr{modal_sr}"),
        }
    n_gate_failed = int((~usable["quality_ok"]).sum())
    if n_gate_failed and not include_gate_failures:
        Xa = usable[feat].astype(float).reset_index(drop=True)
        ya = usable["label"].to_numpy(dtype=int)
        ga = usable["subject_id"].astype(str).to_numpy()
        analyses["gate_failures_included"] = {
            "model": chosen, "n_added": n_gate_failed,
            "result": oof_for_subset(spec, Xa, ya, ga, n_splits, seed, n_inner, n_boot, f"{chosen}-allrows"),
        }
    # healthy controls vs each PD recording session separately (channel-confound check, same OOF)
    sess = train_df["session"].astype(str).to_numpy()
    analyses["healthy_vs_each_pd_session"] = {}
    for s in sorted(set(sess[y == 1])):
        m = (y == 0) | (sess == s)
        analyses["healthy_vs_each_pd_session"][s] = auc_block(y[m], oofs[chosen][m], g[m], n_boot, seed)
    analyses["v1_scored_on_this_audio"] = v1_on_features(train_df)
    analyses["channel_proxy"] = channel_proxy(X, sess, y, g, n_splits, seed)
    analyses["leave_one_session_out"] = leave_one_session_out(specs[chosen], X, y, g, sess, n_inner, seed)
    analyses["univariate_auc_note"] = "see feature_scale_comparison[*].univariate_auc (>0.5 means higher values in PD)"

    scale_rows = feature_scale_comparison(train_df)
    sessions = {}
    for s, grp in table.groupby("session"):
        sessions[str(s)] = {
            "n_recordings": int(len(grp)), "n_subjects": int(grp["subject_id"].nunique()),
            "label": int(grp["label"].iloc[0]) if grp["label"].nunique() == 1 else "mixed",
            "sample_rates": sorted(int(v) for v in grp["sr"].dropna().unique()),
            "n_gate_failed": int((~grp["quality_ok"]).sum()),
            "median_snr_db": float(grp["snr_db"].median()), "median_duration_s": float(grp["duration_s"].median()),
            "median_level_db": float(grp["level_db"].median()), "median_lf_ratio_db": float(grp["lf_ratio_db"].median()),
        }

    metrics: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "name": DATASET_NAME if Path(data).is_dir() else f"manifest {Path(data).name}",
            "slug": DATASET_SLUG,
            "licence": DATASET_LICENCE,
            "language": DATASET_LANGUAGE,
            "source": str(data),
            "task": "sustained /a/ phonation (VA1/VA2 takes)",
            "n_recordings_found": int(len(table)),
            "n_extract_errors": int((table["extract_error"] != "").sum()),
            "n_gate_failed": n_gate_failed,
            "gate_failure_reasons": {k: int(v) for k, v in usable.loc[~usable["quality_ok"], "quality_reasons"].value_counts().items()},
            "gate_failures_by_label": {str(k): int(v) for k, v in usable.loc[~usable["quality_ok"], "label"].value_counts().items()},
            "n_recordings": int(len(y)),
            "n_subjects": int(len(np.unique(g))),
            "n_pd_subjects": int(len(np.unique(g[y == 1]))),
            "n_healthy_subjects": int(len(np.unique(g[y == 0]))),
            "n_pd_recordings": int(y.sum()),
            "subjects_by_sex": {str(k): int(v) for k, v in train_df.groupby("sex")["subject_id"].nunique().items()} if train_df["sex"].notna().any() else {},
            "sessions": sessions,
            "nan_counts_per_feature": {k: int(train_df[k].isna().sum()) for k in UCI_FEATURES},
            "include_gate_failures": include_gate_failures,
        },
        "feature_extractor": {
            "module": "app.ml.features.vowel.extract_vowel_features",
            "quality_gate": "app.ml.features.quality.assess_quality (voiced >= 3 s, SNR >= 15 dB, no clipping)",
            "train_serve_skew": "none: training features were computed by the same extractor the app runs at serving, on the recording's native sample rate",
        },
        "cv": {
            "outer": f"StratifiedGroupKFold(n_splits={n_splits}, shuffle) by subject",
            "inner": f"StratifiedGroupKFold(n_splits<={n_inner}) by subject for GridSearchCV and sigmoid CalibratedClassifierCV",
            "repeats": n_repeats,
            "bootstrap": f"{n_boot} subject-cluster resamples",
            "imputation": "SimpleImputer(median) inside each pipeline (fit on the training fold)",
        },
        "candidates": results,
        "chosen_model": chosen,
        "threshold": results[chosen]["threshold_at_80_specificity"],
        "threshold_rule": "predict 'above threshold' if calibrated probability >= threshold; chosen on pooled out-of-fold recording-level probabilities for 80% specificity",
        "aggregation": "median probability over the 3 repetitions of a session",
        "features": feat,
        "feature_subset": features if isinstance(features, str) else ("all" if features is None else "custom"),
        "f0_features": F0_FEATURES,
        "analyses": analyses,
        "feature_scale_comparison": scale_rows,
        "feature_notes_approximate": [k for k, v in FEATURE_NOTES.items() if v["approximate"]],
        "library_versions": library_versions(),
        "fast_mode": fast,
    }

    if out_dir is not None:
        import joblib

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        pipe, grid = specs[chosen]
        final, info = fit_selected_calibrated(pipe, grid, X, y, g, n_inner=5 if not fast else 3, seed=seed)
        metrics["final_fit"] = {"best_params": info["best_params"], "inner_auc": info["inner_auc"], "n_train": int(len(y))}
        joblib.dump(final, out / "model.joblib")
        (out / "feature_order.json").write_text(
            json.dumps({"features": feat, "train_medians": {k: float(X[k].median()) for k in feat}}, indent=2)
        )
        notes = feature_notes_summary()
        notes["overall"] = (
            f"{MODEL_VERSION} was trained on features computed from audio by app/ml/features/vowel.py — the same "
            "code that runs at serving — so there is no train/serve feature skew. The per-feature notes below "
            "describe how our implementation differs from the original MDVP/Little definitions; they matter only "
            "when comparing values with the UCI table, not for serving this model."
        )
        notes["train_serve_skew"] = metrics["feature_extractor"]["train_serve_skew"]
        (out / "feature_notes.json").write_text(json.dumps(notes, indent=2))
        metrics["train_seconds"] = round(time.time() - t0, 1)
        (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=_json_default))
        write_model_card(metrics, out / "README.md")
    metrics["train_seconds"] = round(time.time() - t0, 1)
    return metrics


# --------------------------------------------------------------------------- model card

def _block_row(label: str, b: dict[str, Any] | None) -> str:
    if not b:
        return f"| {label} | n/a (too few subjects) | | | | |"
    lo, hi = b["auc_ci95_subject_bootstrap"]
    ci = f"{_fmt(lo)}–{_fmt(hi)}" if lo is not None else "n/a"
    return (
        f"| {label} | {b['n_recordings']} / {b['n_subjects']} ({b['n_pd_subjects']} PD, {b['n_healthy_subjects']} HC) | "
        f"{_fmt(b['auc'])} | {ci} | {_fmt(b['subject_auc_median_prob'])} | {_fmt(b['sensitivity_at_80_specificity'])} |"
    )


def write_model_card(metrics: dict, path: Path, template: Path = CARD_TEMPLATE) -> str:
    """Fill ``model_card_pd_vowel_audio.md`` placeholders ``{{key}}`` and write the card."""
    c = metrics["candidates"]
    ch = metrics["chosen_model"]
    best = c[ch]
    ds = metrics["dataset"]
    an = metrics["analyses"]

    def row(name: str) -> str:
        r = c[name]
        rep = r.get("repeated_cv")
        rep_s = f"{_fmt(rep['mean'])} ± {_fmt(rep['sd'])} (n={rep['n_repeats']})" if rep else "n/a"
        lo, hi = r["auc_ci95_subject_bootstrap"]
        return (
            f"| {name} | {_fmt(r['auc_pooled_oof'])} | {_fmt(lo)}–{_fmt(hi)} | {rep_s} | "
            f"{_fmt(r['sensitivity_at_80_specificity'])} | {_fmt(r['brier'])} | {_fmt(r['subject_level']['auc_median_prob'])} |"
        )

    sens_rows = [_block_row(f"all features ({ch})", auc_block_from_candidate(best))]
    sens_rows.append(_block_row(f"without F0 features ({ch})", an["without_f0_features"].get(ch)))
    for s, b in sorted(an["by_sex"].get(ch, {}).items()):
        sens_rows.append(_block_row(f"sex = {s} only ({ch}, same OOF)", b))
    if "single_sample_rate_subset" in an:
        sub = an["single_sample_rate_subset"]
        sens_rows.append(_block_row(f"{sub['sample_rate']} Hz recordings only (drops {', '.join(sub['dropped_sessions'])})", sub["result"]))
    if "gate_failures_included" in an:
        sub = an["gate_failures_included"]
        sens_rows.append(_block_row(f"quality-gate failures included (+{sub['n_added']})", sub["result"]))
    for s, b in sorted(an.get("healthy_vs_each_pd_session", {}).items()):
        sens_rows.append(_block_row(f"healthy controls vs PD session {s} only ({ch}, same OOF)", b))
    cp = an.get("channel_proxy")
    if cp and "macro_ovr_auc" in cp:
        pairs = "; ".join(f"{k}: AUC {_fmt(v['auc'])}" for k, v in cp["pairwise_same_label"].items()) or "none"
        channel_text = (
            f"A subject-grouped logistic regression predicting the recording session from these features reaches "
            f"macro one-vs-rest AUC {_fmt(cp['macro_ovr_auc'])} and balanced accuracy {_fmt(cp['balanced_accuracy'])} "
            f"(chance {cp['chance_balanced_accuracy']:.2f}). Same-label session pairs (pure channel): {pairs}."
        )
    else:
        channel_text = "Not computed (fewer than two sessions)."
    loso = an.get("leave_one_session_out")
    if loso and loso.get("splits"):
        loso_rows = "\n".join(
            f"| {k} | {v['n_train']} / {v['n_test']} | {_fmt(v['auc'])} | {_fmt(v['subject_auc_median_prob'])} | "
            f"{_fmt(v['sensitivity_heldout_pd_session'])} | {_fmt(v['specificity_heldout_hc_half'])} |"
            for k, v in loso["splits"].items()
        )
        loso_text = (
            "| split | n train / n test | AUC | subject AUC | sens (held-out PD session) | spec (held-out HC half) |\n|---|---|---|---|---|---|\n"
            + loso_rows + f"\n\nMean AUC {_fmt(loso['mean_auc'])}; excluded multi-session subjects: {', '.join(loso['excluded_multi_session_subjects']) or 'none'}."
        )
    else:
        loso_text = "Not computed (needs two PD sessions and one healthy session)."
    v1 = an.get("v1_scored_on_this_audio")
    v1_text = (
        f"The served `{v1['model_version']}` (trained on the UCI table) scored on these app-extracted features "
        f"reaches AUC {_fmt(v1['auc'])} (subject-level {_fmt(v1['subject_auc_median_prob'])}); at its own threshold "
        f"{_fmt(v1['v1_threshold'], 4)} it gives sensitivity {_fmt(v1['sensitivity_at_v1_threshold'])} and specificity "
        f"{_fmt(v1['specificity_at_v1_threshold'])} (mean probability {_fmt(v1['mean_prob_healthy'])} for controls, "
        f"{_fmt(v1['mean_prob_pd'])} for PD)."
        if v1 else "The v1 model was not available to score on this data."
    )
    session_rows = "\n".join(
        f"| {s} | {v['label']} | {v['n_recordings']} / {v['n_subjects']} | {', '.join(str(x) for x in v['sample_rates'])} | "
        f"{v['n_gate_failed']} | {_fmt(v['median_snr_db'], 1)} | {_fmt(v['median_level_db'], 1)} | {_fmt(v['median_lf_ratio_db'], 1)} | "
        f"{_fmt(an['mean_oof_prob_by_session'][ch].get(s, {}).get('mean_prob'))} |"
        for s, v in ds["sessions"].items()
    )
    scale_rows = "\n".join(
        f"| `{r['feature']}` | {_g(r['this_hc_median'])} | {_g(r['this_hc_iqr'][0])}–{_g(r['this_hc_iqr'][1])} | {_g(r['this_pd_median'])} | "
        f"{_g(r['uci_healthy_median'])} | {_g(r['v1_train_median_uci_all'])} | {_g(r['ratio_hc_to_uci_healthy'], 2)} | "
        f"{_g(r['univariate_auc'], 2)} | {'**yes**' if r['scale_mismatch'] else 'no'} |"
        for r in metrics["feature_scale_comparison"]
    )
    notes = "\n".join(
        f"| `{k}` | {'yes' if v['approximate'] else 'no'} | {v['method']} | {v['difference']} |" for k, v in FEATURE_NOTES.items()
    )
    lib_rows = "\n".join(f"| {k} | {v} |" for k, v in metrics["library_versions"].items())
    cm = best["confusion_at_0.5"]
    mismatched = [r["feature"] for r in metrics["feature_scale_comparison"] if r["scale_mismatch"]]
    nan_total = sum(ds["nan_counts_per_feature"].values())
    values = {
        "model_version": metrics["model_version"],
        "created_utc": metrics["created_utc"],
        "dataset_name": ds["name"],
        "licence": ds["licence"],
        "language": ds["language"],
        "n_found": str(ds["n_recordings_found"]),
        "n_gate_failed": str(ds["n_gate_failed"]),
        "gate_failures_by_label": json.dumps(ds["gate_failures_by_label"]),
        "n_recordings": str(ds["n_recordings"]),
        "n_subjects": str(ds["n_subjects"]),
        "n_pd_subjects": str(ds["n_pd_subjects"]),
        "n_healthy_subjects": str(ds["n_healthy_subjects"]),
        "subjects_by_sex": json.dumps(ds["subjects_by_sex"]),
        "nan_summary": "none" if nan_total == 0 else json.dumps({k: v for k, v in ds["nan_counts_per_feature"].items() if v}),
        "chosen_model": ch,
        "best_params": json.dumps(metrics.get("final_fit", {}).get("best_params", {})),
        "metrics_rows": "\n".join(row(n) for n in c),
        "sensitivity_rows": "\n".join(sens_rows),
        "v1_on_this_audio": v1_text,
        "channel_proxy_text": channel_text,
        "loso_text": loso_text,
        "feature_subset": metrics.get("feature_subset", "all"),
        "n_features": str(len(metrics["features"])),
        "session_rows": session_rows,
        "scale_rows": scale_rows,
        "mismatched_features": ", ".join(f"`{m}`" for m in mismatched) or "none",
        "threshold": _fmt(metrics["threshold"], 4),
        "sens_at_spec": _fmt(best["sensitivity_at_80_specificity"]),
        "achieved_spec": _fmt(best["achieved_specificity"]),
        "cm_tn": str(cm["tn"]), "cm_fp": str(cm["fp"]), "cm_fn": str(cm["fn"]), "cm_tp": str(cm["tp"]),
        "subj_sens": _fmt(best["subject_level"]["sensitivity_at_threshold"]),
        "subj_spec": _fmt(best["subject_level"]["specificity_at_threshold"]),
        "cv_outer": metrics["cv"]["outer"],
        "cv_inner": metrics["cv"]["inner"],
        "feature_notes_rows": notes,
        "library_rows": lib_rows,
        "features": ", ".join(f"`{f}`" for f in metrics["features"]),
    }
    text = template.read_text()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    path.write_text(text)
    return text


def auc_block_from_candidate(r: dict[str, Any]) -> dict[str, Any]:
    """Shape a ``summarize`` result like :func:`auc_block` (for the sensitivity table)."""
    subj = r["subject_level"]["per_subject"]
    return {
        "n_recordings": sum(s["n_recordings"] for s in subj),
        "n_subjects": len(subj),
        "n_pd_subjects": sum(1 for s in subj if s["status"] == 1),
        "n_healthy_subjects": sum(1 for s in subj if s["status"] == 0),
        "auc": r["auc_pooled_oof"],
        "auc_ci95_subject_bootstrap": r["auc_ci95_subject_bootstrap"],
        "subject_auc_median_prob": r["subject_level"]["auc_median_prob"],
        "sensitivity_at_80_specificity": r["sensitivity_at_80_specificity"],
    }


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="extracted dataset dir (Italian layout) or manifest CSV")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--cache", default=str(DEFAULT_CACHE), help="feature-table CSV ('none' to disable)")
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--jobs", type=int, default=4, help="parallel workers for feature extraction")
    ap.add_argument("--fast", action="store_true", help="tiny grids (smoke test)")
    ap.add_argument("--recompute-features", action="store_true", help="ignore the feature cache")
    ap.add_argument("--include-gate-failures", action="store_true", help="also train on recordings failing assess_quality")
    ap.add_argument("--features", default="all", help=f"subset name {list(FEATURE_SUBSETS)} or comma list of UCI feature names")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    m = run_training(
        args.data, args.out, args.splits, args.repeats, args.n_boot, args.fast, args.seed,
        cache=None if args.cache.lower() == "none" else args.cache, force_features=args.recompute_features,
        n_jobs=args.jobs, include_gate_failures=args.include_gate_failures, features=args.features,
    )
    print(f"features ({m['feature_subset']}): {', '.join(m['features'])}")
    ds = m["dataset"]
    print(
        f"dataset: {ds['n_recordings_found']} recordings found, {ds['n_extract_errors']} extraction errors, "
        f"{ds['n_gate_failed']} failed the quality gate {ds['gate_failures_by_label']}; trained on {ds['n_recordings']} recordings / "
        f"{ds['n_subjects']} subjects ({ds['n_pd_subjects']} PD, {ds['n_healthy_subjects']} HC)"
    )
    for name, r in m["candidates"].items():
        lo, hi = r["auc_ci95_subject_bootstrap"]
        rep = r.get("repeated_cv", {})
        print(
            f"{name:8s} AUC={r['auc_pooled_oof']:.3f} [95% CI {lo:.3f}-{hi:.3f}] subjAUC={r['subject_level']['auc_median_prob']:.3f} "
            f"fold-mean={_fmt(r['auc_fold_mean'])} repeated={_fmt(rep.get('mean'))}±{_fmt(rep.get('sd'))} "
            f"sens@80spec={r['sensitivity_at_80_specificity']:.3f} thr={r['threshold_at_80_specificity']:.4f} "
            f"brier={r['brier']:.3f} cm@0.5={r['confusion_at_0.5']}"
        )
    an = m["analyses"]
    for name, b in an["without_f0_features"].items():
        print(f"{name:8s} without F0: AUC={_fmt(b and b['auc'])} CI={b and [round(x, 3) for x in b['auc_ci95_subject_bootstrap'] if x is not None]} subjAUC={_fmt(b and b['subject_auc_median_prob'])}")
    for name, d in an["by_sex"].items():
        for s, b in d.items():
            print(f"{name:8s} sex={s}: AUC={_fmt(b and b['auc'])} n_subj={b and b['n_subjects']} ({b and b['n_pd_subjects']} PD)")
    for key in ("single_sample_rate_subset", "gate_failures_included"):
        if key in an:
            b = an[key]["result"]
            print(f"{key}: AUC={_fmt(b and b['auc'])} CI={b and [round(x, 3) for x in b['auc_ci95_subject_bootstrap'] if x is not None]} subjAUC={_fmt(b and b['subject_auc_median_prob'])}")
    print("mean OOF prob by session:", json.dumps(an["mean_oof_prob_by_session"][m["chosen_model"]]))
    for s, b in an.get("healthy_vs_each_pd_session", {}).items():
        print(f"healthy vs PD session {s}: AUC={_fmt(b and b['auc'])} subjAUC={_fmt(b and b['subject_auc_median_prob'])} n_subj={b and b['n_subjects']}")
    if an.get("v1_scored_on_this_audio"):
        print("v1 scored on this audio:", json.dumps(an["v1_scored_on_this_audio"]))
    if an.get("channel_proxy"):
        cp = an["channel_proxy"]
        print(f"channel proxy: macro OVR AUC={_fmt(cp.get('macro_ovr_auc'))} balanced acc={_fmt(cp.get('balanced_accuracy'))} "
              f"(chance {cp['chance_balanced_accuracy']:.2f}) same-label pairs={json.dumps({k: round(v['auc'], 3) for k, v in cp['pairwise_same_label'].items()})}")
    if an.get("leave_one_session_out"):
        for k, v in an["leave_one_session_out"]["splits"].items():
            print(f"LOSO {k}: AUC={v['auc']:.3f} subjAUC={v['subject_auc_median_prob']:.3f} sens(held-out PD)={v['sensitivity_heldout_pd_session']:.2f} spec(held-out HC)={v['specificity_heldout_hc_half']:.2f}")
    print(scale_table_text(m["feature_scale_comparison"]))
    print(f"chosen={m['chosen_model']} threshold={m['threshold']:.4f} time={m['train_seconds']}s -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
