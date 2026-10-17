"""Text-to-speech providers for story and word-list playback (spec §1.1 "Story material", §1.4).

The protocol reads stories at ≈140 wpm with a fixed voice per language.

Selection: ``TTS_PROVIDER`` = ``fake`` (default) | ``openai`` | ``elevenlabs``.
``openai`` needs ``OPENAI_API_KEY``; ``TTS_MODEL`` (default ``tts-1``). ``elevenlabs`` needs
``ELEVENLABS_API_KEY`` and uses ``settings.elevenlabs_voice_id`` / ``elevenlabs_tts_model``
(mp3 44.1 kHz 128 kbps). ElevenLabs ``voice_settings.speed`` is the same kind of multiplier
(range 0.7-1.2), so the same ``TTS_SPEED_EN``/``TTS_SPEED_JA`` calibration applies, clamped to
that range; calibrate it separately for the ElevenLabs voice with :func:`calibrate_speed`.

Pace tuning
-----------
OpenAI TTS has no words-per-minute setting, only ``speed`` (0.25-4.0, a
multiplier on the voice's natural rate). Our measurements are not yet
recorded, so ``DEFAULT_SPEED`` holds starting values: the stock voices
read English at roughly 155-165 wpm at speed 1.0, so EN starts at 0.88
(≈140 wpm). For Japanese the target is the same duration (≈45-55 s for a
270-330-character story, i.e. ≈330-360 characters/min); the voices read
Japanese a little fast, so JA starts at 0.92. To calibrate, synthesise one story at
speed 1.0, measure duration, and set ``TTS_SPEED_EN``/``TTS_SPEED_JA`` to
:func:`calibrate_speed` of the measured rate. Keep the speed fixed once
sessions are collected (it is part of the protocol version).
"""

from __future__ import annotations

import io
import os
import wave
from typing import Any, Protocol, runtime_checkable

from app.providers.embeddings import ProviderConfigError, _settings

__all__ = ["TtsProvider", "OpenAITts", "ElevenLabsTts", "FakeTts", "get_tts_provider", "calibrate_speed", "TARGET_WPM"]

TARGET_WPM = 140.0
TARGET_JA_CPM = 345.0  # characters/min giving the EN-equivalent duration for our JA stories
DEFAULT_VOICE = {"en": "alloy", "ja": "nova"}
DEFAULT_SPEED = {"en": 0.88, "ja": 0.92}


def calibrate_speed(measured_rate_at_speed_1: float, target_rate: float = TARGET_WPM) -> float:
    """Speed multiplier that moves a measured rate (at speed 1.0) to the target; clamped to the API range."""
    if measured_rate_at_speed_1 <= 0:
        raise ValueError("measured rate must be positive")
    return max(0.25, min(4.0, target_rate / measured_rate_at_speed_1))


def _lang(language: str) -> str:
    return "ja" if (language or "").lower().startswith("ja") else "en"


@runtime_checkable
class TtsProvider(Protocol):
    name: str
    audio_format: str

    def synthesize(self, text: str, language: str, voice: str | None = None) -> bytes: ...


class OpenAITts:
    """OpenAI ``tts-1`` with a fixed voice and speed per language (see module docstring)."""

    def __init__(self, model: str | None = None, api_key: str | None = None, audio_format: str = "mp3") -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY", "") or _settings().openai_api_key
        if not key:
            raise ProviderConfigError("TTS_PROVIDER=openai but OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise ProviderConfigError("openai package is not installed") from e
        self._client: Any = OpenAI(api_key=key, max_retries=2)
        self.model = model or os.environ.get("TTS_MODEL", "tts-1")
        self.audio_format = audio_format
        self.name = f"openai:{self.model}"

    def speed_for(self, language: str) -> float:
        lang = _lang(language)
        return float(os.environ.get(f"TTS_SPEED_{lang.upper()}", DEFAULT_SPEED[lang]))

    def synthesize(self, text: str, language: str, voice: str | None = None) -> bytes:
        lang = _lang(language)
        res = self._client.audio.speech.create(
            model=self.model,
            voice=voice or DEFAULT_VOICE[lang],
            input=text,
            speed=self.speed_for(lang),
            response_format=self.audio_format,
        )
        return res.read() if hasattr(res, "read") else bytes(res.content)


ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_SPEED_RANGE = (0.7, 1.2)
_OPENAI_VOICES = {"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer",
                  "verse", "default"}


class ElevenLabsTts:
    """ElevenLabs text-to-speech with one fixed voice (``settings.elevenlabs_voice_id``).

    ``voice`` may be an ElevenLabs voice id; OpenAI voice names (which the story cache passes)
    are ignored so the configured voice stays fixed.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None, voice_id: str | None = None,
                 timeout_s: float = 120.0, client: Any = None) -> None:
        key = api_key or os.environ.get("ELEVENLABS_API_KEY", "") or _settings().elevenlabs_api_key
        if not key:
            raise ProviderConfigError("TTS_PROVIDER=elevenlabs but ELEVENLABS_API_KEY is not set")
        import httpx

        self._key = key
        self._client: Any = client or httpx.Client(timeout=timeout_s)
        self.model = model or _settings().elevenlabs_tts_model or "eleven_multilingual_v2"
        self.voice_id = voice_id or _settings().elevenlabs_voice_id
        self.output_format = "mp3_44100_128"
        self.audio_format = "mp3"
        self.name = f"elevenlabs:{self.model}:{self.voice_id}"

    def speed_for(self, language: str) -> float:
        lang = _lang(language)
        raw = float(os.environ.get(f"TTS_SPEED_{lang.upper()}", DEFAULT_SPEED[lang]))
        lo, hi = ELEVENLABS_SPEED_RANGE
        return max(lo, min(hi, raw))

    def synthesize(self, text: str, language: str, voice: str | None = None) -> bytes:
        voice_id = voice if voice and voice.lower() not in _OPENAI_VOICES else self.voice_id
        body: dict[str, Any] = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": self.speed_for(language)},
        }
        if self.model in ("eleven_turbo_v2_5", "eleven_flash_v2_5"):  # only these accept language_code
            body["language_code"] = _lang(language)
        resp = self._client.post(
            ELEVENLABS_TTS_URL.format(voice_id=voice_id),
            params={"output_format": self.output_format},
            headers={"xi-api-key": self._key, "Accept": "audio/mpeg"},
            json=body,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"ElevenLabs TTS failed: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.content


class FakeTts:
    """Returns a short silent 16 kHz mono WAV (duration ``seconds``)."""

    def __init__(self, seconds: float = 0.5, sr: int = 16000) -> None:
        self.seconds = seconds
        self.sr = sr
        self.audio_format = "wav"
        self.name = "fake-tts"

    def synthesize(self, text: str, language: str, voice: str | None = None) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            w.writeframes(b"\x00\x00" * int(self.seconds * self.sr))
        return buf.getvalue()


def get_tts_provider(name: str | None = None) -> TtsProvider:
    """Factory: ``name`` or ``TTS_PROVIDER`` env (``fake`` default, ``openai``, ``elevenlabs``)."""
    choice = (name or os.environ.get("TTS_PROVIDER") or _settings().tts_provider or "fake").strip().lower()
    if choice == "fake":
        return FakeTts()
    if choice == "openai":
        return OpenAITts()
    if choice == "elevenlabs":
        return ElevenLabsTts()
    raise ProviderConfigError(f"unknown TTS_PROVIDER={choice!r} (expected 'fake', 'openai' or 'elevenlabs')")
