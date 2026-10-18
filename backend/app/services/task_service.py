"""Assessment-task service: story recall, sustained vowel, finger tapping, 3-word recall.

Spec: docs/specs/phase-a-assessments.md
- §1.1/§1.3 story-recall battery (state machine in ``app/ml/story_task.py``; scoring in
  ``app/ml/recall.py``). One ``AssessmentTask(task_type='story_recall')`` row per session;
  ``raw_json`` holds ``StorySession.to_dict()`` (+ ``audio_urls``). ``valid`` stays False
  (``invalid_reason='in_progress'``) until the battery is finalised.
- §1.4 3-word delayed recall → ``AssessmentTask(word_recall)``.
- §5.3 finger tapping → ``AssessmentTask(finger_tap)`` (features read by PD-04).
- §5.4 sustained vowel → ``AssessmentTask(sustained_vowel)`` with
  ``model_scores['pd_vowel']`` (read by PD-03).
- §10 output contract: measured features, model score + version, and data quality are
  stored in separate columns, never collapsed into one number.

Routes stay thin (``app/api/routes/tasks.py``); errors are raised as :class:`TaskError`
with an HTTP status code for the route to translate.
"""

from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.loader import STORY_BANK_VERSION, ContentError
from app.core.logging import get_logger
from app.core.security import audit
from app.db.models import TASK_TYPES, AssessmentTask, User
from app.ml.features.quality import load_audio
from app.ml.features.speech import normalize_language
from app.ml.features.tapping import tapping_session_features
from app.ml.recall import PROTOCOL_VERSION
from app.ml.story_task import (
    INSTRUCTIONS,
    MAX_RECALL_S,
    RECALL_STEPS,
    DelayNotElapsedError,
    StepOrderError,
    StorySession,
    finalize,
    new_session,
    new_word_recall,
    next_step,
    score_word_recall,
)
from app.providers.embeddings import EmbeddingProvider
from app.providers.storage import Storage, get_storage
from app.providers.stt import SttProvider, Transcript
from app.providers.tts import DEFAULT_VOICE, TtsProvider

log = get_logger(__name__)

VOWEL_PROTOCOL_VERSION = "vowel-1"
TAPPING_PROTOCOL_VERSION = "tapping-2"  # keyboard BRAIN tap test (s / ; keys, 30 s per hand)
TAPPING_DEVICE = "keyboard"               # PD-04 baseline is per input method (context_builder.tapping_series)
#: flat session-level feature keys read by the PD-04 series (mean of both hands when valid)
TAPPING_FLAT_KEYS = ("tap_count", "kinesia_score", "mean_iti_ms", "iti_cv", "decrement_slope_ms_per_tap",
                     "dwell_mean_ms", "dwell_cv", "travel_time_var_ms2", "travel_time_cv", "alternation_error_rate")
WORD_RECALL_PROTOCOL_VERSION = "word-recall-1"
PD_VOWEL_KEY = "pd_vowel"  # app/ml/rules.py PD_VOWEL_MODEL_KEY
VOWEL_DETAIL_LABEL = "experimental voice-model score"
IN_PROGRESS = "in_progress"
LISTEN_STEPS = ("story1_listen", "story2_listen")
TRANSCRIBED_STEPS = (*RECALL_STEPS, "fluency_60s")

#: §5.4 recording instructions, shown when the quality gate fails.
VOWEL_TIPS: dict[str, list[str]] = {
    "en": [
        "Say a steady \"ahh\" for about 5 seconds at a comfortable pitch and loudness.",
        "Hold the phone 10-15 cm from your mouth.",
        "Record in a quiet room (no TV, fan or traffic noise).",
        "Start recording first, wait a moment, then begin the \"ahh\".",
        "Don't hold the phone too close or speak too loudly, which distorts the sound.",
    ],
    "ja": [
        "楽な高さと大きさで「あー」と約5秒間、一定の声で発声してください。",
        "スマートフォンを口から10〜15cm離して持ってください。",
        "静かな部屋で録音してください（テレビ・扇風機・車の音がない場所）。",
        "先に録音を開始し、少し待ってから「あー」と発声してください。",
        "近づけすぎたり大きすぎる声は音が割れる原因になります。",
    ],
}
_WR_RECALL_PROMPT = {
    "en": "Earlier I asked you to remember three words. What were they?",
    "ja": "先ほど覚えていただいた三つの言葉は何でしたか？",
}


def device_label(user_agent: str | None, explicit: str | None = None) -> str | None:
    """Coarse recording-device label stored with voice tasks (PD-03B, spec §5.5: a voice baseline is only
    compared within one device). An explicit client ``device`` string wins; otherwise the platform family
    (+ Android model) is read from the User-Agent so browser version bumps do not restart the baseline.
    ``None`` when nothing is known."""
    if explicit and explicit.strip():
        return explicit.strip()[:64]
    ua = (user_agent or "").strip()
    if not ua:
        return None
    low = ua.lower()
    if "ipad" in low:
        return "iPad"
    if "iphone" in low:
        return "iPhone"
    if "android" in low:
        inner = ua[ua.find("(") + 1:ua.find(")")] if "(" in ua and ")" in ua else ""
        parts = [p.strip() for p in inner.split(";")]
        model = next((p.split(" Build")[0] for p in parts
                      if p and not p.lower().startswith(("linux", "android", "wv", "u", "mobile"))), "")
        return f"Android {model}".strip()[:64]
    if "macintosh" in low or "mac os x" in low:
        return "Mac"
    if "windows" in low:
        return "Windows"
    if "cros" in low:
        return "ChromeOS"
    if "linux" in low:
        return "Linux"
    return "other"


class TaskError(Exception):
    """Service error carrying an HTTP status code and optional extra payload."""

    def __init__(self, status_code: int, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.extra = extra


def _now() -> datetime:
    return datetime.now(timezone.utc)


def user_language(user: User, language: str | None = None) -> str:
    """Explicit language, else the profile language, else ``en`` (normalised to en|ja)."""
    lang = language or (user.profile.language if getattr(user, "profile", None) else None) or "en"
    return normalize_language(lang)


def _get_task(db: Session, user: User, task_id: str, task_type: str) -> AssessmentTask:
    task = db.get(AssessmentTask, task_id)
    if task is None or task.user_id != user.id or task.task_type != task_type:
        raise TaskError(404, f"{task_type} task not found")
    return task


# ================================================================ §1 story recall

def used_story_ids(db: Session, user: User) -> set[str]:
    """Story ids from every prior story_recall row of the user (§1.1: never reused)."""
    rows = db.scalars(select(AssessmentTask.raw_json).where(
        AssessmentTask.user_id == user.id, AssessmentTask.task_type == "story_recall"))
    used: set[str] = set()
    for raw in rows:
        if isinstance(raw, dict):
            used.update(str(s) for s in raw.get("story_ids") or [])
    return used


def load_session(task: AssessmentTask) -> StorySession:
    return StorySession.from_dict(task.raw_json or {})


def _save_session(task: AssessmentTask, session: StorySession) -> None:
    """Reassign ``raw_json`` (plain JSON column → mutations are not tracked)."""
    extra = {k: v for k, v in (task.raw_json or {}).items() if k not in StorySession.__dataclass_fields__}
    task.raw_json = {**session.to_dict(), **extra}


def start_story_session(db: Session, user: User, language: str | None = None,
                        device: str | None = None) -> tuple[AssessmentTask, StorySession]:
    """Create a battery with two stories the user has never heard (§1.1). Commits.
    ``device`` (see :func:`device_label`) is kept in ``raw_json`` for the PD-03B voice baseline."""
    lang = user_language(user, language)
    try:
        session = new_session(lang, used_story_ids(db, user))
    except ContentError as e:
        raise TaskError(409, f"no unused stories left for language {lang!r}") from e
    task = AssessmentTask(
        user_id=user.id, task_type="story_recall", protocol_version=session.protocol_version,
        language=session.language, raw_json={**session.to_dict(), "audio_urls": {},
                                             "story_bank_version": STORY_BANK_VERSION, "device": device},
        features=None, model_scores={}, score=None, valid=False, invalid_reason=IN_PROGRESS,
    )
    db.add(task)
    db.flush()
    audit(db, user.id, "task.story.start", f"assessment_task:{task.id}", {"story_ids": session.story_ids})
    db.commit()
    return task, session


def get_story_task(db: Session, user: User, task_id: str) -> tuple[AssessmentTask, StorySession]:
    task = _get_task(db, user, task_id, "story_recall")
    return task, load_session(task)


def delay_remaining_s(session: StorySession, now: datetime | None = None) -> float | None:
    """Seconds until the delayed recall may start (None when story 1 recall is not done yet)."""
    for e in session.step_log:
        if e["step"] == "story1_recall":
            elapsed = ((now or _now()) - datetime.fromisoformat(e["completed_at"])).total_seconds()
            return max(0.0, round(session.min_delay_s - elapsed, 1))
    return None


def _instruction(step: str | None, language: str) -> str:
    """Instruction text for ``step`` in ``language`` (same key mapping as ``StorySession.instruction``)."""
    if not step:
        return ""
    key = ("listen" if step.endswith("_listen") else "delayed_recall" if step == "story1_delayed_recall"
           else "recall" if step.endswith("_recall") else step)
    return INSTRUCTIONS[language].get(key, "")


def story_status(task: AssessmentTask, session: StorySession) -> dict[str, Any]:
    """Status payload (current step, JA/EN instruction, audio URL for listen steps, delay)."""
    step = session.current_step
    instr = {lang: _instruction(step, lang) for lang in INSTRUCTIONS}
    out: dict[str, Any] = {
        "task_id": task.id,
        "session_id": session.session_id,
        "language": session.language,
        "protocol_version": session.protocol_version,
        "steps": list(session.steps),
        "step_index": session.current_index,
        "current_step": step,
        "finished": session.finished,
        "instruction": instr.get(session.language, ""),
        "instructions": instr,
        "audio_url": f"/api/tasks/story/{task.id}/audio/{step}" if step in LISTEN_STEPS else None,
        "delay_remaining_s": delay_remaining_s(session) if step in ("fluency_60s", "delay") else None,
        "max_recall_s": MAX_RECALL_S,
        "result": story_result(task) if session.finished else None,
    }
    return out


def story_result(task: AssessmentTask) -> dict[str, Any]:
    f = task.features or {}
    reasons = list(f.get("invalid_reasons") or [])
    return {
        "score": task.score,
        "score_name": f.get("score_name", "semantic_recall"),
        "valid": bool(task.valid),
        "status": f.get("status", "ok" if task.valid else "invalid_measurement"),
        "invalid_reasons": reasons,
        "per_recall": f.get("per_recall") or {},
        "content_unit_fraction_mean": f.get("content_unit_fraction_mean"),
        "story_ids": f.get("story_ids") or (task.raw_json or {}).get("story_ids") or [],
        "message": ("Thank you — your story task is recorded." if task.valid else
                    "We couldn't measure this session reliably (no clear speech was captured). "
                    "Please try again in a quiet room, speaking clearly toward the microphone."),
    }


def _tts_cache_path(storage: Storage, story_id: str, language: str, voice: str, tts: TtsProvider) -> Path | None:
    root = getattr(storage, "root", None)
    if root is None:
        return None
    ext = "mp3" if tts.audio_format == "mp3" else tts.audio_format
    safe_provider = "".join(c if c.isalnum() or c in "-_" else "_" for c in tts.name)
    d = Path(root) / "_tts_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{story_id}_{STORY_BANK_VERSION}_{language}_{voice}_{safe_provider}.{ext}"


def media_type_for(audio_format: str) -> str:
    return {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg", "aac": "audio/aac",
            "flac": "audio/flac"}.get(audio_format, "application/octet-stream")


def get_story_audio(session: StorySession, step: str, tts: TtsProvider, storage: Storage | None = None) -> tuple[bytes, str]:
    """Story audio for a listen step (§1.1: TTS, fixed voice per language, no text shown).

    Cached on disk per story id + bank version + language + voice + provider, so TTS is
    called once per story across all users and sessions.
    """
    if step not in LISTEN_STEPS:
        raise TaskError(400, f"audio is only available for listen steps {LISTEN_STEPS}")
    story = session.story_for_step(step)
    if story is None:
        raise TaskError(404, "no story for step")
    voice = DEFAULT_VOICE.get(session.language, "default")
    storage = storage or get_storage()
    path = _tts_cache_path(storage, story.id, session.language, voice, tts)
    mt = media_type_for(tts.audio_format)
    if path is not None and path.exists():
        return path.read_bytes(), mt
    data = tts.synthesize(story.text, session.language, voice=voice)
    if path is not None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    return data, mt


def _load_clip(storage: Storage, url: str | None) -> tuple[Any, int] | None:
    if not url:
        return None
    try:
        return load_audio(storage.open(url))
    except Exception as exc:  # noqa: BLE001 - undecodable audio → no audio quality, transcript still scored
        log.info("story audio not decodable for quality checks", extra={"error": str(exc)})
        return None


def _finalize_story(db: Session, user: User, task: AssessmentTask, session: StorySession,
                    embeddings: EmbeddingProvider, storage: Storage) -> None:
    """Score the finished battery and persist §10 items 1 and 3 (no model score)."""
    urls = (task.raw_json or {}).get("audio_urls") or {}
    audio = [_load_clip(storage, urls.get(step)) for step in RECALL_STEPS]
    result = finalize(session, None, embeddings, audio=audio)
    d = result.to_dict()
    task.score = result.semantic_recall_mean
    task.valid = bool(result.valid)
    task.invalid_reason = ", ".join(result.invalid_reasons)[:200] or None
    task.features = {k: d[k] for k in ("semantic_recall_mean", "per_recall", "content_unit_fraction_mean", "lexical",
                                       "temporal", "story_ids", "invalid_reasons", "score_name",
                                       "embedding_provider", "status")}
    task.features["recalls"] = [
        {k: r.get(k) for k in ("label", "story_id", "valid", "invalid_reasons", "semantic_recall",
                               "content_unit_fraction", "content_units", "asr_confidence")}
        for r in d.get("recalls", [])
    ]
    task.recording_quality = {r["label"]: {"audio": r.get("recording_quality"), "asr_confidence": r.get("asr_confidence")}
                              for r in d.get("recalls", [])}
    if (task.raw_json or {}).get("device"):
        task.recording_quality["device"] = task.raw_json["device"]
    task.model_scores = {}
    task.language = result.language
    task.protocol_version = result.protocol_version or PROTOCOL_VERSION
    audit(db, user.id, "task.story.finalize", f"assessment_task:{task.id}",
          {"valid": task.valid, "score": task.score, "invalid_reasons": result.invalid_reasons})


def submit_step(
    db: Session,
    user: User,
    task_id: str,
    step: str,
    audio_bytes: bytes | None,
    transcript_text: str | None,
    stt: SttProvider,
    *,
    embeddings: EmbeddingProvider,
    storage: Storage | None = None,
    force_delay: bool = False,
    played_s: float | None = None,
    filename: str | None = None,
    device: str | None = None,
) -> tuple[AssessmentTask, StorySession]:
    """Complete ``step`` of a battery and advance (§1.1). Commits.

    Recall and fluency steps: ``audio_bytes`` is transcribed with the language forced to the
    session language and stored; ``transcript_text`` is used only when no audio is sent
    (client-side ASR / demo). A transcription failure records no transcript, which
    ``finalize`` turns into ``no_transcript`` → ``invalid_measurement``.

    Raises :class:`TaskError` 409 for out-of-order steps or when the delay has not elapsed
    (``extra['delay_remaining_s']``).
    """
    storage = storage or get_storage()
    task, session = get_story_task(db, user, task_id)
    if session.finished:
        raise TaskError(409, "session already finished")
    if step != session.current_step:
        raise TaskError(409, f"expected step {session.current_step!r}, got {step!r}",
                        current_step=session.current_step)
    payload: dict[str, Any] = {}
    if played_s is not None:
        payload["played_s"] = played_s
    if step == "delay":
        payload["force"] = bool(force_delay)
        if not force_delay:
            remaining = delay_remaining_s(session) or 0.0
            if remaining > 0:
                raise TaskError(409, f"delay not elapsed; {remaining:.0f}s remaining", delay_remaining_s=remaining)
    if step in TRANSCRIBED_STEPS:
        transcript: Transcript | None = None
        if audio_bytes:
            url = storage.save(user.id, "task_audio", filename or f"{step}.webm", audio_bytes)
            raw = dict(task.raw_json or {})
            raw["audio_urls"] = {**(raw.get("audio_urls") or {}), step: url}
            if device and not raw.get("device"):
                raw["device"] = device
            task.raw_json = raw
            try:
                transcript = stt.transcribe(audio_bytes, session.language, filename=filename)
            except Exception as exc:  # noqa: BLE001
                log.warning("story step transcription failed", extra={"task_id": task.id, "error": str(exc)})
        elif transcript_text is not None:
            transcript = Transcript(text=transcript_text, language=session.language, provider="client")
        if transcript is not None:
            payload["transcript"] = transcript
    try:
        next_step(session, step, payload)
    except DelayNotElapsedError as e:
        raise TaskError(409, str(e), delay_remaining_s=delay_remaining_s(session) or 0.0) from e
    except StepOrderError as e:
        raise TaskError(409, str(e), current_step=session.current_step) from e
    _save_session(task, session)
    if session.finished:
        _finalize_story(db, user, task, session, embeddings, storage)
    db.commit()
    return task, session


# ================================================================ §5.4 sustained vowel

_MODEL_LOCK = threading.Lock()
_VOWEL_MODEL: Any = None
_VOWEL_MODEL_ERROR: str | None = None


def get_vowel_model() -> Any:
    """Module-level lazy singleton of :class:`PdVowelModel` (None if artefacts are missing)."""
    global _VOWEL_MODEL, _VOWEL_MODEL_ERROR
    if _VOWEL_MODEL is None and _VOWEL_MODEL_ERROR is None:
        with _MODEL_LOCK:
            if _VOWEL_MODEL is None and _VOWEL_MODEL_ERROR is None:
                try:
                    from app.ml.pd_vowel_model import PdVowelModel

                    _VOWEL_MODEL = PdVowelModel.load_default()
                except Exception as exc:  # noqa: BLE001
                    _VOWEL_MODEL_ERROR = str(exc)
                    log.error("pd vowel model failed to load", extra={"error": str(exc)})
    return _VOWEL_MODEL


def _finite_or_none(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def submit_vowel(db: Session, user: User, audio_bytes: bytes, filename: str | None, repetition_index: int,
                 storage: Storage | None = None, model: Any = None, device: str | None = None) -> dict[str, Any]:
    """Store + quality-gate + score one "ahh" repetition (§5.4). Commits.

    ``valid`` = recording-quality gate (≥3 s voiced, SNR ≥15 dB, no clipping). The model
    score is stored in ``model_scores['pd_vowel']`` only for valid recordings; PD-03 decides
    what it means (voice alone never raises the level). ``device`` (:func:`device_label`) is
    stored in ``recording_quality`` for the PD-03B same-device voice baseline (§5.5).
    """
    from app.ml.features.quality import assess_quality
    from app.ml.features.vowel import extract_vowel_features

    storage = storage or get_storage()
    lang = user_language(user)
    try:
        y, sr = load_audio(audio_bytes)
    except Exception as e:  # noqa: BLE001
        raise TaskError(422, "could not decode the audio file; please record again") from e
    url = storage.save(user.id, "task_audio", filename or "vowel.wav", audio_bytes)
    quality = assess_quality(y, sr)
    features: dict[str, Any] | None = None
    model_scores: dict[str, Any] = {}
    score: float | None = None
    model = model if model is not None else get_vowel_model()
    if quality.ok:
        features = {k: _finite_or_none(v) for k, v in extract_vowel_features(y, sr).items()}
        if model is not None:
            pred = model.predict_from_features(features)
            model_scores[PD_VOWEL_KEY] = {
                "name": pred.name, "probability": pred.probability, "threshold": pred.threshold,
                "version": pred.model_version, "above_threshold": bool(pred.above_threshold),
                "calibration_note": pred.calibration_note, "feature_vector_ok": pred.feature_vector_ok,
                "missing_features": pred.missing_features,
            }
            score = pred.probability
    reasons = list(quality.reasons)
    task = AssessmentTask(
        user_id=user.id, task_type="sustained_vowel", protocol_version=VOWEL_PROTOCOL_VERSION, language=lang,
        recording_quality={**quality.to_dict(), "device": device}, raw_url=url,
        raw_json={"repetition_index": int(repetition_index), "filename": filename, "sample_rate": int(sr)},
        features=features, model_scores=model_scores, score=score, valid=bool(quality.ok),
        invalid_reason=", ".join(reasons)[:200] or None,
    )
    db.add(task)
    db.flush()
    audit(db, user.id, "task.vowel.submit", f"assessment_task:{task.id}",
          {"valid": task.valid, "reasons": reasons, "repetition_index": int(repetition_index)})
    db.commit()
    exp = None
    if PD_VOWEL_KEY in model_scores:
        m = model_scores[PD_VOWEL_KEY]
        exp = {"probability": m["probability"], "threshold": m["threshold"], "above_threshold": m["above_threshold"],
               "version": m["version"], "label": VOWEL_DETAIL_LABEL, "detail_only": True,
               "calibration_note": m["calibration_note"]}
    if quality.ok:
        msg = ("Recording received — thank you." if lang == "en" else "録音を受け付けました。ありがとうございます。")
    else:
        msg = ("We couldn't use this recording. Please record again following the tips below." if lang == "en"
               else "この録音は解析に使えませんでした。下のヒントを参考にもう一度録音してください。")
    return {
        "task_id": task.id, "repetition_index": int(repetition_index), "valid": bool(quality.ok),
        "status": "ok" if quality.ok else "invalid_measurement", "recording_quality": dict(task.recording_quality),
        "reasons": reasons, "message": msg, "tips": [] if quality.ok else VOWEL_TIPS[lang],
        "experimental_score": exp,
    }


# ================================================================ §5.3 finger tapping

def build_tapping_task(user: User, left_events: list[dict[str, Any]], right_events: list[dict[str, Any]],
                       duration_s: float = 30, keys: Sequence[str] = ("s", ";"),
                       device: str = TAPPING_DEVICE) -> AssessmentTask:
    """Unsaved ``AssessmentTask(finger_tap)`` for a keyboard BRAIN tap-test session (§5.3).

    ``features`` = per-hand features + asymmetry, plus flat ``TAPPING_FLAT_KEYS`` (mean of both hands
    when valid) read by the PD-04 baseline series. ``device`` (= input method, "keyboard") is stored in
    ``recording_quality`` and ``raw_json`` like the voice tasks so the baseline is per input method.
    """
    feats = tapping_session_features(left_events, right_events, duration_s, keys)
    if feats["valid"]:
        for k in TAPPING_FLAT_KEYS:
            vals = [feats[h][k] for h in ("left", "right") if isinstance(feats[h].get(k), (int, float))]
            feats[k] = float(sum(vals) / len(vals)) if vals else None
    quality = {"device": device, "input_method": "keyboard", "duration_s": float(duration_s)}
    for h in ("left", "right"):
        quality[h] = {k: feats[h][k] for k in ("n_events", "tap_count", "error_presses", "n_autorepeat",
                                               "n_freezes", "n_outliers", "n_unreleased", "valid", "reason")}
    return AssessmentTask(
        user_id=user.id, task_type="finger_tap", protocol_version=TAPPING_PROTOCOL_VERSION,
        language=user_language(user),
        raw_json={"input_method": "keyboard", "device": device, "duration_s": float(duration_s), "keys": list(keys),
                  "left": {"events": [dict(e) for e in left_events]},
                  "right": {"events": [dict(e) for e in right_events]}},
        recording_quality=quality, features=feats, model_scores={}, score=None, valid=bool(feats["valid"]),
        invalid_reason=(feats.get("reason") or None) if not feats["valid"] else None,
    )


def submit_tapping(db: Session, user: User, left_events: list[dict[str, Any]], right_events: list[dict[str, Any]],
                   duration_s: float = 30, keys: Sequence[str] = ("s", ";")) -> AssessmentTask:
    """Store a both-hand keyboard tapping session (§5.3; see :func:`build_tapping_task`). Commits."""
    task = build_tapping_task(user, left_events, right_events, duration_s, keys)
    db.add(task)
    db.flush()
    audit(db, user.id, "task.tapping.submit", f"assessment_task:{task.id}",
          {"valid": task.valid, "reason": task.invalid_reason, "device": TAPPING_DEVICE})
    db.commit()
    return task


_TAPPING_MESSAGES = {
    "key_autorepeat_detected": "A key was held down and the keyboard auto-repeated it. Please press and release "
                               "each key with a single tap of your index finger and try again.",
    "fewer_than_10_taps": "Not enough key presses were recorded. Please alternate the S and ; keys with your "
                          "index finger as fast as you can for the full 30 seconds with each hand.",
    "duration_below_20_s": "The trial was too short. Please tap for the full 30 seconds with each hand.",
    "too_many_outlier_intervals": "The tapping rhythm could not be measured. Please tap steadily, alternating "
                                  "the S and ; keys, for the full 30 seconds with each hand.",
}


def tapping_message(task: AssessmentTask) -> str:
    if task.valid:
        return "Tapping recorded — thank you."
    for reason, msg in _TAPPING_MESSAGES.items():
        if reason in (task.invalid_reason or ""):
            return msg
    return _TAPPING_MESSAGES["fewer_than_10_taps"]


# ================================================================ §1.4 word recall

def start_word_recall(db: Session, user: User, language: str | None = None) -> AssessmentTask:
    """Present 3 words (§1.4); the response is submitted later. Commits."""
    lang = user_language(user, language)
    prior = db.scalars(select(AssessmentTask.raw_json).where(
        AssessmentTask.user_id == user.id, AssessmentTask.task_type == "word_recall"))
    exclude = {r.get("set_id") for r in prior if isinstance(r, dict) and r.get("set_id")}
    trial = new_word_recall(lang, exclude_ids=exclude)
    task = AssessmentTask(
        user_id=user.id, task_type="word_recall", protocol_version=WORD_RECALL_PROTOCOL_VERSION, language=lang,
        raw_json={"set_id": trial.set_id, "words": trial.words, "presented_at": trial.presented_at,
                  "instruction": trial.instruction, "recall_prompt": _WR_RECALL_PROMPT[lang]},
        model_scores={}, valid=False, invalid_reason=IN_PROGRESS,
    )
    db.add(task)
    db.flush()
    audit(db, user.id, "task.word_recall.start", f"assessment_task:{task.id}", {"set_id": trial.set_id})
    db.commit()
    return task


def submit_word_recall(db: Session, user: User, task_id: str, response_text: str,
                       latency_ms: float | None) -> AssessmentTask:
    """Score the response 0-3 + latency (§1.4). Commits. 409 if already scored."""
    task = _get_task(db, user, task_id, "word_recall")
    raw = dict(task.raw_json or {})
    if "response" in raw:
        raise TaskError(409, "word recall already scored")
    res = score_word_recall(raw.get("words") or [], response_text, latency_ms, task.language or "en")
    raw["response"] = {"text": response_text, "latency_ms": latency_ms, "responded_at": _now().isoformat()}
    task.raw_json = raw
    task.features = res
    task.score = float(res["score"])
    task.valid = bool(res["valid"])
    task.invalid_reason = None if task.valid else "empty_response"
    audit(db, user.id, "task.word_recall.submit", f"assessment_task:{task.id}", {"score": res["score"]})
    db.commit()
    return task


# ================================================================ history

def _history_model_scores(task: AssessmentTask) -> dict[str, Any]:
    ms = dict(task.model_scores or {})
    if PD_VOWEL_KEY in ms and isinstance(ms[PD_VOWEL_KEY], dict):
        ms[PD_VOWEL_KEY] = {**ms[PD_VOWEL_KEY], "detail_only": True, "label": VOWEL_DETAIL_LABEL}
    return ms


def list_tasks(db: Session, user: User, task_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Newest-first task history. The vowel model score is flagged detail-only (§11.2)."""
    if task_type is not None and task_type not in TASK_TYPES:
        raise TaskError(422, f"unknown task_type {task_type!r}")
    q = select(AssessmentTask).where(AssessmentTask.user_id == user.id)
    if task_type:
        q = q.where(AssessmentTask.task_type == task_type)
    q = q.order_by(AssessmentTask.created_at.desc()).limit(max(1, min(int(limit), 200)))
    return [
        {
            "id": t.id, "task_type": t.task_type, "created_at": t.created_at,
            "status": IN_PROGRESS if t.invalid_reason == IN_PROGRESS else "complete",
            "score": t.score, "valid": bool(t.valid), "invalid_reason": t.invalid_reason,
            "language": t.language, "protocol_version": t.protocol_version,
            "model_scores": _history_model_scores(t),
        }
        for t in db.scalars(q)
    ]
