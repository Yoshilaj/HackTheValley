"""Provider factories: fakes by default; real providers fail clearly without keys."""

import io
import wave

import pytest

from app.providers import (
    FakeEmbeddings, FakeStt, FakeTts, ProviderConfigError, Transcript, cosine,
    get_embedding_provider, get_stt_provider, get_tts_provider,
)
from app.providers.tts import calibrate_speed


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("EMBEDDING_PROVIDER", "STT_PROVIDER", "TTS_PROVIDER", "VOYAGE_API_KEY", "OPENAI_API_KEY", "FAKE_STT_TEXT"):
        monkeypatch.delenv(k, raising=False)


def test_defaults_are_fakes():
    assert isinstance(get_embedding_provider(), FakeEmbeddings)
    assert isinstance(get_stt_provider(), FakeStt)
    assert isinstance(get_tts_provider(), FakeTts)


@pytest.mark.parametrize("var,val,factory,key", [
    ("EMBEDDING_PROVIDER", "voyage", get_embedding_provider, "VOYAGE_API_KEY"),
    ("STT_PROVIDER", "openai", get_stt_provider, "OPENAI_API_KEY"),
    ("TTS_PROVIDER", "openai", get_tts_provider, "OPENAI_API_KEY"),
])
def test_real_provider_without_key_raises(monkeypatch, var, val, factory, key):
    monkeypatch.setenv(var, val)
    with pytest.raises(ProviderConfigError, match=key):
        factory()


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bogus")
    with pytest.raises(ProviderConfigError):
        get_embedding_provider()


def test_real_providers_construct_with_key(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    assert get_embedding_provider("voyage").name == "voyage:voyage-3"
    assert get_stt_provider("openai").name == "openai:whisper-1"
    assert get_tts_provider("openai").name == "openai:tts-1"


def test_fake_embeddings_deterministic_normalised():
    p = FakeEmbeddings(dim=256)
    a1, a2 = p.embed(["The cat sat on the mat."]), p.embed(["The cat sat on the mat."])
    assert a1 == a2 and len(a1[0]) == 256
    assert abs(sum(v * v for v in a1[0]) - 1.0) < 1e-9
    assert p.embed([""])[0] == [0.0] * 256


def test_fake_embeddings_paraphrase_beats_unrelated():
    p = FakeEmbeddings()
    base, para, unrel = p.embed([
        "Helen bought six ripe tomatoes and honey at the market.",
        "At the market Helen bought tomatoes and some honey.",
        "My brother plays golf every Sunday morning.",
    ])
    assert cosine(base, para) > cosine(base, unrel) + 0.3
    jb, jp, ju = p.embed(["花子さんは朝市でトマトとはちみつを買いました。", "朝市で花子さんがはちみつとトマトを買った。", "兄は毎週日曜日にゴルフをします。"])
    assert cosine(jb, jp) > cosine(jb, ju) + 0.3


def test_fake_stt_injected_and_env(monkeypatch):
    t = Transcript(text="hello", language="en", provider="x")
    assert FakeStt(transcript=t).transcribe(b"", "en") is t
    monkeypatch.setenv("FAKE_STT_TEXT", "こんにちは")
    r = FakeStt().transcribe(b"", "ja-JP")
    assert r.text == "こんにちは" and r.language == "ja"
    assert Transcript.from_dict(r.to_dict()) == r


def test_fake_tts_is_wav():
    data = FakeTts(seconds=0.25).synthesize("hi", "en")
    with wave.open(io.BytesIO(data)) as w:
        assert w.getnchannels() == 1 and w.getnframes() == 4000


def test_cosine_and_calibration():
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([0, 0], [1, 1]) == 0.0
    with pytest.raises(ValueError):
        cosine([1], [1, 2])
    assert calibrate_speed(160.0) == pytest.approx(0.875)
