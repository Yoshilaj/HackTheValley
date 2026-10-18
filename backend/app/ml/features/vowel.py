"""Sustained-vowel (/a/) dysphonia features compatible with the UCI Parkinson's dataset.

Spec: docs/specs/phase-a-assessments.md §5.4; docs/BACKEND_PLAN.md A1/A4.

Output keys are **exactly** the UCI column names (``UCI_FEATURES``) so that the
training CSV and serving share one feature vector.

References
----------
* Little MA, McSharry PE, Hunter EJ, Spielman J, Ramig LO (2009). "Suitability of
  dysphonia measurements for telemonitoring of Parkinson's disease." IEEE TBME
  56(4):1015-1022 (dataset paper; PPE, spread1/2, D2).
* Little MA, McSharry PE, Roberts SJ, Costello DAE, Moroz IM (2007). "Exploiting
  nonlinear recurrence and fractal scaling properties for voice disorder
  detection." BioMed Eng OnLine 6:23 (RPDE, DFA).
* Boersma P, Weenink D. Praat manual: "Voice 2. Jitter", "Voice 3. Shimmer"
  (Praat's stated correspondences with Kay MDVP measures).
* Grassberger P, Procaccia I (1983). "Characterization of strange attractors."
  PRL 50:346 (correlation dimension, D2).

The original UCI features were extracted with Kay Elemetrics MDVP plus Little's
MATLAB code on 25 kHz studio recordings; ours use Praat (parselmouth) and
re-implementations. ``FEATURE_NOTES`` records every known difference; this is
a stated limitation of the experimental model.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

UCI_FEATURES: list[str] = [
    "MDVP:Fo(Hz)",
    "MDVP:Fhi(Hz)",
    "MDVP:Flo(Hz)",
    "MDVP:Jitter(%)",
    "MDVP:Jitter(Abs)",
    "MDVP:RAP",
    "MDVP:PPQ",
    "Jitter:DDP",
    "MDVP:Shimmer",
    "MDVP:Shimmer(dB)",
    "Shimmer:APQ3",
    "Shimmer:APQ5",
    "MDVP:APQ",
    "Shimmer:DDA",
    "NHR",
    "HNR",
    "RPDE",
    "DFA",
    "spread1",
    "spread2",
    "D2",
    "PPE",
]

# Extra (non-UCI) features appended to the returned dict AFTER the 22 UCI keys.
# They are not inputs to PdVowelModel (v1); they exist for the personal
# voice-stability rule (PD-03b), which compares a user against their own baseline.
EXTRA_FEATURES: tuple[str, ...] = ("CPPS",)

# Analysis constants
PITCH_FLOOR_HZ = 75.0
PITCH_CEILING_HZ = 600.0
NONLINEAR_SR = 25_000  # UCI recordings were 25 kHz; nonlinear measures are rate-dependent
PPE_REF_HZ = 127.09  # semitone reference used in Little et al. 2009 PPE
_MAX_PERIOD_FACTOR = 1.3
_PERIOD_FLOOR = 0.0001
_PERIOD_CEILING = 0.02
_MAX_AMP_FACTOR = 1.6

FEATURE_NOTES: dict[str, dict[str, Any]] = {
    "MDVP:Fo(Hz)": {
        "approximate": False,
        "method": "Praat To Pitch (ac), 75-600 Hz; mean of voiced frames",
        "difference": "MDVP pitch extraction differs from Praat's autocorrelation tracker; means agree closely on clean vowels.",
    },
    "MDVP:Fhi(Hz)": {
        "approximate": True,
        "method": "max of Praat voiced pitch frames",
        "difference": "Extremes are sensitive to octave errors; MDVP's tracker and windowing differ, so Fhi/Flo are only approximately comparable.",
    },
    "MDVP:Flo(Hz)": {
        "approximate": True,
        "method": "min of Praat voiced pitch frames",
        "difference": "See MDVP:Fhi(Hz).",
    },
    "MDVP:Jitter(%)": {
        "approximate": False,
        "method": "Praat Get jitter (local) on PointProcess (periodic, cc), as a fraction (UCI stores the fraction, e.g. 0.0078)",
        "difference": "Praat manual states jitter (local) corresponds to MDVP Jitt; period detection algorithm differs.",
    },
    "MDVP:Jitter(Abs)": {
        "approximate": False,
        "method": "Praat Get jitter (local, absolute), seconds",
        "difference": "Corresponds to MDVP Jita.",
    },
    "MDVP:RAP": {"approximate": False, "method": "Praat Get jitter (rap)", "difference": "Corresponds to MDVP RAP."},
    "MDVP:PPQ": {"approximate": False, "method": "Praat Get jitter (ppq5)", "difference": "Corresponds to MDVP PPQ (5-point)."},
    "Jitter:DDP": {"approximate": False, "method": "Praat Get jitter (ddp) (= 3 x RAP)", "difference": "Definitional equivalent."},
    "MDVP:Shimmer": {"approximate": False, "method": "Praat Get shimmer (local)", "difference": "Corresponds to MDVP Shim."},
    "MDVP:Shimmer(dB)": {"approximate": False, "method": "Praat Get shimmer (local_dB)", "difference": "Corresponds to MDVP ShdB."},
    "Shimmer:APQ3": {"approximate": False, "method": "Praat Get shimmer (apq3)", "difference": "Definitional equivalent."},
    "Shimmer:APQ5": {"approximate": False, "method": "Praat Get shimmer (apq5)", "difference": "Definitional equivalent."},
    "MDVP:APQ": {"approximate": False, "method": "Praat Get shimmer (apq11)", "difference": "Praat manual: apq11 corresponds to MDVP APQ."},
    "Shimmer:DDA": {"approximate": False, "method": "Praat Get shimmer (dda) (= 3 x APQ3)", "difference": "Definitional equivalent."},
    "NHR": {
        "approximate": True,
        "method": "10 ** (-HNR / 10) from Praat harmonicity (cc)",
        "difference": "MDVP NHR is an energy ratio of 1.5-4.5 kHz inharmonic to 70-4.5 kHz harmonic energy; the HNR conversion is a different (broadband) quantity with a different scale.",
    },
    "HNR": {
        "approximate": False,
        "method": "Praat To Harmonicity (cc), 0.01 s, 75 Hz, mean in dB",
        "difference": "UCI HNR came from Little's code / MDVP-family processing; Praat HNR is the standard equivalent.",
    },
    "RPDE": {
        "approximate": True,
        "method": "Recurrence period density entropy (Little 2007): signal resampled to 25 kHz, normalised to [-1,1], time-delay embedded (m=4, tau=35), close returns within eps=0.12 up to T_max=1000 samples, normalised Shannon entropy H/ln(T_max) on a central 0.3 s segment",
        "difference": "Embedding parameters are our reconstruction from the paper; Little's code also pre-filtered the signal and used the whole recording.",
    },
    "DFA": {
        "approximate": True,
        "method": "Detrended fluctuation analysis (Little 2007) of the 25 kHz signal, window sizes L=50..100 step 10, scaling exponent alpha, reported as logistic 1/(1+exp(-alpha))",
        "difference": "Window range and logistic transform as described in Little 2007; exact segment/overlap choices of the original code unknown.",
    },
    "spread1": {
        "approximate": True,
        "method": "ln(std) of the whitened semitone pitch residual (AR(2) linear prediction), residual in octaves",
        "difference": "No public algorithmic definition of spread1/spread2 exists; this is our closest documented approximation, chosen to measure nonlinear F0 variation on a comparable scale (UCI range about -7.9 to -2.4).",
    },
    "spread2": {
        "approximate": True,
        "method": "std of the mean-removed semitone pitch contour, in octaves",
        "difference": "No public definition; approximation on a comparable scale (UCI range about 0.006 to 0.45).",
    },
    "D2": {
        "approximate": True,
        "method": "Grassberger-Procaccia correlation dimension: 25 kHz signal decimated x2, embedding m=10, tau=8, 1500 sampled points, Theiler window 50, slope of log C(r) vs log r between the 2nd and 30th percentiles of pairwise distances",
        "difference": "Embedding and scaling-region choices are ours; correlation-dimension estimates are highly parameter dependent.",
    },
    "PPE": {
        "approximate": True,
        "method": "Pitch period entropy (Little 2009): pitch to semitones re 127.09 Hz, AR(2) whitening, entropy of residual histogram (0.1-semitone bins over +/-3 semitones) normalised by ln(n_bins)",
        "difference": "Histogram binning and normalisation are our choices; Little used a different probability-density estimate.",
    },
}

# Notes for EXTRA_FEATURES, same shape as FEATURE_NOTES (kept separate so the
# UCI model cards, which iterate FEATURE_NOTES, are unchanged).
EXTRA_FEATURE_NOTES: dict[str, dict[str, Any]] = {
    "CPPS": {
        "approximate": False,
        "method": (
            "Smoothed cepstral peak prominence, dB (Hillenbrand 1994 / Praat): "
            "To PowerCepstrogram (60 Hz, 2 ms step, 5 kHz, pre-emphasis 50 Hz) then "
            "Get CPPS (time 0.02 s / quefrency 0.0005 s smoothing, peak search 60-330 Hz, "
            "parabolic interpolation, straight robust trend line over 0.001-0.05 s)"
        ),
        "difference": (
            "Not in the UCI set, so not a model input. Lower in PD (Wright & Aharonson 2025 review) "
            "and, unlike HNR, robust to room acoustics (Bottalico 2018); used only for within-person "
            "comparison against the user's own baseline. Praat's CPPS value depends on these "
            "parameters, so it is comparable across our recordings but not with other software."
        ),
    },
}

_CPPS_PITCH_FLOOR_HZ = 60.0
_CPPS_PEAK_SEARCH_HZ = (60.0, 330.0)

_OVERALL_NOTE = (
    "UCI features were computed by MDVP (Kay Elemetrics) and Little's MATLAB code on studio "
    "recordings (25 kHz, head-mounted microphone). Serving features come from Praat (parselmouth) "
    "and our re-implementations on phone recordings; the classifier therefore faces a "
    "measurement-domain shift that has not been validated."
)


# --------------------------------------------------------------------------- helpers

def _main_voiced_region(y: np.ndarray, sr: int) -> np.ndarray:
    """Trim leading/trailing silence (first to last non-silent interval)."""
    import librosa

    iv = librosa.effects.split(y, top_db=30, frame_length=1024, hop_length=256)
    if len(iv) == 0:
        return y
    return y[int(iv[0][0]) : int(iv[-1][1])]


def _resample(y: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target:
        return y
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(int(sr), int(target))
    return resample_poly(y, target // g, sr // g)


def _central(y: np.ndarray, n: int) -> np.ndarray:
    if len(y) <= n:
        return y
    start = (len(y) - n) // 2
    return y[start : start + n]


def _embed(x: np.ndarray, m: int, tau: int) -> np.ndarray:
    n = len(x) - (m - 1) * tau
    if n <= 0:
        return np.empty((0, m))
    return np.stack([x[i * tau : i * tau + n] for i in range(m)], axis=1)


def rpde(x: np.ndarray, m: int = 4, tau: int = 35, eps: float = 0.12, t_max: int = 1000, step: int = 2) -> float:
    """Recurrence period density entropy (Little et al. 2007, BioMed Eng OnLine 6:23).

    For each embedded point, find the first time the trajectory leaves the
    eps-ball and then the first return into it; the return time T is a
    recurrence period. RPDE = -sum P(T) ln P(T) / ln(T_max).
    """
    x = np.asarray(x, dtype=np.float64)
    rng = np.max(np.abs(x))
    if rng == 0:
        return float("nan")
    x = x / rng
    emb = _embed(x, m, tau)
    n = len(emb) - t_max - 1
    if n <= 10:
        return float("nan")
    counts = np.zeros(t_max + 1, dtype=np.int64)
    idx_all = np.arange(0, n, step)
    offsets = np.arange(1, t_max + 1)
    for chunk in np.array_split(idx_all, max(1, len(idx_all) // 256)):
        ref = emb[chunk][:, None, :]
        fut = emb[chunk[:, None] + offsets[None, :]]
        d = np.sqrt(np.sum((fut - ref) ** 2, axis=2))
        inside = d < eps
        outside = ~inside
        has_left = outside.any(axis=1)
        first_out = np.argmax(outside, axis=1)
        after = inside & (np.arange(t_max)[None, :] > first_out[:, None])
        has_ret = after.any(axis=1) & has_left
        first_ret = np.argmax(after, axis=1)
        t = offsets[first_ret[has_ret]]
        counts += np.bincount(t, minlength=t_max + 1)
    total = counts.sum()
    if total == 0:
        return float("nan")
    p = counts[counts > 0] / total
    return float(-np.sum(p * np.log(p)) / np.log(t_max))


def dfa(x: np.ndarray, scales: tuple[int, ...] = (50, 60, 70, 80, 90, 100)) -> float:
    """Detrended fluctuation analysis, logistic-transformed exponent (Little et al. 2007)."""
    x = np.asarray(x, dtype=np.float64)
    yint = np.cumsum(x - x.mean())
    flucts = []
    for L in scales:
        nwin = len(yint) // L
        if nwin < 4:
            return float("nan")
        segs = yint[: nwin * L].reshape(nwin, L)
        t = np.arange(L, dtype=np.float64)
        A = np.vstack([t, np.ones(L)]).T
        coef, *_ = np.linalg.lstsq(A, segs.T, rcond=None)
        resid = segs.T - A @ coef
        flucts.append(np.sqrt(np.mean(resid**2)))
    flucts = np.asarray(flucts)
    if np.any(flucts <= 0):
        return float("nan")
    alpha = np.polyfit(np.log(scales), np.log(flucts), 1)[0]
    return float(1.0 / (1.0 + np.exp(-alpha)))


def correlation_dimension(
    x: np.ndarray,
    m: int = 10,
    tau: int = 8,
    n_points: int = 1500,
    theiler: int = 50,
    seed: int = 0,
) -> float:
    """Grassberger-Procaccia correlation dimension D2 (approximation; see FEATURE_NOTES)."""
    x = np.asarray(x, dtype=np.float64)
    if np.std(x) == 0:
        return float("nan")
    x = (x - x.mean()) / np.std(x)
    emb = _embed(x, m, tau)
    if len(emb) < 200:
        return float("nan")
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(emb), size=min(n_points, len(emb)), replace=False))
    from scipy.spatial.distance import pdist

    dist = pdist(emb[idx])  # condensed, same order as triu_indices(k=1)
    iu = np.triu_indices(len(idx), k=1)
    sep = np.abs(idx[iu[0]] - idx[iu[1]])
    d = dist[sep > theiler]
    d = d[d > 0]
    if len(d) < 100:
        return float("nan")
    r = np.exp(np.linspace(np.log(np.percentile(d, 2)), np.log(np.percentile(d, 30)), 10))
    d_sorted = np.sort(d)
    c = np.searchsorted(d_sorted, r) / len(d_sorted)
    ok = c > 0
    if ok.sum() < 3:
        return float("nan")
    return float(np.polyfit(np.log(r[ok]), np.log(c[ok]), 1)[0])


def _ar_residual(s: np.ndarray, order: int = 2) -> np.ndarray:
    """Whiten a sequence by least-squares linear prediction of the given order."""
    s = np.asarray(s, dtype=np.float64)
    s = s - s.mean()
    if len(s) <= order + 2:
        return s
    X = np.column_stack([s[order - k - 1 : len(s) - k - 1] for k in range(order)])
    target = s[order:]
    coef, *_ = np.linalg.lstsq(X, target, rcond=None)
    return target - X @ coef


def pitch_nonlinear_measures(f0: np.ndarray) -> dict[str, float]:
    """PPE, spread1, spread2 from a voiced F0 contour (Hz). See FEATURE_NOTES."""
    f0 = np.asarray(f0, dtype=np.float64)
    f0 = f0[np.isfinite(f0) & (f0 > 0)]
    if len(f0) < 10:
        return {"PPE": float("nan"), "spread1": float("nan"), "spread2": float("nan")}
    semis = 12.0 * np.log2(f0 / PPE_REF_HZ)
    resid = _ar_residual(semis, order=2)
    edges = np.linspace(-3.0, 3.0, 61)
    hist, _ = np.histogram(np.clip(resid, -3.0, 3.0 - 1e-9), bins=edges)
    p = hist[hist > 0] / hist.sum()
    ppe = float(-np.sum(p * np.log(p)) / np.log(len(edges) - 1))
    resid_std_oct = float(np.std(resid) / 12.0)
    spread1 = float(np.log(max(resid_std_oct, 1e-6)))
    spread2 = float(np.std(semis - semis.mean()) / 12.0)
    return {"PPE": ppe, "spread1": spread1, "spread2": spread2}


def cpps(snd: "Any") -> float:
    """Smoothed cepstral peak prominence (dB) of a parselmouth Sound; ``nan`` on failure.

    Praat >= 6.1 argument lists (verified against parselmouth 0.4.x / Praat 6.1.38):

    * ``To PowerCepstrogram``: pitch floor (Hz), time step (s), maximum frequency (Hz),
      pre-emphasis from (Hz).
    * ``Get CPPS``: subtract tilt before smoothing, time averaging window (s), quefrency
      averaging window (s), peak search pitch range floor/ceiling (Hz), tolerance (0-1),
      interpolation, tilt line quefrency range start/end (s), line type, fit method.
    """
    from parselmouth.praat import call

    try:
        # Praat silently returns 0 dB for silence / sub-window input; report "not computable" instead.
        if snd.get_total_duration() < 0.1 or not np.any(np.asarray(snd.values)):
            return float("nan")
        cep = call(snd, "To PowerCepstrogram", _CPPS_PITCH_FLOOR_HZ, 0.002, 5000.0, 50.0)
        val = call(
            cep, "Get CPPS",
            False, 0.02, 0.0005, *_CPPS_PEAK_SEARCH_HZ, 0.05, "Parabolic", 0.001, 0.05, "Straight", "Robust",
        )
        val = float(val)
        return val if np.isfinite(val) else float("nan")
    except Exception as exc:  # noqa: BLE001 - PraatError on degenerate input
        logger.warning("Praat CPPS failed: %s", exc)
        return float("nan")


# --------------------------------------------------------------------------- main API

def extract_vowel_features(y: np.ndarray, sr: int) -> dict[str, float]:
    """Compute the 22 UCI Parkinson's dysphonia features (+ ``EXTRA_FEATURES``) from a sustained /a/.

    Args:
        y: mono waveform (float, [-1, 1]).
        sr: sample rate in Hz.

    Returns:
        dict whose first 22 keys are exactly ``UCI_FEATURES`` (the v1 model
        input, unchanged), followed by ``EXTRA_FEATURES`` (currently ``CPPS``).
        A value is ``nan`` when it cannot be computed (e.g. too few voiced
        periods); the serving model reports those as missing.
    """
    import parselmouth
    from parselmouth.praat import call

    y = np.asarray(y, dtype=np.float64)
    if y.ndim > 1:
        y = y.mean(axis=0 if y.shape[0] < y.shape[1] else 1)
    y = _main_voiced_region(y, sr)
    out: dict[str, float] = {k: float("nan") for k in (*UCI_FEATURES, *EXTRA_FEATURES)}
    if len(y) < int(0.2 * sr):
        logger.warning("vowel too short for feature extraction (%d samples)", len(y))
        return out

    snd = parselmouth.Sound(y, sampling_frequency=float(sr))

    # --- F0 statistics
    pitch = snd.to_pitch_ac(pitch_floor=PITCH_FLOOR_HZ, pitch_ceiling=PITCH_CEILING_HZ)
    f0 = pitch.selected_array["frequency"]
    voiced = f0[f0 > 0]
    if len(voiced):
        out["MDVP:Fo(Hz)"] = float(np.mean(voiced))
        out["MDVP:Fhi(Hz)"] = float(np.max(voiced))
        out["MDVP:Flo(Hz)"] = float(np.min(voiced))

    # --- jitter / shimmer (Praat Voice 2/3)
    try:
        pp = call(snd, "To PointProcess (periodic, cc)", PITCH_FLOOR_HZ, PITCH_CEILING_HZ)
        jargs = (0.0, 0.0, _PERIOD_FLOOR, _PERIOD_CEILING, _MAX_PERIOD_FACTOR)
        out["MDVP:Jitter(%)"] = call(pp, "Get jitter (local)", *jargs)
        out["MDVP:Jitter(Abs)"] = call(pp, "Get jitter (local, absolute)", *jargs)
        out["MDVP:RAP"] = call(pp, "Get jitter (rap)", *jargs)
        out["MDVP:PPQ"] = call(pp, "Get jitter (ppq5)", *jargs)
        out["Jitter:DDP"] = call(pp, "Get jitter (ddp)", *jargs)
        sargs = jargs + (_MAX_AMP_FACTOR,)
        out["MDVP:Shimmer"] = call([snd, pp], "Get shimmer (local)", *sargs)
        out["MDVP:Shimmer(dB)"] = call([snd, pp], "Get shimmer (local_dB)", *sargs)
        out["Shimmer:APQ3"] = call([snd, pp], "Get shimmer (apq3)", *sargs)
        out["Shimmer:APQ5"] = call([snd, pp], "Get shimmer (apq5)", *sargs)
        out["MDVP:APQ"] = call([snd, pp], "Get shimmer (apq11)", *sargs)
        out["Shimmer:DDA"] = call([snd, pp], "Get shimmer (dda)", *sargs)
    except Exception as exc:  # noqa: BLE001 - Praat raises PraatError on degenerate input
        logger.warning("Praat jitter/shimmer failed: %s", exc)

    # --- HNR / NHR
    try:
        harm = call(snd, "To Harmonicity (cc)", 0.01, PITCH_FLOOR_HZ, 0.1, 1.0)
        hnr = call(harm, "Get mean", 0.0, 0.0)
        out["HNR"] = float(hnr)
        out["NHR"] = float(10.0 ** (-hnr / 10.0))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Praat harmonicity failed: %s", exc)

    # --- nonlinear signal measures at 25 kHz
    y25 = _resample(y, sr, NONLINEAR_SR)
    out["RPDE"] = rpde(_central(y25, int(0.3 * NONLINEAR_SR)))
    out["DFA"] = dfa(_central(y25, NONLINEAR_SR))
    out["D2"] = correlation_dimension(_central(y25, NONLINEAR_SR)[::2])

    # --- pitch-contour nonlinear measures
    out.update(pitch_nonlinear_measures(voiced))

    # --- extra (non-UCI) features: smoothed cepstral peak prominence
    out["CPPS"] = cpps(snd)

    return {k: (float(out[k]) if out[k] is not None else float("nan")) for k in (*UCI_FEATURES, *EXTRA_FEATURES)}


def feature_notes_summary() -> dict[str, Any]:
    """Return FEATURE_NOTES plus the overall domain-shift note (for model cards/API)."""
    return {
        "overall": _OVERALL_NOTE,
        "approximate_features": [k for k, v in FEATURE_NOTES.items() if v["approximate"]],
        "features": FEATURE_NOTES,
    }
