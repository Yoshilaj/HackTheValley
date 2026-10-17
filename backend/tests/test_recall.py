"""Experimental semantic-recall score, session scoring and the story-task state machine."""

from datetime import datetime, timedelta, timezone

import pytest

from app.content import get_story_by_id
from app.ml.recall import PROTOCOL_VERSION, content_unit_recall, score_session, semantic_recall
from app.ml.story_task import (
    STEPS, DelayNotElapsedError, StepOrderError, StorySession, finalize, new_session, new_word_recall,
    next_step, score_word_recall,
)
from app.providers import FakeEmbeddings, Transcript

P = FakeEmbeddings()

EN = get_story_by_id("en-01-market")
EN_FULL = ("On Saturday Helen took the bus to the market in Riverside. She wanted tomatoes and yellow flowers for her "
           "sister Margaret who came to lunch. A farmer called Tom sold her tomatoes and honey. There were no yellow "
           "flowers so she picked white ones. She forgot her umbrella at the honey stall and went back, and Tom had "
           "kept it. She bought more honey. At noon they had tomato soup and laughed.")
EN_PART = "Helen went to a market and bought some tomatoes. She forgot her umbrella."
EN_UNREL = "I usually play golf with my brother on Sundays and then we watch baseball on television in the evening."

JA = get_story_by_id("ja-01-asaichi")
JA_FULL = ("土曜日の朝、花子さんはバスで桜町の朝市に行きました。妹の幸子さんが昼に来るので、トマトと黄色い花を買いたかったです。"
           "田中さんという農家からトマトを六つとはちみつを買いました。黄色い花はなかったので白い花にしました。"
           "帰りに傘を忘れたことに気づいて戻ると、田中さんが傘を預かっていました。はちみつをもう一つ買いました。"
           "お昼に二人でトマトのスープを飲んで笑いました。")
JA_PART = "花子さんは朝市でトマトを買いました。傘を忘れました。"
JA_UNREL = "私は毎週日曜日に兄とゴルフをして、夜はテレビで野球を見ます。"


def tr(text, lang, dur=30.0):
    return Transcript(text=text, language=lang, segments=[{"start": 0.0, "end": dur, "text": text}],
                      provider="fake", duration_s=dur)


@pytest.mark.parametrize("story,full,part,unrel", [(EN, EN_FULL, EN_PART, EN_UNREL), (JA, JA_FULL, JA_PART, JA_UNREL)])
def test_semantic_recall_ordering(story, full, part, unrel):
    f, p, u = (semantic_recall(story.text, t, P) for t in (full, part, unrel))
    assert 0 <= u < p < f <= 100
    assert semantic_recall(story.text, "", P) == 0.0
    assert semantic_recall(story.text, story.text, P) == pytest.approx(100.0)


@pytest.mark.parametrize("story,full,part,unrel", [(EN, EN_FULL, EN_PART, EN_UNREL), (JA, JA_FULL, JA_PART, JA_UNREL)])
def test_content_unit_recall_ordering(story, full, part, unrel):
    res = {k: content_unit_recall(story.content_units, t, P, threshold=0.4, language=story.language)
           for k, t in (("full", full), ("part", part), ("unrel", unrel))}
    mean = {k: sum(u["score"] for u in v[1]) / len(v[1]) for k, v in res.items()}
    assert res["full"][0] > res["part"][0] >= res["unrel"][0]
    assert mean["full"] > mean["part"] > mean["unrel"]
    assert len(res["full"][1]) == len(story.content_units)
    assert content_unit_recall(story.content_units, "", P)[0] == 0.0


def test_score_session_valid():
    s2 = get_story_by_id("en-03-cake")
    r = score_session([EN, s2], [tr(EN_FULL, "en"), tr("Kate made a birthday cake for Leo with peaches.", "en"), tr(EN_PART, "en")], P)
    assert r.valid and r.status == "ok" and not r.invalid_reasons
    assert set(r.per_recall) == {"immediate_1", "immediate_2", "delayed"}
    assert r.per_recall["immediate_1"] > r.per_recall["delayed"]
    assert r.semantic_recall_mean == pytest.approx(sum(r.per_recall.values()) / 3, abs=0.01)
    assert r.story_ids == [EN.id, s2.id] and r.protocol_version == PROTOCOL_VERSION
    assert r.lexical["immediate_1"]["word_count"] > 0 and "speech_duration_s" in r.temporal["immediate_1"]
    assert "G-match" not in str(r.to_dict())


def test_score_session_invalid_empty_and_short():
    r = score_session(EN, [tr("", "en"), tr(EN_FULL, "en", dur=3.0), None], P)
    assert not r.valid and r.status == "invalid_measurement"
    assert "immediate_1:empty_transcript" in r.invalid_reasons
    assert "immediate_2:speech_lt_5s" in r.invalid_reasons
    assert "delayed:no_transcript" in r.invalid_reasons
    assert r.semantic_recall_mean is None


def test_score_session_missing_recall():
    r = score_session(EN, [tr(EN_FULL, "en")], P)
    assert not r.valid and "missing_delayed" in r.invalid_reasons
    assert r.semantic_recall_mean == r.per_recall["immediate_1"]


def test_story_task_step_order_and_finalize():
    t0 = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
    s = new_session("en", used_story_ids=["en-01-market", "en-02-seaside", "en-03-cake", "en-04-cat"], now=t0)
    assert s.steps == list(STEPS)
    assert set(s.story_ids) == {"en-05-photo", "en-06-soup"} and s.story_ids[0] != s.story_ids[1]
    assert s.current_step == "story1_listen" and s.instruction().startswith("Listen")
    with pytest.raises(StepOrderError):
        next_step(s, "story1_recall", {}, now=t0)
    s1, s2 = (get_story_by_id(i) for i in s.story_ids)
    assert next_step(s, "story1_listen", {"played_s": 50}, now=t0) == "story1_recall"
    assert "as much detail" in s.instruction()
    next_step(s, "story1_recall", {"transcript": tr(s1.text, "en")}, now=t0 + timedelta(seconds=60))
    next_step(s, "story2_listen", {}, now=t0 + timedelta(seconds=120))
    next_step(s, "story2_recall", {"transcript": tr(s2.text, "en").to_dict()}, now=t0 + timedelta(seconds=180))
    assert next_step(s, "fluency_60s", {"transcript": tr("dog cat horse lion dog", "en", 60)}, now=t0 + timedelta(seconds=240)) == "delay"
    with pytest.raises(DelayNotElapsedError):
        next_step(s, "delay", {}, now=t0 + timedelta(seconds=200))
    assert next_step(s, "delay", {}, now=t0 + timedelta(seconds=300)) == "story1_delayed_recall"
    assert s.delay_s == pytest.approx(240)
    pending = finalize(StorySession.from_dict(s.to_dict()), None, P)
    assert "incomplete_session" in pending.invalid_reasons
    assert next_step(s, "story1_delayed_recall", {"transcript": tr(s1.text, "en")}, now=t0 + timedelta(seconds=400)) is None
    with pytest.raises(StepOrderError):
        next_step(s, "story1_delayed_recall", {})
    res = finalize(s, None, P)
    assert res.valid and res.semantic_recall_mean == pytest.approx(100.0)
    assert res.temporal["session"]["delay_s"] == pytest.approx(240)
    assert res.temporal["session"]["fluency_unique_nouns"] == 4
    # explicit list form
    res2 = finalize(s, [tr(s1.text, "en"), tr(s2.text, "en"), tr("", "en")], P)
    assert not res2.valid and "delayed:empty_transcript" in res2.invalid_reasons


def test_new_session_ja_and_exhausted():
    s = new_session("ja-JP", [])
    assert s.language == "ja" and all(i.startswith("ja-") for i in s.story_ids)
    assert "詳しく" in s.instruction("story1_recall")
    from app.content import ContentError
    with pytest.raises(ContentError):
        new_session("en", [f"en-0{i}" for i in range(1, 6)] + ["en-01-market", "en-02-seaside", "en-03-cake", "en-04-cat", "en-05-photo"])


def test_word_recall():
    trial = new_word_recall("ja")
    assert len(trial.words) == 3 and trial.language == "ja"
    r = score_word_recall(["りんご", "いす", "かわ"], "えーと、林檎と、椅子と…あとは忘れました", 2300, "ja")
    assert r["score"] == 2 and r["missed"] == ["かわ"] and r["latency_ms"] == 2300.0
    assert score_word_recall(["かさ", "つくえ", "もも"], "たかさ", None, "ja")["score"] == 0
    r = score_word_recall(["bicycle", "lemon", "window"], "um a bike and lemons", 1200, "en")
    assert r["score"] == 2 and r["recalled"] == ["bicycle", "lemon"]
    assert score_word_recall(["apple", "chair", "river"], "", None, "en")["valid"] is False


def test_score_session_with_audio_quality():
    import numpy as np

    sr = 16000
    t = np.arange(int(8 * sr)) / sr
    rng = np.random.default_rng(0)
    clean = (0.3 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 0.5 * t) > -0.5)
             + 0.001 * rng.standard_normal(t.size)).astype(np.float32)
    clipped = np.clip(clean * 10, -1, 1).astype(np.float32)
    r = score_session(EN, [tr(EN_FULL, "en", 8.0)] * 3, P, audio=[(clean, sr), (clipped, sr), None])
    assert r.recalls[0].recording_quality is not None
    assert r.recalls[0].valid
    assert "immediate_2:clipping_gt_1pct" in r.invalid_reasons
