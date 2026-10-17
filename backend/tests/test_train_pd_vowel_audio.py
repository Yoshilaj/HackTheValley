"""Smoke tests for ``ml_training/train_pd_vowel_audio.py`` on tiny synthetic vowels (no download)."""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml.features.vowel import UCI_FEATURES
from app.ml.pd_vowel_model import PdVowelModel
from ml_training.train_pd_vowel_audio import (
    F0_FEATURES,
    MODEL_VERSION,
    build_feature_table,
    discover_italian,
    extract_one,
    feature_scale_comparison,
    load_manifest,
    run_training,
)
from tests.test_vowel_features import SR, synth_vowel


@pytest.fixture(scope="module")
def manifest(tmp_path_factory) -> Path:
    """3 'subjects' per class x 2 takes of a 4 s synthetic /a/; PD subjects get more jitter/shimmer."""
    import soundfile as sf

    root = tmp_path_factory.mktemp("synth")
    rows = []
    for label, (jit, shim, f0s) in enumerate([(0.003, 0.02, (110, 130, 200)), (0.02, 0.09, (115, 140, 210))]):
        for s, f0 in enumerate(f0s):
            for take in (1, 2):
                seed = 100 * label + 10 * s + take
                y = synth_vowel(sr=SR, f0=f0, dur=4.0, jitter=jit, shimmer=shim, pad=0.4, seed=seed)
                name = f"VA{take}_{'pd' if label else 'hc'}{s}.wav"
                sf.write(root / name, y, SR)
                rows.append(
                    {"path": name, "subject_id": f"{'pd' if label else 'hc'}_{s}", "label": label,
                     "sex": "F" if f0 >= 200 else "M", "take": take, "session": f"synth_{label}"}
                )
    p = root / "manifest.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_extract_one_uses_app_extractor(manifest):
    m = load_manifest(manifest)
    row = extract_one(m.iloc[0].to_dict())
    assert row["extract_error"] == "" and row["quality_ok"] and row["sr"] == SR
    assert all(np.isfinite(row[k]) for k in UCI_FEATURES)
    bad = extract_one({**m.iloc[0].to_dict(), "path": str(Path(manifest).parent / "missing.wav")})
    assert bad["extract_error"] and not bad["quality_ok"] and np.isnan(bad["PPE"])


def test_feature_table_cache(manifest, tmp_path):
    m = load_manifest(manifest)
    cache = tmp_path / "feats.csv"
    t = build_feature_table(m, cache=cache, n_jobs=1)
    assert len(t) == 12 and cache.exists() and set(UCI_FEATURES) <= set(t.columns)
    t2 = build_feature_table(m, cache=cache, n_jobs=1)  # served from cache
    assert t2["file"].tolist() == t["file"].tolist()
    rows = feature_scale_comparison(t)
    assert [r["feature"] for r in rows] == UCI_FEATURES
    assert all(r["univariate_auc"] is not None for r in rows)


def test_run_training_fast(manifest, tmp_path):
    t0 = time.time()
    m = run_training(manifest, out_dir=tmp_path, n_splits=3, n_repeats=1, n_boot=100, fast=True,
                     cache=tmp_path / "cache.csv", n_jobs=1)
    assert time.time() - t0 < 120
    assert m["model_version"] == MODEL_VERSION and set(m["candidates"]) == {"logreg", "xgboost"}
    ds = m["dataset"]
    assert ds["n_recordings"] == 12 and ds["n_subjects"] == 6 and ds["n_pd_subjects"] == 3 and ds["n_gate_failed"] == 0
    best = m["candidates"][m["chosen_model"]]
    # jitter/shimmer separate the synthetic classes within every fold; pooled OOF probabilities are
    # not comparable across folds here because sigmoid calibration on 8 recordings is degenerate
    assert m["candidates"]["logreg"]["auc_fold_mean"] > 0.8 and best["auc_pooled_oof"] > 0.5
    for key in ("auc_ci95_subject_bootstrap", "sensitivity_at_80_specificity", "brier", "confusion_at_0.5", "subject_level", "repeated_cv"):
        assert key in best
    an = m["analyses"]
    assert set(an["without_f0_features"]) == {"logreg", "xgboost"}
    nof0 = an["without_f0_features"]["logreg"]
    assert nof0["n_recordings"] == 12 and 0 <= nof0["auc"] <= 1 and 0 <= nof0["subject_auc_median_prob"] <= 1
    assert m["f0_features"] == F0_FEATURES and len(m["feature_scale_comparison"]) == 22
    assert set(an["mean_oof_prob_by_session"][m["chosen_model"]]) == {"synth_0", "synth_1"}
    assert m["feature_extractor"]["train_serve_skew"].startswith("none")
    for f in ("model.joblib", "metrics.json", "feature_order.json", "feature_notes.json", "README.md"):
        assert (tmp_path / f).exists()
    saved = json.loads((tmp_path / "metrics.json").read_text())
    assert 0 < saved["threshold"] < 1 and saved["library_versions"]["sklearn"]
    fo = json.loads((tmp_path / "feature_order.json").read_text())
    assert fo["features"] == UCI_FEATURES and set(fo["train_medians"]) == set(UCI_FEATURES)
    notes = json.loads((tmp_path / "feature_notes.json").read_text())
    assert "no train/serve feature skew" in notes["overall"]
    card = (tmp_path / "README.md").read_text()
    assert "{{" not in card and "no train/serve feature skew" in card and "scale mismatch" in card
    # the artefacts load and score through the app's serving wrapper
    model = PdVowelModel.load(tmp_path)
    assert model.model_version == MODEL_VERSION and model.feature_order == UCI_FEATURES
    feats = pd.read_csv(tmp_path / "cache.csv").iloc[0][UCI_FEATURES].to_dict()
    pred = model.predict_from_features(feats)
    assert 0 <= pred.probability <= 1 and pred.feature_vector_ok
    pred2 = model.predict_from_features({**feats, "D2": float("nan")})
    assert pred2.missing_features == ["D2"]


def test_feature_subsets(manifest, tmp_path):
    from ml_training.train_pd_vowel_audio import FEATURE_SUBSETS, resolve_features

    assert resolve_features(None) == UCI_FEATURES and resolve_features("all") == UCI_FEATURES
    robust = resolve_features("channel_robust")
    assert len(robust) == 9 and not any(f in robust for f in F0_FEATURES) and "MDVP:Shimmer" not in robust and "NHR" not in robust
    assert resolve_features("PPE,MDVP:Fo(Hz)") == ["MDVP:Fo(Hz)", "PPE"]  # UCI order kept
    with pytest.raises(ValueError):
        resolve_features("MDVP:Shimmer,not_a_feature")
    assert set(FEATURE_SUBSETS["channel_robust_f0"]) == set(robust) | set(F0_FEATURES)
    m = run_training(manifest, out_dir=tmp_path, n_splits=3, n_repeats=0, n_boot=50, fast=True,
                     cache=tmp_path / "cache.csv", n_jobs=1, features="channel_robust")
    assert m["features"] == robust and m["feature_subset"] == "channel_robust"
    assert m["analyses"]["without_f0_features"]["logreg"] is None  # no F0 features to drop
    cp = m["analyses"]["channel_proxy"]
    assert cp["sessions"] == ["synth_0", "synth_1"] and 0 <= cp["macro_ovr_auc"] <= 1
    assert m["analyses"]["leave_one_session_out"] is None  # needs two PD sessions
    fo = json.loads((tmp_path / "feature_order.json").read_text())
    assert fo["features"] == robust and list(fo["train_medians"]) == robust
    model = PdVowelModel.load(tmp_path)
    assert model.feature_order == robust
    pred = model.predict_from_features({k: 0.01 for k in robust})
    assert 0 <= pred.probability <= 1 and pred.feature_vector_ok
    assert "channel_robust" in (tmp_path / "README.md").read_text()


def test_discover_italian_layout_and_subject_merge(tmp_path):
    """File-code parsing: label from folder, sex/birth year from the code, same person across sessions."""
    import soundfile as sf

    y = np.zeros(1600)
    files = {
        "28 People with Parkinson's disease/1-5/Roberto R/VA1rriovbie49M2605161845.wav": None,  # 2016, 44.1 kHz session
        "28 People with Parkinson's disease/11-16/Roberto R/VA1RROIBVEI49M240120171904.wav": None,  # same person, 2017
        "28 People with Parkinson's disease/17-28/Vito L/VA2VLIAT OP47M100220171318.wav": None,
        "22 Elderly Healthy Control/AGNESE P/VA1APGANRET55F170320171107.wav": None,
        "22 Elderly Healthy Control/AGNESE P/B1APGANRET55F170320171104.wav": None,  # reading task: ignored
        "15 Young Healthy Control/Sara T/VA1LBULCAAS94M100120171066.wav": None,  # young controls: ignored
    }
    for rel in files:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        sf.write(p, y, 16000)
    df = discover_italian(tmp_path)
    assert len(df) == 4 and set(df["label"]) == {0, 1}
    assert df.loc[df.file.str.startswith("VA1r"), "subject_id"].iloc[0] == df.loc[df.file.str.startswith("VA1R"), "subject_id"].iloc[0]
    assert df["subject_id"].nunique() == 3 and set(df["session"]) == {"pd_2016", "pd_2017", "hc_2017"}
    agnese = df[df.file.str.startswith("VA1AP")].iloc[0]
    assert agnese["sex"] == "F" and agnese["birth_year"] == 1955 and agnese["label"] == 0 and agnese["take"] == 1
    assert not any("Roberto" in s or "AGNESE" in s for s in df["subject_id"])
