"""Train the experimental Parkinson's sustained-vowel classifier on UCI Parkinson's.

Spec: docs/specs/phase-a-assessments.md §5.4 and docs/BACKEND_PLAN.md A4.
Dataset: Little MA et al. (2009) "Suitability of dysphonia measurements for
telemonitoring of Parkinson's disease", IEEE TBME 56(4) — 195 sustained-vowel
recordings from 31 subjects (23 PD).

Evaluation design (everything subject-grouped, because each subject has ~6 recordings):

* Outer ``GroupKFold(n_splits=5)`` by subject. Inside each outer training fold:
  hyper-parameter / feature-number selection with ``GridSearchCV`` on an inner
  ``StratifiedGroupKFold`` (still by subject), then ``CalibratedClassifierCV``
  (sigmoid) on inner subject-grouped splits. Nothing sees the outer test fold.
* Repeated grouped CV (``GroupKFold(shuffle=True)`` with different seeds) for
  the spread of AUC across partitions.
* Subject-cluster bootstrap 95% CI of the pooled out-of-fold AUC.
* Threshold at 80% specificity chosen on pooled out-of-fold calibrated
  probabilities; stored in metrics.json and used at serving.

Usage (from backend/)::

    .venv/bin/python -m ml_training.train_pd_vowel [--repeats 3] [--push-to-hub ORG/REPO]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:  # allow `python ml_training/train_pd_vowel.py`
    sys.path.insert(0, str(BACKEND_DIR))

from app.ml.features.vowel import FEATURE_NOTES, UCI_FEATURES, feature_notes_summary  # noqa: E402

logger = logging.getLogger("train_pd_vowel")

DEFAULT_DATA = BACKEND_DIR / "data" / "raw" / "uci_parkinsons" / "parkinsons.data"
DEFAULT_OUT = BACKEND_DIR / "models" / "pd_vowel_experimental_v1"
CARD_TEMPLATE = Path(__file__).resolve().parent / "model_card_pd_vowel.md"
MODEL_VERSION = "pd_vowel_experimental_v1"
DEFAULT_HUB_REPO = "hackthevalley/pd-vowel-experimental-v1"
TARGET_SPECIFICITY = 0.80


# --------------------------------------------------------------------------- data

def load_uci(path: Path | str = DEFAULT_DATA) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, str]:
    """Load the UCI CSV with Hugging Face ``datasets`` (pandas fallback).

    Returns (X with UCI_FEATURES columns, y status, subject groups, loader name).
    Subject id = ``name`` without the trailing ``_<recording>`` suffix.
    """
    path = str(path)
    loader = "datasets"
    try:
        from datasets import load_dataset

        ds = load_dataset("csv", data_files=path, split="train")
        df = ds.to_pandas()
    except Exception as exc:  # noqa: BLE001 - e.g. read-only HF cache
        logger.warning("datasets.load_dataset failed (%s); falling back to pandas", exc)
        df = pd.read_csv(path)
        loader = "pandas"
    df["subject"] = df["name"].astype(str).str.rsplit("_", n=1).str[0]
    X = df[UCI_FEATURES].astype(float)
    y = df["status"].astype(int).to_numpy()
    groups = df["subject"].to_numpy()
    return X, y, groups, loader


# --------------------------------------------------------------------------- models

def model_specs(fast: bool = False) -> dict[str, tuple[Any, dict[str, list]]]:
    """Candidate pipelines and their (small) hyper-parameter grids."""
    from sklearn.feature_selection import SelectKBest, f_classif
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from xgboost import XGBClassifier

    logreg = Pipeline(
        [
            ("scale", StandardScaler()),
            ("select", SelectKBest(f_classif)),
            ("clf", LogisticRegression(max_iter=5000)),
        ]
    )
    xgb = Pipeline(
        [
            (
                "clf",
                XGBClassifier(
                    subsample=0.8,
                    colsample_bytree=0.8,
                    eval_metric="logloss",
                    tree_method="hist",
                    n_jobs=1,
                    random_state=0,
                    verbosity=0,
                ),
            )
        ]
    )
    if fast:
        lr_grid = {"select__k": [10, "all"], "clf__C": [0.1, 1.0]}
        xgb_grid = {"clf__n_estimators": [100], "clf__max_depth": [2], "clf__learning_rate": [0.1]}
    else:
        lr_grid = {"select__k": [8, 15, "all"], "clf__C": [0.01, 0.1, 1.0, 10.0]}
        xgb_grid = {
            "clf__n_estimators": [100, 300],
            "clf__max_depth": [2, 3],
            "clf__learning_rate": [0.05, 0.1],
        }
    return {"logreg": (logreg, lr_grid), "xgboost": (xgb, xgb_grid)}


def _inner_splits(X: pd.DataFrame, y: np.ndarray, g: np.ndarray, n_inner: int, seed: int) -> list:
    from sklearn.model_selection import StratifiedGroupKFold

    n_inner = max(2, min(n_inner, len(np.unique(g[y == 0])), len(np.unique(g[y == 1]))))
    return list(StratifiedGroupKFold(n_splits=n_inner, shuffle=True, random_state=seed).split(X, y, g))


def fit_selected_calibrated(
    pipe: Any, grid: dict, X: pd.DataFrame, y: np.ndarray, g: np.ndarray, n_inner: int = 4, seed: int = 0
) -> tuple[Any, dict]:
    """Grouped inner grid search, then sigmoid calibration on grouped inner splits.

    Used both inside every outer fold and for the final all-data fit.
    """
    from sklearn.base import clone
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import GridSearchCV

    splits = _inner_splits(X, y, g, n_inner, seed)
    gs = GridSearchCV(clone(pipe), grid, scoring="roc_auc", cv=splits, n_jobs=-1, refit=False)
    gs.fit(X, y)
    best = clone(pipe).set_params(**gs.best_params_)
    cal_splits = _inner_splits(X, y, g, n_inner, seed + 1)
    cal = CalibratedClassifierCV(best, method="sigmoid", cv=cal_splits)
    cal.fit(X, y)
    return cal, {"best_params": gs.best_params_, "inner_auc": float(gs.best_score_)}


def grouped_oof(
    name: str,
    spec: tuple[Any, dict],
    X: pd.DataFrame,
    y: np.ndarray,
    g: np.ndarray,
    outer_splits: list,
    seed: int = 0,
    n_inner: int = 4,
) -> tuple[np.ndarray, list[dict]]:
    """Out-of-fold calibrated probabilities with all selection/calibration inside folds."""
    from sklearn.metrics import roc_auc_score

    pipe, grid = spec
    oof = np.full(len(y), np.nan)
    folds = []
    for k, (tr, te) in enumerate(outer_splits):
        assert not set(g[tr]) & set(g[te]), "subject leakage between folds"
        model, info = fit_selected_calibrated(pipe, grid, X.iloc[tr], y[tr], g[tr], n_inner, seed + k)
        oof[te] = model.predict_proba(X.iloc[te])[:, 1]
        auc = float(roc_auc_score(y[te], oof[te])) if len(np.unique(y[te])) == 2 else None
        folds.append({"fold": k, "n_test": int(len(te)), "n_test_subjects": int(len(set(g[te]))), "auc": auc, **info})
        logger.info("%s fold %d auc=%s params=%s", name, k, auc, info["best_params"])
    return oof, folds


# --------------------------------------------------------------------------- metrics

def threshold_at_specificity(y: np.ndarray, p: np.ndarray, spec: float = TARGET_SPECIFICITY) -> tuple[float, float, float]:
    """Smallest threshold t (predict PD if p >= t) giving specificity >= ``spec``.

    Returns (threshold, sensitivity, achieved specificity).
    """
    neg = np.sort(p[y == 0])
    pos = p[y == 1]
    cands = np.unique(np.concatenate([p, [1.0 + 1e-9]]))
    for t in cands:
        sp = float(np.mean(neg < t))
        if sp >= spec:
            return float(t), float(np.mean(pos >= t)), sp
    return 1.0, 0.0, 1.0


def subject_bootstrap_auc(y: np.ndarray, p: np.ndarray, g: np.ndarray, n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% percentile CI of AUC, resampling subjects (clusters) with replacement."""
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    subjects = np.unique(g)
    idx_by_subj = {s: np.flatnonzero(g == s) for s in subjects}
    aucs = []
    for _ in range(n_boot):
        pick = rng.choice(subjects, size=len(subjects), replace=True)
        idx = np.concatenate([idx_by_subj[s] for s in pick])
        if len(np.unique(y[idx])) < 2:
            continue
        aucs.append(roc_auc_score(y[idx], p[idx]))
    lo, hi = np.percentile(aucs, [2.5, 97.5])
    return float(lo), float(hi)


def summarize(y: np.ndarray, p: np.ndarray, g: np.ndarray, folds: list[dict], n_boot: int, seed: int) -> dict:
    """AUC (+subject bootstrap CI), sens@80%spec, Brier, confusion@0.5, per-subject summary."""
    from sklearn.metrics import brier_score_loss, confusion_matrix, roc_auc_score

    auc = float(roc_auc_score(y, p))
    lo, hi = subject_bootstrap_auc(y, p, g, n_boot, seed)
    thr, sens, spec = threshold_at_specificity(y, p)
    tn, fp, fn, tp = confusion_matrix(y, (p >= 0.5).astype(int), labels=[0, 1]).ravel()
    fold_aucs = [f["auc"] for f in folds if f["auc"] is not None]
    subj = (
        pd.DataFrame({"subject": g, "y": y, "p": p})
        .groupby("subject")
        .agg(status=("y", "first"), n_recordings=("y", "size"), median_prob=("p", "median"), min_prob=("p", "min"), max_prob=("p", "max"))
        .reset_index()
    )
    subj_auc = float(roc_auc_score(subj["status"], subj["median_prob"]))
    subj_sens = float(np.mean(subj.loc[subj.status == 1, "median_prob"] >= thr))
    subj_spec = float(np.mean(subj.loc[subj.status == 0, "median_prob"] < thr))
    return {
        "auc_pooled_oof": auc,
        "auc_ci95_subject_bootstrap": [lo, hi],
        "auc_fold_mean": float(np.mean(fold_aucs)) if fold_aucs else None,
        "auc_fold_sd": float(np.std(fold_aucs, ddof=1)) if len(fold_aucs) > 1 else None,
        "threshold_at_80_specificity": thr,
        "sensitivity_at_80_specificity": sens,
        "achieved_specificity": spec,
        "brier": float(brier_score_loss(y, p)),
        "confusion_at_0.5": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "subject_level": {
            "auc_median_prob": subj_auc,
            "sensitivity_at_threshold": subj_sens,
            "specificity_at_threshold": subj_spec,
            "per_subject": subj.round(4).to_dict(orient="records"),
        },
        "folds": folds,
    }


# --------------------------------------------------------------------------- driver

def run_training(
    data_path: Path | str = DEFAULT_DATA,
    out_dir: Path | str | None = DEFAULT_OUT,
    n_splits: int = 5,
    n_repeats: int = 10,
    n_boot: int = 2000,
    fast: bool = False,
    seed: int = 42,
) -> dict[str, Any]:
    """Run grouped nested CV for all candidates, pick the best, refit, save artefacts.

    Args:
        data_path: UCI ``parkinsons.data`` CSV.
        out_dir: artefact directory (model.joblib, metrics.json, feature_order.json,
            README.md model card). ``None`` skips saving and the final refit.
        n_splits: outer GroupKFold splits (5 in the spec; 2 in the smoke test).
        n_repeats: extra shuffled GroupKFold repetitions per model (0 to skip).
        n_boot: subject-bootstrap resamples for the AUC CI.
        fast: tiny grids (tests).

    Returns:
        metrics dict (also written to metrics.json).
    """
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import GroupKFold

    warnings.filterwarnings("ignore", category=UserWarning)
    t0 = time.time()
    X, y, g, loader = load_uci(data_path)
    specs = model_specs(fast=fast)
    n_inner = 3 if fast else 4
    outer = list(GroupKFold(n_splits=n_splits).split(X, y, g))

    results: dict[str, Any] = {}
    oofs: dict[str, np.ndarray] = {}
    for name, spec in specs.items():
        oof, folds = grouped_oof(name, spec, X, y, g, outer, seed, n_inner)
        oofs[name] = oof
        res = summarize(y, oof, g, folds, n_boot, seed)
        rep_aucs = []
        for r in range(n_repeats):
            rs = list(GroupKFold(n_splits=n_splits, shuffle=True, random_state=seed + 100 + r).split(X, y, g))
            rp, _ = grouped_oof(f"{name}-rep{r}", spec, X, y, g, rs, seed + 100 + r, n_inner)
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

    chosen = max(results, key=lambda k: results[k]["auc_pooled_oof"])
    metrics: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": {
            "name": "UCI Parkinson's (Little et al. 2008/2009)",
            "loader": loader,
            "n_recordings": int(len(y)),
            "n_subjects": int(len(np.unique(g))),
            "n_pd_subjects": int(len(np.unique(g[y == 1]))),
            "n_healthy_subjects": int(len(np.unique(g[y == 0]))),
            "n_pd_recordings": int(y.sum()),
            "documented_subjects": "31 subjects (23 PD) per parkinsons.names; subject IDs derived from `name` give the counts above",
        },
        "cv": {
            "outer": f"GroupKFold(n_splits={n_splits}) by subject",
            "inner": f"StratifiedGroupKFold(n_splits<={n_inner}) by subject for GridSearchCV and sigmoid CalibratedClassifierCV",
            "repeats": n_repeats,
            "bootstrap": f"{n_boot} subject-cluster resamples",
        },
        "candidates": results,
        "chosen_model": chosen,
        "threshold": results[chosen]["threshold_at_80_specificity"],
        "threshold_rule": "predict 'above threshold' if calibrated probability >= threshold; chosen on pooled out-of-fold recording-level probabilities for 80% specificity",
        "aggregation": "median probability over the 3 repetitions of a session",
        "features": UCI_FEATURES,
        "feature_notes_approximate": [k for k, v in FEATURE_NOTES.items() if v["approximate"]],
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
            json.dumps(
                {"features": UCI_FEATURES, "train_medians": {k: float(X[k].median()) for k in UCI_FEATURES}},
                indent=2,
            )
        )
        metrics["train_seconds"] = round(time.time() - t0, 1)
        (out / "metrics.json").write_text(json.dumps(metrics, indent=2, default=_json_default))
        (out / "feature_notes.json").write_text(json.dumps(feature_notes_summary(), indent=2))
        write_model_card(metrics, out / "README.md")
    metrics["train_seconds"] = round(time.time() - t0, 1)
    return metrics


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)


def _fmt(x: float | None, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def write_model_card(metrics: dict, path: Path, template: Path = CARD_TEMPLATE) -> str:
    """Fill ``model_card_pd_vowel.md`` placeholders ``{{key}}`` and write the card."""
    c = metrics["candidates"]
    ch = metrics["chosen_model"]
    best = c[ch]

    def row(name: str) -> str:
        r = c[name]
        rep = r.get("repeated_cv")
        rep_s = f"{_fmt(rep['mean'])} ± {_fmt(rep['sd'])} (n={rep['n_repeats']})" if rep else "n/a"
        lo, hi = r["auc_ci95_subject_bootstrap"]
        return (
            f"| {name} | {_fmt(r['auc_pooled_oof'])} | {_fmt(lo)}–{_fmt(hi)} | {rep_s} | "
            f"{_fmt(r['sensitivity_at_80_specificity'])} | {_fmt(r['brier'])} | {_fmt(r['subject_level']['auc_median_prob'])} |"
        )

    notes = "\n".join(
        f"| `{k}` | {'yes' if v['approximate'] else 'no'} | {v['method']} | {v['difference']} |" for k, v in FEATURE_NOTES.items()
    )
    cm = best["confusion_at_0.5"]
    ds = metrics["dataset"]
    values = {
        "model_version": metrics["model_version"],
        "created_utc": metrics["created_utc"],
        "n_recordings": str(ds["n_recordings"]),
        "n_subjects": str(ds["n_subjects"]),
        "n_pd_subjects": str(ds["n_pd_subjects"]),
        "n_healthy_subjects": str(ds["n_healthy_subjects"]),
        "chosen_model": ch,
        "best_params": json.dumps(metrics.get("final_fit", {}).get("best_params", {})),
        "metrics_rows": "\n".join(row(n) for n in c),
        "threshold": _fmt(metrics["threshold"], 4),
        "sens_at_spec": _fmt(best["sensitivity_at_80_specificity"]),
        "achieved_spec": _fmt(best["achieved_specificity"]),
        "cm_tn": str(cm["tn"]),
        "cm_fp": str(cm["fp"]),
        "cm_fn": str(cm["fn"]),
        "cm_tp": str(cm["tp"]),
        "subj_sens": _fmt(best["subject_level"]["sensitivity_at_threshold"]),
        "subj_spec": _fmt(best["subject_level"]["specificity_at_threshold"]),
        "cv_outer": metrics["cv"]["outer"],
        "cv_inner": metrics["cv"]["inner"],
        "feature_notes_rows": notes,
        "features": ", ".join(f"`{f}`" for f in metrics["features"]),
    }
    text = template.read_text()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    path.write_text(text)
    return text


def _settings_hf_token() -> str:
    """``HF_TOKEN`` from backend/.env via the app settings (the file is not shell-sourceable)."""
    try:
        from app.core.config import settings

        return settings.hf_token or ""
    except Exception:  # noqa: BLE001 - training may run without the app package
        return ""


def push_to_hub(repo: str, folder: Path | str = DEFAULT_OUT) -> str:
    """Upload the artefact folder to the HF Hub when ``HF_TOKEN`` is set; otherwise describe the plan."""
    token = os.environ.get("HF_TOKEN") or _settings_hf_token()
    files = sorted(p.name for p in Path(folder).iterdir() if p.is_file())
    if not token:
        return f"[dry-run] HF_TOKEN not set; would create model repo '{repo}' (exist_ok) and upload {files} from {folder}"
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    # Private by default: experimental, 31-subject model; publish deliberately, not by accident.
    api.create_repo(repo_id=repo, repo_type="model", exist_ok=True, private=True)
    info = api.upload_folder(folder_path=str(folder), repo_id=repo, repo_type="model",
                             commit_message=f"Upload {MODEL_VERSION}")
    sha = getattr(info, "oid", None) or ""
    return (f"uploaded {files} to https://huggingface.co/{repo}"
            + (f"\npin it in .env: PD_VOWEL_HUB_REVISION={sha}" if sha else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--splits", type=int, default=5)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fast", action="store_true", help="tiny grids (smoke test)")
    ap.add_argument("--push-to-hub", metavar="REPO", nargs="?", const=DEFAULT_HUB_REPO, default=None)
    ap.add_argument("--push-only", action="store_true", help="skip training; push the existing --out folder")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.push_only:
        if not args.push_to_hub:
            ap.error("--push-only requires --push-to-hub [REPO]")
        print(push_to_hub(args.push_to_hub, args.out))
        return 0

    m = run_training(args.data, args.out, args.splits, args.repeats, args.n_boot, args.fast, args.seed)
    for name, r in m["candidates"].items():
        lo, hi = r["auc_ci95_subject_bootstrap"]
        rep = r.get("repeated_cv", {})
        print(
            f"{name:8s} AUC={r['auc_pooled_oof']:.3f} [95% CI {lo:.3f}-{hi:.3f}] "
            f"fold-mean={_fmt(r['auc_fold_mean'])} repeated={_fmt(rep.get('mean'))}±{_fmt(rep.get('sd'))} "
            f"sens@80spec={r['sensitivity_at_80_specificity']:.3f} thr={r['threshold_at_80_specificity']:.4f} "
            f"brier={r['brier']:.3f} cm@0.5={r['confusion_at_0.5']}"
        )
    print(f"chosen={m['chosen_model']} threshold={m['threshold']:.4f} time={m['train_seconds']}s -> {args.out}")
    if args.push_to_hub:
        print(push_to_hub(args.push_to_hub, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
