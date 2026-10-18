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


# --------------------------------------------------------------------------- audio preprocessing / augmentation

def _db(y: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(y**2))))


def test_preprocess_audio_synthetic_vowel():
    """Resample, crop to the central 3 s of phonation, fixed RMS; EQ toward a reference removes a channel tilt."""
    from scipy.signal import welch

    from ml_training.train_pd_vowel_audio import (
        PRE_CROP_S, PRE_EQ_CAP_DB, PRE_RMS_DBFS, PRE_SR, apply_fir_gain, eq_gain_curve, ltas_shape_db, preprocess_audio,
    )

    y = synth_vowel(sr=44100, f0=120, dur=5.0, pad=0.5, seed=3)  # 44.1 kHz like the 2016 PD session
    for mode in ("basic", "hpf"):
        out, sr, info = preprocess_audio(y, 44100, mode)
        assert sr == PRE_SR and len(out) == int(PRE_CROP_S * PRE_SR) and info["pre_crop_s"] == PRE_CROP_S
        assert abs(_db(out) - PRE_RMS_DBFS) < 0.05 and info["pre_quality_ok"] and info["pre_mode"] == mode
        assert np.max(np.abs(out)) <= 0.99 + 1e-9
    short, sr, info = preprocess_audio(synth_vowel(sr=SR, f0=120, dur=1.5, pad=0.3, seed=4), SR, "basic")
    assert 1.3 < len(short) / sr <= 1.6 and info["pre_crop_s"] < PRE_CROP_S  # whole voiced part when shorter than 3 s
    assert preprocess_audio(y, 44100, "none")[1] == 44100
    with pytest.raises(ValueError):
        preprocess_audio(y, 44100, "bogus")

    # the gain curve: capped, and 0 dB outside the EQ band
    f, shape = ltas_shape_db(preprocess_audio(y, 44100, "basic")[0], PRE_SR)
    g = eq_gain_curve(shape, shape + 30.0, f)
    assert g.max() == PRE_EQ_CAP_DB and np.all(g[f <= 60] == 0) and np.all(g[f >= 7000] == 0)
    assert np.allclose(eq_gain_curve(shape, shape, f), 0)

    # channel removal: the same vowel through a quiet, -3 dB/oct tilted channel (about the size of the Italian
    # controls' channel difference) ends up with the same level and tilt
    clean, _, _ = preprocess_audio(y, 44100, "basic")
    fr = np.linspace(0, 22050, 513)
    tilted = 0.1 * apply_fir_gain(y, fr, np.clip(-3 * np.log2(np.maximum(fr, 50) / 1000), -9, 9), 44100, numtaps=511)
    ref = ltas_shape_db(clean, PRE_SR)[1]
    a, _, ia = preprocess_audio(y, 44100, "ltas", ref)
    b, _, ib = preprocess_audio(tilted, 44100, "ltas", ref)
    assert abs(_db(a) - _db(b)) < 0.05 and ia["pre_eq_rms_gain_db"] < ib["pre_eq_rms_gain_db"]

    def tilt(x):
        fx, p = welch(x, PRE_SR, nperseg=1024)
        m = (fx >= 100) & (fx <= 4000)
        return np.polyfit(np.log2(fx[m]), 10 * np.log10(p[m]), 1)[0]

    before = abs(tilt(clean) - tilt(preprocess_audio(tilted, 44100, "basic")[0]))
    after = abs(tilt(a) - tilt(b))
    assert before > 1.5 and after < 0.75 * before, (before, after)
    # a linear-phase FIR leaves the periods alone: jitter survives the EQ
    from app.ml.features.vowel import extract_vowel_features

    ja, jb = extract_vowel_features(a, PRE_SR)["MDVP:Jitter(%)"], extract_vowel_features(b, PRE_SR)["MDVP:Jitter(%)"]
    assert np.isfinite(ja) and abs(ja - jb) < 0.5 * ja


def test_reference_ltas_and_augmentation(manifest, tmp_path):
    from ml_training.train_pd_vowel_audio import (
        AUG_KEYS, PRE_LTAS_NFFT, augment_audio, augmented_manifest, cache_path_for, load_manifest, reference_ltas,
    )

    m = load_manifest(manifest)
    ref = reference_ltas(m, cache=tmp_path / "ref.json", n_jobs=1)
    assert ref["n_recordings"] == 12 and len(ref["ltas_db"]) == PRE_LTAS_NFFT // 2 + 1
    assert reference_ltas(m, cache=tmp_path / "ref.json")["files_key"] == ref["files_key"]  # served from cache
    aug = augmented_manifest(m, 2, seed=7)
    assert len(aug) == 24 and set(aug["variant"]) == {0, 1} and set(AUG_KEYS) <= set(aug.columns)
    assert aug.equals(augmented_manifest(m, 2, seed=7)) and not aug.equals(augmented_manifest(m, 2, seed=8))
    assert aug["aug_gain_db"].abs().max() <= 6 and aug["aug_snr_db"].between(20, 35).all()
    y = synth_vowel(sr=SR, f0=120, dur=3.0, pad=0.3, seed=1)
    p = aug.iloc[0][list(AUG_KEYS)].to_dict()
    ya = augment_audio(y, SR, p)
    assert len(ya) == len(y) and np.all(np.isfinite(ya)) and not np.allclose(ya, y)
    assert np.allclose(ya, augment_audio(y, SR, p))  # deterministic
    base = Path("/x/feats.csv")
    assert cache_path_for(base) == base and cache_path_for(base, "ltas") == Path("/x/feats__ltas.csv")
    assert cache_path_for(base, "none", 4, 42) == Path("/x/feats__none__aug4_s42.csv") and cache_path_for(None, "ltas") is None


def test_run_training_preprocess_and_augment(manifest, tmp_path):
    m = run_training(manifest, out_dir=tmp_path, n_splits=3, n_repeats=0, n_boot=50, fast=True,
                     cache=tmp_path / "cache.csv", n_jobs=1, preprocess="ltas", augment=1)
    assert m["preprocessing"]["mode"] == "ltas" and m["preprocessing"]["n_gate_failed_after_preprocessing"] == 0
    assert m["augmentation"]["n_variants"] == 1 and m["augmentation"]["n_augmented_rows"] == 12
    assert (tmp_path / "cache__ltas.csv").exists() and (tmp_path / "cache__ltas__aug1_s42.csv").exists() and not (tmp_path / "cache.csv").exists()
    folds = m["candidates"]["logreg"]["folds"]
    assert all(f["n_train_with_aug"] == 2 * (12 - f["n_test"]) for f in folds)  # every training recording + its variant
    assert m["final_fit"]["n_train_with_aug"] == 24 and m["final_fit"]["n_train"] == 12
    assert "ltas" in m["feature_extractor"]["train_serve_skew"]
    pre = json.loads((tmp_path / "preprocess.json").read_text())
    assert pre["mode"] == "ltas" and len(pre["reference_ltas"]["ltas_db"]) == 513 and pre["params"]["rms_dbfs"] == -23.0
    card = (tmp_path / "README.md").read_text()
    assert "{{" not in card and "Mode `ltas`" in card and "re-channelled variants" in card
    assert PdVowelModel.load(tmp_path).feature_order == UCI_FEATURES
