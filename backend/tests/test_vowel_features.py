"""Quality gate + UCI vowel features on synthetic sustained vowels (spec §5.4)."""

import math

import numpy as np
import pytest

from app.ml.features.quality import assess_quality, load_audio
from app.ml.features.vowel import FEATURE_NOTES, UCI_FEATURES, extract_vowel_features

SR = 16000


def synth_vowel(
    sr: int = SR, f0: float = 120.0, dur: float = 5.0, jitter: float = 0.005, shimmer: float = 0.03,
    pad: float = 0.5, noise: float = 1e-4, seed: int = 0,
) -> np.ndarray:
    """Harmonic series at f0 built period by period with Gaussian jitter/shimmer, padded with near-silence."""
    rng = np.random.default_rng(seed)
    periods, t = [], 0.0
    while t < dur:
        n = int(round(sr * (1.0 / f0) * (1.0 + jitter * rng.standard_normal())))
        ph = np.arange(n) / n
        amp = 1.0 + shimmer * rng.standard_normal()
        periods.append(amp * sum((0.6**k) * np.sin(2 * np.pi * k * ph) for k in range(1, 8)))
        t += n / sr
    v = np.concatenate(periods)
    v = 0.5 * v / np.max(np.abs(v))
    z = np.zeros(int(pad * sr))
    y = np.concatenate([z, v, z])
    return y + noise * rng.standard_normal(len(y))


@pytest.fixture(scope="module")
def clean():
    return synth_vowel()


def test_quality_clean_passes(clean):
    q = assess_quality(clean, SR)
    assert q.ok, q.reasons
    assert q.voiced_s >= 4.5 and q.snr_db > 30 and q.clip_frac == 0


def test_quality_noisy_fails(clean):
    noisy = clean + 0.2 * np.random.default_rng(1).standard_normal(len(clean))
    q = assess_quality(noisy, SR)
    assert not q.ok and any("snr" in r for r in q.reasons)


def test_quality_clipped_fails(clean):
    q = assess_quality(np.clip(clean * 4, -1, 1), SR)
    assert not q.ok and "clipping" in q.reasons


def test_quality_short_fails():
    q = assess_quality(synth_vowel(dur=2.0), SR)
    assert not q.ok and any("voiced" in r for r in q.reasons)


def test_load_audio_roundtrip(tmp_path, clean):
    import soundfile as sf

    p = tmp_path / "v.wav"
    sf.write(p, clean, SR)
    y, sr = load_audio(p)
    assert sr == SR and len(y) == len(clean)
    y2, sr2 = load_audio(p.read_bytes(), target_sr=8000)
    assert sr2 == 8000 and abs(len(y2) - len(clean) / 2) < 10


def test_vowel_features_keys_and_ranges(clean):
    f = extract_vowel_features(clean, SR)
    assert list(f) == UCI_FEATURES
    assert all(math.isfinite(v) for v in f.values()), {k: v for k, v in f.items() if not math.isfinite(v)}
    assert abs(f["MDVP:Fo(Hz)"] - 120) < 5
    assert f["MDVP:Flo(Hz)"] <= f["MDVP:Fo(Hz)"] <= f["MDVP:Fhi(Hz)"]
    assert 0 < f["MDVP:Jitter(%)"] < 0.05
    assert 0 < f["MDVP:Shimmer"] < 0.2
    assert 10 < f["HNR"] < 50 and 0 < f["NHR"] < 0.5
    assert 0 < f["RPDE"] < 1 and 0.5 < f["DFA"] < 1 and 0 <= f["PPE"] <= 1
    assert -12 < f["spread1"] < 0 and 0 <= f["spread2"] < 1 and 0 < f["D2"] < 10
    assert abs(f["Jitter:DDP"] - 3 * f["MDVP:RAP"]) < 1e-6


def test_more_jitter_increases_jitter_feature():
    lo = extract_vowel_features(synth_vowel(jitter=0.002, seed=3), SR)
    hi = extract_vowel_features(synth_vowel(jitter=0.02, seed=3), SR)
    assert hi["MDVP:Jitter(%)"] > lo["MDVP:Jitter(%)"]


def test_feature_notes_cover_all_features():
    assert set(FEATURE_NOTES) == set(UCI_FEATURES)
    assert all("approximate" in v for v in FEATURE_NOTES.values())
    for k in ("RPDE", "DFA", "PPE", "spread1", "spread2", "D2", "NHR"):
        assert FEATURE_NOTES[k]["approximate"] is True
