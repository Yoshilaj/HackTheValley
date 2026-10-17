"""EXPERIMENTAL semantic-recall score for the story-recall task (spec §1.1 "Primary score", §1.3).

This is **our own experimental score**. It is *not* Novoic's published
score and is not equivalent to it: Novoic's weights and language model are
unpublished, our stories/TTS/ASR/embedding model differ, and so their
published thresholds (≈59-60) and AUCs **do not transfer**. Scores are only
meaningful against the same user's own baseline (§1.3: median/MAD of the
first 5 valid sessions), with the same embedding provider (``provider.name``
is stored with every score — never compare scores across providers).

* :func:`semantic_recall` — cosine similarity of whole-text embeddings
  (story vs. transcript) scaled to 0-100 as ``100 * max(0, cos)``. No
  rescaling is applied, so absolute values depend on the embedding model
  (e.g. unrelated same-language texts are often 20-40 with ``voyage-3``).
* :func:`content_unit_recall` — secondary, interpretable: fraction of the
  story's content units (propositions a human rater would check) whose best
  match among transcript chunks has cosine ≥ ``threshold``.
* :func:`score_session` — the three recalls of one ADNI4-style session
  (immediate 1, immediate 2, delayed 1) with §1.1 exclusions.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import numpy as np

from app.content.loader import STORY_BANK_VERSION, Story
from app.ml.features.speech import (
    lexical_features,
    normalize_language,
    split_sentences,
    spectral_features,
    temporal_features,
    tokenize,
)
from app.providers.embeddings import EmbeddingProvider, cosine
from app.providers.stt import Transcript

__all__ = [
    "PROTOCOL_VERSION",
    "SCORE_NAME",
    "MIN_SPEECH_S",
    "RecallScore",
    "RecallSessionScore",
    "semantic_recall",
    "content_unit_recall",
    "check_recall_validity",
    "score_recall",
    "score_session",
]

SCORE_NAME = "experimental_semantic_recall"
PROTOCOL_VERSION = f"story-recall-adni4-adapted/v1+bank-{STORY_BANK_VERSION}"
RECALL_LABELS = ("immediate_1", "immediate_2", "delayed")
MIN_SPEECH_S = 5.0  # §1.1 exclusion: speech duration <5 s
MAX_CLIPPING_FRACTION = 0.01  # §1.1: clipping >1%
MIN_SNR_DB = 10.0  # §1.1: SNR <10 dB
_CHUNK_TOKENS = {"en": 14, "ja": 24}

AudioClip = tuple[np.ndarray, int]


def _text(t: Transcript | str) -> str:
    return t.text if isinstance(t, Transcript) else str(t or "")


def semantic_recall(story_text: str, transcript: Transcript | str, provider: EmbeddingProvider) -> float:
    """Experimental semantic-recall score 0-100 = ``100 * max(0, cos(embed(story), embed(transcript)))``.

    Not equivalent to Novoic's score (see module docstring). Empty transcript → 0.0.
    """
    text = _text(transcript).strip()
    if not text or not story_text.strip():
        return 0.0
    a, b = provider.embed([story_text, text])
    return round(100.0 * max(0.0, cosine(a, b)), 2)


def _chunks(text: str, language: str) -> list[str]:
    """Transcript sentences; run-on ASR output (common in JA) is cut into ~fixed-size token windows."""
    lang = normalize_language(language)
    size = _CHUNK_TOKENS[lang]
    out: list[str] = []
    joiner = "" if lang == "ja" else " "
    for sent in split_sentences(text) or [text]:
        toks = tokenize(sent, lang)
        if len(toks) <= int(size * 1.5):
            out.append(sent)
            continue
        step = max(1, size // 2)  # 50% overlap so a proposition is not split across windows
        for i in range(0, max(1, len(toks) - size + step), step):
            out.append(joiner.join(toks[i : i + size]))
    return [c for c in out if c.strip()]


def content_unit_recall(
    content_units: Sequence[str],
    transcript: Transcript | str,
    provider: EmbeddingProvider,
    threshold: float = 0.6,
    language: str | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Secondary interpretable score: fraction of content units recalled.

    Each unit is embedded and compared with every transcript chunk
    (sentences, or overlapping token windows for unpunctuated ASR text); a
    unit's score is its maximum cosine, and it counts as recalled when the
    score ≥ ``threshold``. The threshold is provider-dependent and must be
    calibrated per embedding model (0.6 is a starting point, not validated).

    Returns ``(fraction, [{unit, score, recalled}, ...])``.
    """
    text = _text(transcript)
    lang = normalize_language(language or (transcript.language if isinstance(transcript, Transcript) else "en"))
    if not content_units:
        return 0.0, []
    chunks = _chunks(text, lang) if text.strip() else []
    if not chunks:
        return 0.0, [{"unit": u, "score": 0.0, "recalled": False} for u in content_units]
    vecs = provider.embed(list(content_units) + chunks)
    unit_vecs, chunk_vecs = vecs[: len(content_units)], vecs[len(content_units) :]
    per: list[dict[str, Any]] = []
    for u, uv in zip(content_units, unit_vecs):
        best = max(cosine(uv, cv) for cv in chunk_vecs)
        per.append({"unit": u, "score": round(best, 4), "recalled": best >= threshold})
    return sum(p["recalled"] for p in per) / len(per), per


def _quality(y: np.ndarray, sr: int) -> dict[str, Any]:
    """Recording quality via ``app.ml.features.quality.assess_quality`` if present, else a local estimate.

    Thresholds passed are the story-task ones (§1.1: SNR ≥10 dB, clipping ≤1%),
    not the vowel-task defaults of that module.
    """
    try:  # owned by another module; optional at runtime
        from app.ml.features.quality import assess_quality

        res = assess_quality(y, sr, min_voiced_s=MIN_SPEECH_S, min_snr_db=MIN_SNR_DB, max_clip_frac=MAX_CLIPPING_FRACTION)
        d = res.to_dict() if hasattr(res, "to_dict") else dict(res)
        d.setdefault("clipping_fraction", d.get("clip_frac", 0.0))
        return {"source": "quality.assess_quality", **d}
    except Exception:
        return _local_quality(y, sr)


def _local_quality(y: np.ndarray, sr: int) -> dict[str, Any]:
    """Fallback: clipping fraction (|x| ≥ 0.999) and SNR = 95th/10th percentile frame RMS in dB."""
    y = np.asarray(y, dtype=np.float32).reshape(-1)
    if y.size == 0:
        return {"source": "local", "clipping_fraction": 0.0, "snr_db": None, "duration_s": 0.0}
    hop = max(1, int(0.02 * sr))
    n = y.size // hop
    rms = np.sqrt(np.mean(y[: n * hop].reshape(n, hop) ** 2, axis=1)) if n else np.array([0.0])
    hi, lo = float(np.percentile(rms, 95)), float(np.percentile(rms, 10))
    snr = 20.0 * math.log10(max(hi, 1e-10) / max(lo, 1e-10)) if hi > 0 else None
    return {
        "source": "local",
        "clipping_fraction": float(np.mean(np.abs(y) >= 0.999)),
        "snr_db": snr,
        "duration_s": y.size / float(sr),
    }


def check_recall_validity(
    transcript: Transcript | None,
    temporal: dict[str, Any] | None,
    quality: dict[str, Any] | None = None,
) -> list[str]:
    """§1.1 exclusions → list of reasons (empty = valid).

    ``no_transcript`` / ``empty_transcript`` (transcription failure or no
    audible speech), ``speech_lt_5s``, and with audio quality available
    ``clipping_gt_1pct`` / ``snr_lt_10db``.
    """
    if transcript is None:
        return ["no_transcript"]
    reasons: list[str] = []
    if not transcript.text.strip() or not tokenize(transcript.text, transcript.language):
        reasons.append("empty_transcript")
    speech_s: float | None = None
    if temporal and temporal.get("timing_source") != "none":
        speech_s = float(temporal.get("speech_duration_s") or 0.0)
    elif transcript.duration_s is not None:
        speech_s = float(transcript.duration_s)
    if speech_s is not None and speech_s < MIN_SPEECH_S:
        reasons.append("speech_lt_5s")
    if quality:
        if (quality.get("clipping_fraction") or 0.0) > MAX_CLIPPING_FRACTION:
            reasons.append("clipping_gt_1pct")
        snr = quality.get("snr_db")
        if snr is not None and snr < MIN_SNR_DB:
            reasons.append("snr_lt_10db")
    return reasons


@dataclass
class RecallScore:
    """One retelling's scores and features."""

    label: str
    story_id: str
    valid: bool
    invalid_reasons: list[str]
    semantic_recall: float | None
    content_unit_fraction: float | None
    content_units: list[dict[str, Any]]
    lexical: dict[str, Any]
    temporal: dict[str, Any]
    spectral: dict[str, Any] | None = None
    recording_quality: dict[str, Any] | None = None
    asr_confidence: float | None = None
    transcript_text: str = ""


@dataclass
class RecallSessionScore:
    """Per-session output (§1.3, §10 item 1+3): never collapsed into one number by this module.

    ``semantic_recall_mean`` is the unweighted mean over *valid* recalls;
    ``valid`` is True only when all three recalls are valid (only valid
    sessions feed the personal baseline).
    """

    semantic_recall_mean: float | None
    per_recall: dict[str, float | None]
    content_unit_fraction_mean: float | None
    lexical: dict[str, dict[str, Any]]
    temporal: dict[str, dict[str, Any]]
    language: str
    protocol_version: str
    story_ids: list[str]
    valid: bool
    invalid_reasons: list[str]
    score_name: str = SCORE_NAME
    embedding_provider: str = ""
    recalls: list[RecallScore] = field(default_factory=list)
    status: str = "ok"  # "ok" | "invalid_measurement"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_recall(
    label: str,
    story: Story,
    transcript: Transcript | None,
    provider: EmbeddingProvider,
    audio: AudioClip | None = None,
    content_unit_threshold: float = 0.6,
    with_spectral: bool = False,
) -> RecallScore:
    """Score one retelling against its story, applying §1.1 exclusions."""
    lang = story.language
    if transcript is None:
        return RecallScore(label, story.id, False, ["no_transcript"], None, None, [], {}, {})
    y, sr = audio if audio is not None else (None, None)
    temporal = temporal_features(
        transcript.word_timestamps or transcript.segments,
        transcript.duration_s,
        y=y, sr=sr, language=lang, text=transcript.text,
    )
    quality = _quality(y, sr) if y is not None and sr else None
    reasons = check_recall_validity(transcript, temporal, quality)
    lexical = lexical_features(transcript.text, lang)
    spectral = None
    if with_spectral and y is not None and sr:
        try:
            spectral = spectral_features(y, sr)
        except Exception:  # feature-extraction failure is an exclusion reason (§1.1)
            reasons.append("spectral_extraction_failed")
    if reasons:
        return RecallScore(label, story.id, False, reasons, None, None, [], lexical, temporal,
                           spectral, quality, transcript.confidence, transcript.text)
    sem = semantic_recall(story.text, transcript, provider)
    frac, per = content_unit_recall(story.content_units, transcript, provider, content_unit_threshold, lang)
    return RecallScore(label, story.id, True, [], sem, round(frac, 4), per, lexical, temporal,
                       spectral, quality, transcript.confidence, transcript.text)


def score_session(
    story: Story | Sequence[Story],
    recalls: Sequence[Transcript | None],
    provider: EmbeddingProvider,
    audio: Sequence[AudioClip | None] | None = None,
    content_unit_threshold: float = 0.6,
    with_spectral: bool = False,
) -> RecallSessionScore:
    """Score a session's recalls ``[immediate_1, immediate_2, delayed]`` (§1.1 battery).

    ``story`` may be one :class:`Story` (used for every recall) or
    ``[story1, story2]`` (mapped story1, story2, story1 — the ADNI4 order) or
    three stories (one per recall). Missing recalls count as invalid
    (``missing_<label>``).
    """
    stories = [story] if isinstance(story, Story) else list(story)
    if len(stories) == 1:
        mapping = [stories[0]] * 3
    elif len(stories) == 2:
        mapping = [stories[0], stories[1], stories[0]]
    elif len(stories) == 3:
        mapping = stories
    else:
        raise ValueError("story must be 1, 2 or 3 stories")
    languages = {s.language for s in stories}
    if len(languages) != 1:
        raise ValueError("all stories in a session must share one language")
    language = languages.pop()
    audio = list(audio or [])
    results: list[RecallScore] = []
    reasons: list[str] = []
    for i, label in enumerate(RECALL_LABELS):
        if i >= len(recalls):
            reasons.append(f"missing_{label}")
            results.append(RecallScore(label, mapping[i].id, False, ["missing"], None, None, [], {}, {}))
            continue
        r = score_recall(label, mapping[i], recalls[i], provider, audio[i] if i < len(audio) else None,
                         content_unit_threshold, with_spectral)
        reasons.extend(f"{label}:{x}" for x in r.invalid_reasons)
        results.append(r)
    valid_scores = [r.semantic_recall for r in results if r.valid and r.semantic_recall is not None]
    valid_fracs = [r.content_unit_fraction for r in results if r.valid and r.content_unit_fraction is not None]
    valid = not reasons
    return RecallSessionScore(
        semantic_recall_mean=round(statistics.fmean(valid_scores), 2) if valid_scores else None,
        per_recall={r.label: r.semantic_recall for r in results},
        content_unit_fraction_mean=round(statistics.fmean(valid_fracs), 4) if valid_fracs else None,
        lexical={r.label: r.lexical for r in results},
        temporal={r.label: r.temporal for r in results},
        language=language,
        protocol_version=PROTOCOL_VERSION,
        story_ids=list(dict.fromkeys(s.id for s in stories)),
        valid=valid,
        invalid_reasons=reasons,
        embedding_provider=getattr(provider, "name", type(provider).__name__),
        recalls=results,
        status="ok" if valid else "invalid_measurement",
    )
