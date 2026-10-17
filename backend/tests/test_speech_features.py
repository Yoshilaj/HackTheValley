"""Tokeniser, lexical, temporal and spectral features (spec §1.1 secondary features)."""

import math

import numpy as np
import pytest

from app.ml.features.speech import (
    energy_vad, lexical_features, pos_tags, spectral_features, temporal_features, to_hiragana, tokenize,
)


def test_tokenize_en():
    assert tokenize("Helen's cat, Pepper, SAT on the mat!", "en") == ["helen's", "cat", "pepper", "sat", "on", "the", "mat"]


def test_tokenize_ja_excludes_punctuation():
    toks = tokenize("太郎さんは、東京の駅で花を買いました。", "ja")
    assert "、" not in toks and "。" not in toks
    assert "東京" in toks and "花" in toks and "太郎" in toks


def test_pos_tags_ja_and_en():
    ja = dict(pos_tags("彼は東京で花を買った", "ja"))
    assert ja["彼"] == "PRON" and ja["東京"] == "PROPN" and ja["花"] == "NOUN" and ja["買っ"] == "VERB"
    en = dict(pos_tags("Yesterday she walked to the market with Tom.", "en"))
    assert en["she"] == "PRON" and en["to"] == "ADP" and en["walked"] == "VERB"
    assert en["market"] == "NOUN" and en["tom"] == "PROPN"


def test_lexical_features_en():
    f = lexical_features("Um, she went to the, uh, the thing. She bought apples and bread.", "en")
    assert f["word_count"] == 13
    assert f["filler_rate"] == pytest.approx(2 / 13)
    assert f["vague_word_rate"] == pytest.approx(1 / 13)
    assert 0 < f["unique_word_ratio"] < 1
    assert f["pronoun_noun_ratio"] > 0 and 0 < f["noun_rate"] < 1 and 0 < f["idea_density"] < 1


def test_lexical_features_ja():
    f = lexical_features("えー、花子さんは、あれ、朝市でトマトを買いました。", "ja")
    assert f["filler_rate"] > 0 and f["vague_word_rate"] > 0
    assert f["char_count"] == len("えー、花子さんは、あれ、朝市でトマトを買いました。")
    assert f["noun_rate"] > 0 and f["tagger"] == "fugashi-unidic"
    # determiner あの is not a filler
    assert lexical_features("あの店でパンを買った。", "ja")["filler_rate"] == 0.0


def test_lexical_features_empty():
    f = lexical_features("", "en")
    assert f["word_count"] == 0 and f["idea_density"] == 0.0


def _words(spec):
    """spec: list of (word, duration) with a gap list in between."""
    t, out = 0.5, []
    for w, dur, gap in spec:
        out.append({"word": w, "start": t, "end": t + dur})
        t += dur + gap
    return out


def test_temporal_from_word_timestamps():
    ws = _words([("she", 0.3, 0.1), ("went", 0.3, 0.5), ("to", 0.2, 0.1), ("the", 0.2, 1.0), ("market", 0.5, 0.0)])
    f = temporal_features(ws, audio_duration_s=5.0, language="en")
    assert f["timing_source"] == "word_timestamps"
    assert f["n_pauses_gt_200ms"] == 2
    assert f["total_pause_s"] == pytest.approx(1.5)
    span = 0.3 + 0.1 + 0.3 + 0.5 + 0.2 + 0.1 + 0.2 + 1.0 + 0.5
    assert f["speech_span_s"] == pytest.approx(span)
    assert f["speech_duration_s"] == pytest.approx(span - 1.5)
    assert f["pause_ratio"] == pytest.approx(1.5 / span)
    assert f["speech_rate_wpm"] == pytest.approx(5 / (span / 60))
    assert f["articulation_rate"] > f["speech_rate_wpm"]


def test_temporal_ja_uses_chars():
    ws = [{"word": "花子さんは", "start": 0.0, "end": 1.0}, {"word": "朝市に", "start": 1.5, "end": 2.0}]
    f = temporal_features(ws, 2.0, language="ja")
    assert f["rate_unit"] == "chars" and f["unit_count"] == 8
    assert f["speech_rate_cps"] == pytest.approx(4.0) and f["speech_rate_wpm"] is None


def test_temporal_from_audio_vad():
    sr = 16000
    tone = lambda s: 0.5 * np.sin(2 * np.pi * 200 * np.arange(int(s * sr)) / sr)
    sil = lambda s: np.zeros(int(s * sr))
    y = np.concatenate([sil(0.5), tone(1.0), sil(0.6), tone(1.0), sil(0.1), tone(0.5), sil(0.5)]).astype(np.float32)
    runs = energy_vad(y, sr)
    assert len(runs) == 3
    f = temporal_features(None, None, y=y, sr=sr, language="en", text="one two three four")
    assert f["timing_source"] == "energy_vad"
    assert f["n_pauses_gt_200ms"] == 1
    assert f["total_pause_s"] == pytest.approx(0.6, abs=0.06)
    assert f["audio_duration_s"] == pytest.approx(4.2)


def test_temporal_empty():
    f = temporal_features([], None)
    assert f["timing_source"] == "none" and f["speech_duration_s"] == 0.0


def test_spectral_features_on_tone():
    sr = 16000
    t = np.arange(int(1.5 * sr)) / sr
    rng = np.random.default_rng(0)
    y = (0.4 * np.sin(2 * np.pi * 150 * t) + 0.01 * rng.standard_normal(t.size)).astype(np.float32)
    f = spectral_features(y, sr)
    assert f["reliability"] == "low_on_uncontrolled_mics"
    assert f["f0_mean"] == pytest.approx(150, rel=0.05)
    assert len(f["mfcc_mean"]) == 13 and len(f["mfcc_sd"]) == 13
    for k in ("hnr", "jitter_local", "shimmer_local"):
        assert k in f
    assert not math.isnan(f["hnr"]) and f["hnr"] > 10


def test_to_hiragana():
    assert to_hiragana("トマト") == "とまと"
