"""Smoke test of the grouped nested-CV training core on the real UCI CSV."""

import json
import time

import pytest

from ml_training.train_pd_vowel import (
    DEFAULT_DATA,
    load_uci,
    push_to_hub,
    run_training,
    threshold_at_specificity,
)

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="UCI Parkinson's CSV not downloaded")


def test_load_uci_subjects():
    X, y, g, _ = load_uci()
    assert X.shape == (195, 22)
    # parkinsons.names says 31 subjects (23 PD); the IDs in the file give 32 (24 PD, 8 healthy)
    assert len(set(g)) == 32 and len(set(g[y == 1])) == 24 and len(set(g[y == 0])) == 8


def test_run_training_fast(tmp_path):
    t0 = time.time()
    m = run_training(out_dir=tmp_path, n_splits=2, n_repeats=0, n_boot=200, fast=True)
    assert time.time() - t0 < 30
    best = m["candidates"][m["chosen_model"]]
    assert best["auc_pooled_oof"] > 0.6
    for key in ("auc_ci95_subject_bootstrap", "sensitivity_at_80_specificity", "brier", "confusion_at_0.5", "subject_level"):
        assert key in best
    assert set(m["candidates"]) == {"logreg", "xgboost"}
    for f in ("model.joblib", "metrics.json", "feature_order.json", "README.md"):
        assert (tmp_path / f).exists()
    saved = json.loads((tmp_path / "metrics.json").read_text())
    assert 0 < saved["threshold"] < 1 and saved["dataset"]["n_subjects"] == 32
    card = (tmp_path / "README.md").read_text()
    assert "{{" not in card and "31 subjects" in card and "32 subject IDs" in card and "no external validation" in card


def test_threshold_at_specificity():
    import numpy as np

    y = np.array([0] * 10 + [1] * 10)
    p = np.concatenate([np.linspace(0, 0.9, 10), np.linspace(0.5, 1, 10)])
    t, sens, spec = threshold_at_specificity(y, p, 0.8)
    assert spec >= 0.8 and np.mean(p[y == 0] < t) >= 0.8


def test_push_to_hub_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    (tmp_path / "x.json").write_text("{}")
    assert push_to_hub("org/repo", tmp_path).startswith("[dry-run]")
