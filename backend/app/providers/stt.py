"""Speech-to-text providers (spec §1.1 "Transcription").

The spec calls for Whisper with the language forced to the user's language,
storing transcript + ASR confidence. JA WER is not evaluated — a stated
limitation.

Selection: ``STT_PROVIDER`` = ``fake`` (default) | ``openai`` | ``elevenlabs``.
``ElevenLabsStt`` posts to ElevenLabs Scribe (``ELEVENLABS_API_KEY``,
``settings.elevenlabs_stt_model``, default ``scribe_v1``) with word timestamps; segments are
rebuilt from the words (split on pauses > 0.8 s or sentence-final punctuation).
``OpenAIWhisperStt`` needs ``OPENAI_API_KEY``; ``STT_MODEL`` (default
``whisper-1``). ``whisper-1`` is the default because only it returns
``verbose_json`` with segment and word timestamps, which the temporal
features need; ``gpt-4o-transcribe`` returns text only (no timestamps), so if
selected the temporal features fall back to audio VAD.
``FakeStt`` returns a transcript given to its constructor, or the text in
``FAKE_STT_TEXT``.
"""

from __future__ import annotations

import io
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.providers.embeddings import ProviderConfigError, _settings

__all__ = ["Transcript", "SttProvider", "OpenAIWhisperStt", "ElevenLabsStt", "FakeStt", "get_stt_provider",
           "group_segments"]

AudioInput = bytes | str | Path


@dataclass
class Transcript:
    """ASR result. ``segments``: ``[{start,end,text}]``; ``word_timestamps``: ``[{word,start,end}]`` or None."""

    text: str
    language: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    word_timestamps: list[dict[str, Any]] | None = None
    provider: str = "unknown"
    confidence: float | None = None
    duration_s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        keys = {"text", "language", "segments", "word_timestamps", "provider", "confidence", "duration_s"}
        return cls(**{k: v for k, v in d.items() if k in keys})


@runtime_checkable
class SttProvider(Protocol):
    name: str

    def transcribe(self, audio: AudioInput, language: str, filename: str | None = None) -> Transcript: ...


def _whisper_lang(language: str) -> str:
    return (language or "en").split("-")[0].lower()


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class OpenAIWhisperStt:
    """OpenAI transcription API with the language forced (§1.1).

    Confidence = exp(mean segment ``avg_logprob``) — a rough proxy in (0, 1],
    available only with ``verbose_json`` (``whisper-1``).
    """

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout_s: float = 120.0) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY", "") or _settings().openai_api_key
        if not key:
            raise ProviderConfigError("STT_PROVIDER=openai but OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise ProviderConfigError("openai package is not installed") from e
        self._client: Any = OpenAI(api_key=key, timeout=timeout_s, max_retries=2)
        self.model = model or os.environ.get("STT_MODEL", "whisper-1")
        self.name = f"openai:{self.model}"

    def transcribe(self, audio: AudioInput, language: str, filename: str | None = None) -> Transcript:
        if isinstance(audio, (str, Path)):
            p = Path(audio)
            data, fname = p.read_bytes(), filename or p.name
        else:
            data, fname = audio, filename or "recording.webm"
        buf = io.BytesIO(data)
        buf.name = fname
        lang = _whisper_lang(language)
        verbose = self.model.startswith("whisper")
        kwargs: dict[str, Any] = {"model": self.model, "file": buf, "language": lang}
        if verbose:
            kwargs.update(response_format="verbose_json", timestamp_granularities=["word", "segment"])
        res = self._client.audio.transcriptions.create(**kwargs)
        text = str(_get(res, "text", "") or "").strip()
        segments = [
            {"start": float(_get(s, "start", 0.0)), "end": float(_get(s, "end", 0.0)), "text": str(_get(s, "text", "")).strip()}
            for s in (_get(res, "segments") or [])
        ]
        words_raw = _get(res, "words")
        words = (
            [{"word": str(_get(w, "word", "")), "start": float(_get(w, "start", 0.0)), "end": float(_get(w, "end", 0.0))} for w in words_raw]
            if words_raw
            else None
        )
        lps = [float(_get(s, "avg_logprob")) for s in (_get(res, "segments") or []) if _get(s, "avg_logprob") is not None]
        conf = math.exp(sum(lps) / len(lps)) if lps else None
        dur = _get(res, "duration")
        return Transcript(
            text=text, language=lang, segments=segments, word_timestamps=words,
            provider=self.name, confidence=conf, duration_s=float(dur) if dur is not None else None,
        )


ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
SEGMENT_PAUSE_S = 0.8
_SENTENCE_END = (".", "?", "!", "。", "？", "！")
_AUDIO_MIME = {".webm": "audio/webm", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
               ".mp4": "audio/mp4", ".ogg": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac"}


def _join_words(words: list[dict[str, Any]]) -> str:
    out = ""
    for w in words:
        tok = str(w["word"]).strip()
        if not tok:
            continue
        no_space = not out or out.endswith(" ") or tok[0] in ",.?!;:%)。、？！" or _is_cjk(tok[0]) and _is_cjk(out[-1])
        out += tok if no_space else " " + tok
    return out.strip()


def _is_cjk(ch: str) -> bool:
    return "　" <= ch <= "鿿" or "＀" <= ch <= "￯"


def group_segments(words: list[dict[str, Any]], pause_s: float = SEGMENT_PAUSE_S) -> list[dict[str, Any]]:
    """Group ``[{word,start,end}]`` into ``[{start,end,text}]`` segments: a new segment starts
    after a gap > ``pause_s`` or after a word ending in sentence punctuation."""
    segments: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > pause_s or str(cur[-1]["word"]).rstrip().endswith(_SENTENCE_END)):
            segments.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": _join_words(cur)})
            cur = []
        cur.append(w)
    if cur:
        segments.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": _join_words(cur)})
    return segments


class ElevenLabsStt:
    """ElevenLabs Scribe speech-to-text with the language forced and word timestamps.

    Confidence = exp(mean word ``logprob``) when the API returns logprobs, else None.
    ``duration_s`` = end of the last token (the API does not return the clip length).
    """

    def __init__(self, model: str | None = None, api_key: str | None = None, timeout_s: float = 120.0,
                 client: Any = None) -> None:
        key = api_key or os.environ.get("ELEVENLABS_API_KEY", "") or _settings().elevenlabs_api_key
        if not key:
            raise ProviderConfigError("STT_PROVIDER=elevenlabs but ELEVENLABS_API_KEY is not set")
        import httpx

        self._key = key
        self._client: Any = client or httpx.Client(timeout=timeout_s)
        self.model = model or _settings().elevenlabs_stt_model or "scribe_v1"
        self.name = f"elevenlabs:{self.model}"

    def transcribe(self, audio: AudioInput, language: str, filename: str | None = None) -> Transcript:
        if isinstance(audio, (str, Path)):
            p = Path(audio)
            data, fname = p.read_bytes(), filename or p.name
        else:
            data, fname = audio, filename or "recording.webm"
        lang = _whisper_lang(language)
        mime = _AUDIO_MIME.get(Path(fname).suffix.lower(), "application/octet-stream")
        resp = self._client.post(
            ELEVENLABS_STT_URL,
            headers={"xi-api-key": self._key},
            data={"model_id": self.model, "language_code": lang, "timestamps_granularity": "word",
                  "tag_audio_events": "false"},
            files={"file": (fname, data, mime)},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"ElevenLabs STT failed: HTTP {resp.status_code}: {resp.text[:300]}")
        return self.parse(resp.json(), lang)

    def parse(self, body: dict[str, Any], lang: str) -> Transcript:
        raw = body.get("words") or []
        words = [{"word": str(w.get("text", "")).strip(), "start": float(w.get("start") or 0.0),
                  "end": float(w.get("end") or 0.0)}
                 for w in raw if w.get("type", "word") == "word" and str(w.get("text", "")).strip()]
        lps = [float(w["logprob"]) for w in raw if w.get("type", "word") == "word" and w.get("logprob") is not None]
        ends = [float(w.get("end") or 0.0) for w in raw]
        text = str(body.get("text") or "").strip() or _join_words(words)
        return Transcript(
            text=text, language=lang,
            segments=group_segments(words), word_timestamps=words, provider=self.name,
            confidence=math.exp(sum(lps) / len(lps)) if lps else None,
            duration_s=max(ends) if ends else 0.0,
        )


class FakeStt:
    """Returns an injected transcript (constructor ``transcript``/``text``, or ``FAKE_STT_TEXT`` env)."""

    def __init__(self, transcript: Transcript | None = None, text: str | None = None) -> None:
        self._transcript = transcript
        self._text = text
        self.name = "fake-stt"

    def transcribe(self, audio: AudioInput, language: str, filename: str | None = None) -> Transcript:
        if self._transcript is not None:
            return self._transcript
        text = self._text if self._text is not None else os.environ.get("FAKE_STT_TEXT", "")
        lang = _whisper_lang(language)
        segs = [{"start": 0.0, "end": 10.0, "text": text}] if text else []
        return Transcript(text=text, language=lang, segments=segs, word_timestamps=None,
                          provider=self.name, confidence=None, duration_s=10.0 if text else 0.0)


def get_stt_provider(name: str | None = None) -> SttProvider:
    """Factory: ``name`` or ``STT_PROVIDER`` env (``fake`` default, ``openai``, ``elevenlabs``)."""
    choice = (name or os.environ.get("STT_PROVIDER") or _settings().stt_provider or "fake").strip().lower()
    if choice == "fake":
        return FakeStt()
    if choice in {"openai", "whisper"}:
        return OpenAIWhisperStt()
    if choice in {"elevenlabs", "scribe"}:
        return ElevenLabsStt()
    raise ProviderConfigError(f"unknown STT_PROVIDER={choice!r} (expected 'fake', 'openai' or 'elevenlabs')")
