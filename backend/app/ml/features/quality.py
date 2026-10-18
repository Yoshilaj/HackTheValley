"""Recording-quality gate shared by all audio tasks.

Spec: docs/specs/phase-a-assessments.md §5.4 (serving: "recording_quality gate
(duration >= 3 s voiced, SNR >= 15 dB, no clipping) else invalid_measurement")
and §10 item 3 (data quality stored separately from the model score).

The gate is deliberately simple and transparent:

* **voiced_s** — total length of non-silent intervals from
  ``librosa.effects.split`` (energy within ``top_db`` of the loudest frame).
* **snr_db** — 10*log10(mean power of voiced frames / mean power of noise
  frames). Noise frames are the frames outside the voiced intervals when at
  least ``_MIN_NOISE_FRAMES`` exist, otherwise the lowest-energy 10 % of all
  frames (a continuous recording with no silence therefore gets a pessimistic
  SNR — the task instructions ask the user to start recording before phonating).
* **clip_frac** — fraction of samples with ``|y| > 0.99`` (full-scale float).

Recording-condition signals (informational, **not** part of the pass/fail gate;
stored so the personal voice-stability rule PD-03b can tell a changed room or
microphone from a changed voice — Bottalico 2018 showed room acoustics shift
HNR by 7-10 dB, Barsties 2025 that smartphone vs clinical microphones offset
perturbation measures):

* **noise_floor_dbfs** — RMS (dBFS) of the quietest 10 % of 50 ms frames.
* **level_dbfs** — RMS (dBFS) of the voiced part (samples inside the non-silent
  intervals; whole recording when there is none).
* **clipping_ratio** — alias of ``clip_frac`` under the name the rule layer uses.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Union

import numpy as np

logger = logging.getLogger(__name__)

AudioSource = Union[str, os.PathLike, bytes, bytearray]

_FRAME = 1024
_HOP = 256
_TOP_DB = 30.0
_MIN_NOISE_FRAMES = 3
_SNR_CAP_DB = 100.0
_CLIP_LEVEL = 0.99
_COND_FRAME_S = 0.050  # 50 ms frames for the noise-floor estimate
_NOISE_FLOOR_QUANTILE = 0.10
_DBFS_FLOOR = -120.0  # reported for digital silence instead of -inf


@dataclass
class RecordingQuality:
    """Result of :func:`assess_quality`; serialisable to the ``recording_quality`` JSON column.

    ``noise_floor_dbfs``, ``level_dbfs`` and ``clipping_ratio`` are
    recording-condition signals; they never affect ``ok``/``reasons``.
    """

    duration_s: float
    voiced_s: float
    snr_db: float
    clip_frac: float
    ok: bool
    reasons: list[str] = field(default_factory=list)
    noise_floor_dbfs: float | None = None  # None = not computed (empty/silent input); JSON-safe
    level_dbfs: float | None = None
    clipping_ratio: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _dbfs(power: float) -> float:
    """Mean-square power (full-scale float) to dBFS, floored at ``_DBFS_FLOOR``."""
    if not np.isfinite(power) or power <= 0.0:
        return _DBFS_FLOOR
    return float(max(_DBFS_FLOOR, 10.0 * np.log10(power)))


def recording_condition_signals(y: np.ndarray, sr: int, intervals: np.ndarray | None = None) -> dict[str, float | None]:
    """Noise floor, voiced level (dBFS) and clipping ratio of a mono float waveform.

    ``intervals`` are ``(start, end)`` sample pairs of the non-silent regions
    (``librosa.effects.split`` output); when omitted they are computed here.
    """
    y = _to_mono_float(y)
    if len(y) == 0 or sr <= 0 or not np.all(np.isfinite(y)):
        return {"noise_floor_dbfs": None, "level_dbfs": None, "clipping_ratio": 0.0}
    clipping_ratio = float(np.mean(np.abs(y) > _CLIP_LEVEL))

    n = max(1, int(round(_COND_FRAME_S * sr)))
    n_frames = len(y) // n
    if n_frames >= 1:
        frame_p = np.mean(y[: n_frames * n].reshape(n_frames, n) ** 2, axis=1)
    else:
        frame_p = np.array([np.mean(y**2)])
    k = max(1, int(round(_NOISE_FLOOR_QUANTILE * len(frame_p))))
    noise_floor_dbfs = _dbfs(float(np.mean(np.sort(frame_p)[:k])))

    if intervals is None:
        import librosa

        intervals = librosa.effects.split(y, top_db=_TOP_DB, frame_length=_FRAME, hop_length=_HOP)
    if len(intervals):
        voiced = np.concatenate([y[int(s) : int(e)] for s, e in intervals])
    else:
        voiced = y
    level_dbfs = _dbfs(float(np.mean(voiced**2))) if len(voiced) else _DBFS_FLOOR
    return {"noise_floor_dbfs": noise_floor_dbfs, "level_dbfs": level_dbfs, "clipping_ratio": clipping_ratio}


def _to_mono_float(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y)
    if y.ndim == 2:
        # soundfile returns (n, channels); librosa returns (channels, n)
        y = y.mean(axis=1) if y.shape[0] > y.shape[1] else y.mean(axis=0)
    if np.issubdtype(y.dtype, np.integer):
        y = y.astype(np.float64) / float(np.iinfo(y.dtype).max)
    return y.astype(np.float64, copy=False)


def _frame_power(y: np.ndarray) -> np.ndarray:
    if len(y) < _FRAME:
        return np.array([np.mean(y**2)]) if len(y) else np.array([0.0])
    from numpy.lib.stride_tricks import sliding_window_view

    frames = sliding_window_view(y, _FRAME)[::_HOP]
    return np.mean(frames**2, axis=1)


def assess_quality(
    y: np.ndarray,
    sr: int,
    min_voiced_s: float = 3.0,
    min_snr_db: float = 15.0,
    max_clip_frac: float = 0.01,
) -> RecordingQuality:
    """Assess whether a recording is usable for acoustic analysis.

    Args:
        y: mono (or multi-channel, will be down-mixed) waveform, float in [-1, 1].
        sr: sample rate in Hz.
        min_voiced_s: minimum total voiced/non-silent duration (spec: 3 s).
        min_snr_db: minimum estimated SNR (spec: 15 dB).
        max_clip_frac: maximum fraction of samples at |y| > 0.99.

    Returns:
        RecordingQuality with ``ok`` False and human-readable ``reasons`` when
        any criterion fails; the caller then records ``invalid_measurement``.
    """
    import librosa

    y = _to_mono_float(y)
    reasons: list[str] = []
    duration_s = float(len(y) / sr) if sr > 0 else 0.0
    if len(y) == 0 or not np.all(np.isfinite(y)) or np.max(np.abs(y)) == 0.0:
        return RecordingQuality(duration_s, 0.0, 0.0, 0.0, False, ["empty_or_silent_recording"])

    clip_frac = float(np.mean(np.abs(y) > _CLIP_LEVEL))

    intervals = librosa.effects.split(y, top_db=_TOP_DB, frame_length=_FRAME, hop_length=_HOP)
    voiced_s = float(sum(int(e - s) for s, e in intervals) / sr)

    power = _frame_power(y)
    n_frames = len(power)
    starts = np.arange(n_frames) * _HOP
    centres = starts + _FRAME // 2
    voiced_mask = np.zeros(n_frames, dtype=bool)
    for s, e in intervals:
        voiced_mask |= (centres >= s) & (centres < e)
    noise_mask = ~voiced_mask
    if noise_mask.sum() < _MIN_NOISE_FRAMES:
        k = max(1, int(round(0.1 * n_frames)))
        noise_mask = np.zeros(n_frames, dtype=bool)
        noise_mask[np.argsort(power)[:k]] = True
    sig_p = float(np.mean(power[voiced_mask])) if voiced_mask.any() else float(np.mean(power))
    noise_p = float(np.mean(power[noise_mask]))
    if noise_p <= 0.0:
        snr_db = _SNR_CAP_DB
    else:
        snr_db = float(min(_SNR_CAP_DB, 10.0 * np.log10(max(sig_p, 1e-20) / noise_p)))

    if voiced_s < min_voiced_s:
        reasons.append(f"voiced_duration_below_{min_voiced_s:g}s")
    if snr_db < min_snr_db:
        reasons.append(f"snr_below_{min_snr_db:g}db")
    if clip_frac > max_clip_frac:
        reasons.append("clipping")
    cond = recording_condition_signals(y, sr, intervals)
    return RecordingQuality(
        duration_s=duration_s,
        voiced_s=voiced_s,
        snr_db=snr_db,
        clip_frac=clip_frac,
        ok=not reasons,
        reasons=reasons,
        noise_floor_dbfs=cond["noise_floor_dbfs"],
        level_dbfs=cond["level_dbfs"],
        clipping_ratio=cond["clipping_ratio"],
    )


def _ffmpeg_decode(path: str) -> tuple[np.ndarray, int]:
    """Decode any container (webm/mp3/m4a) to mono float32 WAV via ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not found on PATH; cannot decode this audio format")
    import soundfile as sf

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "decoded.wav")
        subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", path, "-ac", "1", "-f", "wav", out],
            check=True,
        )
        y, sr = sf.read(out, dtype="float64", always_2d=False)
    return y, int(sr)


def load_audio(path_or_bytes: AudioSource, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    """Load audio as mono float64.

    Tries ``soundfile`` first (wav/flac/ogg), then ``librosa.load``, then an
    ``ffmpeg`` subprocess (mp3/webm/m4a from browsers). Resamples with
    ``librosa.resample`` when ``target_sr`` is given.
    """
    import soundfile as sf

    y: np.ndarray | None = None
    sr = 0
    tmp_path: str | None = None
    try:
        if isinstance(path_or_bytes, (bytes, bytearray)):
            try:
                y, sr = sf.read(io.BytesIO(bytes(path_or_bytes)), dtype="float64", always_2d=False)
            except Exception:  # noqa: BLE001 - fall through to ffmpeg
                fd, tmp_path = tempfile.mkstemp(suffix=".audio")
                with os.fdopen(fd, "wb") as fh:
                    fh.write(bytes(path_or_bytes))
                source = tmp_path
        else:
            source = str(Path(path_or_bytes))
            try:
                y, sr = sf.read(source, dtype="float64", always_2d=False)
            except Exception:  # noqa: BLE001
                y = None
        if y is None:
            try:
                import librosa

                y, sr = librosa.load(source, sr=None, mono=True, dtype=np.float64)
            except Exception as exc:  # noqa: BLE001
                logger.debug("librosa.load failed (%s); trying ffmpeg", exc)
                y, sr = _ffmpeg_decode(source)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    y = _to_mono_float(y)
    sr = int(sr)
    if target_sr is not None and int(target_sr) != sr:
        import librosa

        y = librosa.resample(y, orig_sr=sr, target_sr=int(target_sr))
        sr = int(target_sr)
    return y, sr
