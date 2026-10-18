"""Quality gate + UCI vowel features on synthetic sustained vowels (spec §5.4)."""

import math

import numpy as np
import pytest

from app.ml.features.quality import assess_quality, load_audio, recording_condition_signals
from app.ml.features.vowel import (
    EXTRA_FEATURE_NOTES, EXTRA_FEATURES, FEATURE_NOTES, UCI_FEATURES, extract_vowel_features,
)

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


# --- recording-condition signals (informational; never change the gate)


def test_quality_condition_signals_clean(clean):
    q = assess_quality(clean, SR)
    d = q.to_dict()
    assert {"noise_floor_dbfs", "level_dbfs", "clipping_ratio"} <= set(d)
    # 0.5 FS amplitude harmonic series -> RMS roughly -10 to -14 dBFS; padding noise 1e-4 -> ~ -80 dBFS
    assert -20 < q.level_dbfs < -5, q.level_dbfs
    assert q.noise_floor_dbfs < -60, q.noise_floor_dbfs
    assert q.noise_floor_dbfs < q.level_dbfs
    assert q.clipping_ratio == q.clip_frac == 0.0
    assert q.ok


def test_quality_condition_signals_track_noise_level_and_clipping(clean):
    rng = np.random.default_rng(7)
    noisy = clean + 0.01 * rng.standard_normal(len(clean))  # ~ -40 dBFS floor, still passes SNR gate
    qc, qn = assess_quality(clean, SR), assess_quality(noisy, SR)
    assert qn.noise_floor_dbfs > qc.noise_floor_dbfs + 20
    assert abs(qn.level_dbfs - qc.level_dbfs) < 1.0  # voiced level barely changes
    assert qn.ok and qn.reasons == []  # signals do not alter the gate

    quiet = assess_quality(clean * 0.1, SR)  # -20 dB
    assert abs(quiet.level_dbfs - (qc.level_dbfs - 20.0)) < 0.5

    clipped = assess_quality(np.clip(clean * 4, -1, 1), SR)
    assert clipped.clipping_ratio == clipped.clip_frac > 0.01
    assert "clipping" in clipped.reasons


def test_quality_condition_signals_silent_and_direct():
    q = assess_quality(np.zeros(SR), SR)
    assert not q.ok and q.noise_floor_dbfs is None and q.level_dbfs is None and q.clipping_ratio == 0.0
    sig = recording_condition_signals(np.full(SR, 0.5), SR)  # DC at 0.5 FS: both levels = 20 log10(0.5)
    assert abs(sig["level_dbfs"] - (-6.02)) < 0.05 and abs(sig["noise_floor_dbfs"] - (-6.02)) < 0.05
    assert recording_condition_signals(np.array([]), SR)["level_dbfs"] is None


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
    assert list(f)[:22] == UCI_FEATURES  # v1 model input unchanged
    assert list(f) == [*UCI_FEATURES, *EXTRA_FEATURES] and EXTRA_FEATURES == ("CPPS",)
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
    assert set(EXTRA_FEATURE_NOTES) == set(EXTRA_FEATURES)
    assert {"approximate", "method", "difference"} <= set(EXTRA_FEATURE_NOTES["CPPS"])
    assert not set(EXTRA_FEATURES) & set(UCI_FEATURES)


# --- CPPS (extra, non-UCI feature for the personal voice-stability rule)


def test_cpps_finite_and_plausible_on_synthetic_vowel(clean):
    f = extract_vowel_features(clean, SR)
    assert math.isfinite(f["CPPS"])
    # Praat CPPS for a clean periodic signal lands in the teens-to-twenties of dB
    assert 5.0 < f["CPPS"] < 40.0, f["CPPS"]


def test_cpps_drops_with_added_noise_at_10db_snr(clean):
    rng = np.random.default_rng(11)
    voiced = clean[np.abs(clean) > 1e-3]
    sig_p = float(np.mean(voiced**2))
    noise = rng.standard_normal(len(clean)) * np.sqrt(sig_p / 10.0)  # 10 dB SNR re the voiced part
    noisy = np.clip(clean + noise, -1, 1)
    c_clean = extract_vowel_features(clean, SR)["CPPS"]
    c_noisy = extract_vowel_features(noisy, SR)["CPPS"]
    assert math.isfinite(c_noisy)
    assert c_clean > c_noisy + 2.0, (c_clean, c_noisy)


def test_cpps_nan_on_degenerate_input():
    from app.ml.features.vowel import cpps
    import parselmouth

    # a 10 ms blip is far too short for a cepstrogram: must return nan, not raise
    assert math.isnan(cpps(parselmouth.Sound(np.zeros(160), sampling_frequency=float(SR))))
