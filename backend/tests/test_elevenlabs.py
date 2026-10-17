"""ElevenLabs STT/TTS with httpx.MockTransport (no network)."""

import json

import httpx
import pytest

from app.core.config import settings
from app.providers.embeddings import ProviderConfigError
from app.providers.stt import ElevenLabsStt, get_stt_provider, group_segments
from app.providers.tts import ElevenLabsTts, get_tts_provider


def _client(handler, seen):
    def wrapped(request):
        seen.append(request)
        return handler(request)
    return httpx.Client(transport=httpx.MockTransport(wrapped))


STT_BODY = {
    "language_code": "eng", "language_probability": 0.99, "text": "Hello there. I walked home slowly",
    "words": [
        {"text": "Hello", "start": 0.0, "end": 0.4, "type": "word", "logprob": -0.1},
        {"text": " ", "start": 0.4, "end": 0.45, "type": "spacing"},
        {"text": "there.", "start": 0.45, "end": 0.9, "type": "word", "logprob": -0.1},
        {"text": " ", "start": 0.9, "end": 1.0, "type": "spacing"},
        {"text": "I", "start": 1.0, "end": 1.1, "type": "word", "logprob": -0.1},
        {"text": "walked", "start": 1.15, "end": 1.5, "type": "word", "logprob": -0.1},
        {"text": "(cough)", "start": 1.5, "end": 2.0, "type": "audio_event"},
        {"text": "home", "start": 3.0, "end": 3.3, "type": "word", "logprob": -0.1},
        {"text": "slowly", "start": 3.35, "end": 3.9, "type": "word", "logprob": -0.1},
        {"text": " ", "start": 3.9, "end": 4.2, "type": "spacing"},
    ],
}


def test_stt_request_and_word_mapping():
    seen = []
    stt = ElevenLabsStt(api_key="k", client=_client(lambda r: httpx.Response(200, json=STT_BODY), seen))
    t = stt.transcribe(b"audio-bytes", "en-CA", filename="rec.webm")
    req = seen[0]
    assert req.method == "POST" and str(req.url) == "https://api.elevenlabs.io/v1/speech-to-text"
    assert req.headers["xi-api-key"] == "k"
    body = req.content.decode("latin-1")
    for field, value in (("model_id", settings.elevenlabs_stt_model), ("language_code", "en"),
                         ("timestamps_granularity", "word")):
        assert f'name="{field}"\r\n\r\n{value}' in body
    assert 'filename="rec.webm"' in body and "audio-bytes" in body
    assert t.word_timestamps == [
        {"word": "Hello", "start": 0.0, "end": 0.4}, {"word": "there.", "start": 0.45, "end": 0.9},
        {"word": "I", "start": 1.0, "end": 1.1}, {"word": "walked", "start": 1.15, "end": 1.5},
        {"word": "home", "start": 3.0, "end": 3.3}, {"word": "slowly", "start": 3.35, "end": 3.9}]
    # split after "there." (punctuation) and before "home" (1.5 s pause)
    assert t.segments == [{"start": 0.0, "end": 0.9, "text": "Hello there."},
                          {"start": 1.0, "end": 1.5, "text": "I walked"},
                          {"start": 3.0, "end": 3.9, "text": "home slowly"}]
    assert t.text == STT_BODY["text"] and t.language == "en"
    assert t.duration_s == pytest.approx(4.2)
    assert t.confidence == pytest.approx(0.904837, rel=1e-4)
    assert t.provider == f"elevenlabs:{settings.elevenlabs_stt_model}"


def test_group_segments_edge_cases():
    assert group_segments([]) == []
    words = [{"word": "a", "start": 0.0, "end": 0.1}, {"word": "b", "start": 0.85, "end": 1.0}]
    assert len(group_segments(words)) == 1          # 0.75 s gap ≤ 0.8 s
    words[1]["start"] = 0.95
    assert len(group_segments(words)) == 2          # 0.85 s gap


def test_stt_http_error():
    stt = ElevenLabsStt(api_key="k", client=_client(lambda r: httpx.Response(401, json={"detail": "bad"}), []))
    with pytest.raises(RuntimeError, match="401"):
        stt.transcribe(b"x", "en")


def test_tts_request_shape(monkeypatch):
    monkeypatch.delenv("TTS_SPEED_EN", raising=False)
    seen = []
    tts = ElevenLabsTts(api_key="k", voice_id="voice123",
                        client=_client(lambda r: httpx.Response(200, content=b"ID3mp3"), seen))
    assert tts.audio_format == "mp3" and tts.name.startswith("elevenlabs:")
    assert tts.synthesize("Once upon a time.", "en", voice="alloy") == b"ID3mp3"  # OpenAI voice name ignored
    req = seen[0]
    assert req.url.path == "/v1/text-to-speech/voice123"
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == "k"
    body = json.loads(req.content)
    assert body["text"] == "Once upon a time." and body["model_id"] == settings.elevenlabs_tts_model
    assert body["voice_settings"]["speed"] == pytest.approx(0.88)
    tts.synthesize("x", "en", voice="CustomVoiceId99")
    assert seen[1].url.path == "/v1/text-to-speech/CustomVoiceId99"


def test_tts_speed_clamped(monkeypatch):
    tts = ElevenLabsTts(api_key="k", client=_client(lambda r: httpx.Response(200, content=b""), []))
    monkeypatch.setenv("TTS_SPEED_EN", "0.5")
    assert tts.speed_for("en") == 0.7
    monkeypatch.setenv("TTS_SPEED_EN", "1.6")
    assert tts.speed_for("en") == 1.2
    monkeypatch.setenv("TTS_SPEED_EN", "1.05")
    assert tts.speed_for("en-CA") == pytest.approx(1.05)


def test_tts_http_error():
    tts = ElevenLabsTts(api_key="k", client=_client(lambda r: httpx.Response(402, text="pay"), []))
    with pytest.raises(RuntimeError, match="402"):
        tts.synthesize("x", "en")


def test_missing_key_errors(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(ProviderConfigError):
        ElevenLabsStt()
    with pytest.raises(ProviderConfigError):
        ElevenLabsTts()
    with pytest.raises(ProviderConfigError):
        get_stt_provider("elevenlabs")


def test_factories_select_elevenlabs(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    assert isinstance(get_stt_provider("elevenlabs"), ElevenLabsStt)
    assert isinstance(get_tts_provider("elevenlabs"), ElevenLabsTts)
    monkeypatch.setenv("STT_PROVIDER", "elevenlabs")
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    assert isinstance(get_stt_provider(), ElevenLabsStt)
    assert isinstance(get_tts_provider(), ElevenLabsTts)
