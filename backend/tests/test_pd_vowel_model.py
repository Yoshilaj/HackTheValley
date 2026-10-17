"""Serving wrapper for the experimental PD vowel model (spec §5.4, §10)."""

import json
import math

import numpy as np
import pytest

from app.ml.features.vowel import UCI_FEATURES
from app.ml.pd_vowel_model import DEFAULT_MODEL_DIR, PdVowelModel, aggregate_sessions
from tests.test_vowel_features import SR, synth_vowel

pytestmark = pytest.mark.skipif(
    not (DEFAULT_MODEL_DIR / "model.joblib").exists(), reason="run ml_training/train_pd_vowel.py first"
)


@pytest.fixture(scope="module")
def model():
    return PdVowelModel.load()


def test_load_threshold_from_metrics(model):
    m = json.loads((DEFAULT_MODEL_DIR / "metrics.json").read_text())
    assert model.threshold == pytest.approx(m["threshold"]) and 0 < model.threshold < 1
    assert model.feature_order == UCI_FEATURES


def test_predict_from_uci_row(model):
    import pandas as pd

    df = pd.read_csv(DEFAULT_MODEL_DIR.parents[1] / "data/raw/uci_parkinsons/parkinsons.data")
    pred = model.predict_from_features(df.iloc[0][UCI_FEATURES].to_dict())
    assert 0 <= pred.probability <= 1 and pred.feature_vector_ok and pred.missing_features == []
    assert pred.model_version == "pd_vowel_experimental_v1"
    assert pred.above_threshold == (pred.probability >= pred.threshold)


def test_missing_features_flagged(model):
    pred = model.predict_from_features({"HNR": 20.0, "PPE": float("nan")})
    assert not pred.feature_vector_ok and "PPE" in pred.missing_features and "HNR" not in pred.missing_features
    assert 0 <= pred.probability <= 1


def test_predict_from_audio_clean(model):
    pred, q = model.predict_from_audio(synth_vowel(), SR)
    assert q.ok and pred is not None and 0 <= pred.probability <= 1
    assert pred.to_model_score()["name"] == "pd_vowel_classifier"


def test_predict_from_audio_bad_quality_returns_none(model):
    y = np.clip(synth_vowel() * 4, -1, 1)
    pred, q = model.predict_from_audio(y, SR)
    assert pred is None and not q.ok


def test_aggregate_median():
    assert aggregate_sessions([0.2, 0.9, 0.4]) == 0.4
    assert aggregate_sessions([None, 0.3, float("nan")]) == 0.3
    assert aggregate_sessions([]) is None
    assert PdVowelModel.aggregate_sessions([0.1, 0.3]) == pytest.approx(0.2)
    assert not math.isnan(aggregate_sessions([0.5]))
