"""Serving wrapper for the experimental Parkinson's sustained-vowel classifier.

Spec: docs/specs/phase-a-assessments.md §5.4 (serving: quality gate first, median
over 3 repetitions, calibrated threshold) and §10 item 2 (``model_scores`` =
name, version, probability, threshold, calibration_note) / item 3 (quality).

Artefacts are produced by ``ml_training/train_pd_vowel.py`` in
``models/pd_vowel_experimental_v1/``: ``model.joblib`` (sigmoid-calibrated
sklearn estimator), ``metrics.json`` (``threshold`` = 80%-specificity cut-off on
grouped out-of-fold probabilities) and ``feature_order.json``.

The probability is a task-specific experimental score; it is never a diagnosis
and never by itself raises a pathway level (rule PD-03 decides).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from app.ml.features.quality import RecordingQuality, assess_quality
from app.ml.features.vowel import UCI_FEATURES, extract_vowel_features

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "pd_vowel_experimental_v1"
MODEL_NAME = "pd_vowel_classifier"
CALIBRATION_NOTE = (
    "Sigmoid-calibrated on UCI Parkinson's (31 subjects, subject-grouped CV); threshold set for 80% "
    "specificity out-of-fold. Experimental; serving features (Praat) differ from training features (MDVP). "
    "Not diagnostic."
)


@dataclass
class VowelPrediction:
    """One recording's model output (spec §10 ``model_scores`` entry)."""

    probability: float
    model_version: str
    threshold: float
    feature_vector_ok: bool
    missing_features: list[str] = field(default_factory=list)
    above_threshold: bool = False
    name: str = MODEL_NAME
    calibration_note: str = CALIBRATION_NOTE

    def to_model_score(self) -> dict[str, Any]:
        return asdict(self)


class PdVowelModel:
    """Loaded classifier + threshold + feature order."""

    def __init__(
        self,
        estimator: Any,
        feature_order: list[str],
        threshold: float,
        model_version: str,
        train_medians: Mapping[str, float] | None = None,
        metrics: Mapping[str, Any] | None = None,
    ) -> None:
        self.estimator = estimator
        self.feature_order = list(feature_order)
        self.threshold = float(threshold)
        self.model_version = model_version
        self.train_medians = dict(train_medians or {})
        self.metrics = dict(metrics or {})

    @classmethod
    def load(cls, path: str | Path = DEFAULT_MODEL_DIR) -> "PdVowelModel":
        """Load artefacts from a local directory (e.g. a ``snapshot_download`` of the Hub repo)."""
        import joblib

        d = Path(path)
        estimator = joblib.load(d / "model.joblib")
        metrics = json.loads((d / "metrics.json").read_text())
        fo = json.loads((d / "feature_order.json").read_text())
        return cls(
            estimator=estimator,
            feature_order=fo["features"],
            threshold=metrics["threshold"],
            model_version=metrics.get("model_version", d.name),
            train_medians=fo.get("train_medians"),
            metrics=metrics,
        )

    @classmethod
    def load_from_hub(cls, repo: str, revision: str = "main", token: str | None = None) -> "PdVowelModel":
        """Download the artefact folder from the HF Hub (cached by ``huggingface_hub``) and load it.

        ``revision`` should be a commit sha in production so a later push cannot change the served model.
        """
        from huggingface_hub import snapshot_download

        folder = snapshot_download(repo_id=repo, repo_type="model", revision=revision, token=token or None,
                                   allow_patterns=["model.joblib", "metrics.json", "feature_order.json"])
        model = cls.load(folder)
        model.metrics.setdefault("hub", {"repo": repo, "revision": revision})
        return model

    @classmethod
    def load_default(cls) -> "PdVowelModel":
        """Local ``models/`` folder first; else the Hub repo from settings (``PD_VOWEL_HUB_REPO``)."""
        if (DEFAULT_MODEL_DIR / "model.joblib").exists():
            return cls.load(DEFAULT_MODEL_DIR)
        from app.core.config import settings

        if not settings.pd_vowel_hub_repo:
            raise FileNotFoundError(
                f"{DEFAULT_MODEL_DIR} has no model.joblib and PD_VOWEL_HUB_REPO is not set; "
                "run `python -m ml_training.train_pd_vowel` or set PD_VOWEL_HUB_REPO/HF_TOKEN"
            )
        logger.info("pd vowel model: loading from Hub", extra={"repo": settings.pd_vowel_hub_repo,
                                                               "revision": settings.pd_vowel_hub_revision})
        return cls.load_from_hub(settings.pd_vowel_hub_repo, settings.pd_vowel_hub_revision, settings.hf_token)

    def predict_from_features(self, feats: Mapping[str, float]) -> VowelPrediction:
        """Predict from a UCI-keyed feature dict.

        Missing or non-finite features are imputed with the training median and
        listed in ``missing_features``; ``feature_vector_ok`` is then False and
        the caller should treat the score as low-confidence.
        """
        import pandas as pd

        missing: list[str] = []
        row: dict[str, float] = {}
        for k in self.feature_order:
            v = feats.get(k)
            try:
                fv = float(v) if v is not None else math.nan
            except (TypeError, ValueError):
                fv = math.nan
            if not math.isfinite(fv):
                missing.append(k)
                fv = float(self.train_medians.get(k, 0.0))
            row[k] = fv
        X = pd.DataFrame([row], columns=self.feature_order)
        p = float(self.estimator.predict_proba(X)[0, 1])
        return VowelPrediction(
            probability=p,
            model_version=self.model_version,
            threshold=self.threshold,
            feature_vector_ok=not missing,
            missing_features=missing,
            above_threshold=p >= self.threshold,
        )

    def predict_from_audio(self, y: np.ndarray, sr: int) -> tuple[VowelPrediction | None, RecordingQuality]:
        """Quality gate, then features, then prediction.

        Returns ``(None, quality)`` when ``quality.ok`` is False — the caller
        records ``invalid_measurement`` (spec §5.4, §8).
        """
        quality = assess_quality(y, sr)
        if not quality.ok:
            return None, quality
        feats = extract_vowel_features(y, sr)
        return self.predict_from_features(feats), quality

    @staticmethod
    def aggregate_sessions(probs: Iterable[float | None]) -> float | None:
        """Median probability over a session's repetitions (spec: 3), ignoring None/NaN."""
        vals = [float(p) for p in probs if p is not None and math.isfinite(float(p))]
        return float(np.median(vals)) if vals else None


def aggregate_sessions(probs: Iterable[float | None]) -> float | None:
    """Module-level alias of :meth:`PdVowelModel.aggregate_sessions`."""
    return PdVowelModel.aggregate_sessions(probs)


__all__ = ["PdVowelModel", "VowelPrediction", "aggregate_sessions", "DEFAULT_MODEL_DIR", "UCI_FEATURES"]
