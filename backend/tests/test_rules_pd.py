"""Parkinson's rules (spec §5)."""
import pytest

from app.ml.context import DailySeries, TaskSessionIn
from app.ml.rules import RuleOutcome, Scratch
from app.ml.screener import PD_SCREENER_ITEMS, score_pd_screener
from tests.test_rules_helpers import at, ctx, daily_series, run_rule, screener, vowel, wobble


def C(**kw):
    return ctx("parkinsons", **kw)


# screener
def test_screener_items_have_ja_en():
    assert len(PD_SCREENER_ITEMS) == 8 and sum(i.motor for i in PD_SCREENER_ITEMS) == 4
    assert all(i.ja and i.en for i in PD_SCREENER_ITEMS)


def test_score_pd_screener():
    assert score_pd_screener({"tremor_rest": True, "soft_voice": True}) == (2, 0, True)
    assert score_pd_screener({"tremor_rest": True, "constipation": True}) == (1, 1, True)
    assert score_pd_screener({"tremor_rest": True}) == (1, 0, False)
    assert score_pd_screener({"smell_loss": True, "constipation": True, "falls": True}) == (0, 3, False)
    assert score_pd_screener({"unknown": True, "tremor_rest": False}) == (0, 0, False)


# PD-01
def test_pd01_fires():
    o = run_rule("PD-01", C(screeners=[screener({"stiffness_slowness": True, "dream_enactment": True})]))
    assert o.fired and o.data["schedule_tasks"] == ["sustained_vowel", "finger_tap"]


def test_pd01_not_fired():
    assert run_rule("PD-01", C(screeners=[screener({"constipation": True})])).status == "not_fired"


def test_pd01_insufficient_without_recent_screener():
    assert run_rule("PD-01", C()).status == "insufficient"
    assert run_rule("PD-01", C(screeners=[screener({"tremor_rest": True, "soft_voice": True}, offset=100)])).status == "insufficient"


# PD-02
def gait(speed_recent):
    return {"walking_speed": daily_series("walking_speed", wobble(1.2, 30, 0.02), speed_recent)}


def test_pd02_fires_walking_speed_drop():
    o = run_rule("PD-02", C(series=gait([1.0] * 7)))
    assert o.fired and o.evidence[0]["ref"] == "walking_speed"
    assert set(o.missing_inputs) == {"step_length", "double_support_pct", "walking_asymmetry_pct"}


def test_pd02_fires_on_asymmetry_increase():
    s = {"walking_asymmetry_pct": daily_series("walking_asymmetry_pct", wobble(3, 30, 0.3), [6] * 7)}
    assert run_rule("PD-02", C(series=s)).fired


def test_pd02_not_fired_stable():
    assert run_rule("PD-02", C(series=gait(wobble(1.2, 7, 0.02)))).status == "not_fired"


def test_pd02_insufficient_short_baseline():
    s = {"walking_speed": daily_series("walking_speed", wobble(1.2, 10, 0.02), [1.0] * 7, n_baseline=10)}
    assert run_rule("PD-02", C(series=s)).status == "insufficient"
    assert run_rule("PD-02", C()).status == "insufficient"


# PD-04
def test_pd04_fires_tap_count_drop():
    s = {"tap_count": daily_series("tap_count", wobble(60, 30, 2), [45] * 7)}
    assert run_rule("PD-04", C(series=s)).fired


def test_pd04_not_fired():
    s = {"tap_count": daily_series("tap_count", wobble(60, 30, 2), wobble(60, 7, 2))}
    assert run_rule("PD-04", C(series=s)).status == "not_fired"


def test_pd04_insufficient():
    assert run_rule("PD-04", C(series={"tap_count": DailySeries("tap_count", [])})).status == "insufficient"


# PD-03
def with_fired(*ids):
    sc = Scratch()
    for i in ids:
        sc.outcomes[i] = RuleOutcome("fired")
    return sc


def test_pd03_fires_with_supporting_rule(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "pd_vowel_rule_enabled", True)
    c = C(task_sessions=[vowel(20, 0.8), vowel(5, 0.7)])
    o = run_rule("PD-03", c, with_fired("PD-01"))
    assert o.fired and o.data["supporting_rules"] == ["PD-01"]


def test_pd03_display_only_by_default():
    """Default config: the voice model is unvalidated on app audio, so PD-03 notes but never fires."""
    from app.core.config import settings

    assert settings.pd_vowel_rule_enabled is False
    o = run_rule("PD-03", C(task_sessions=[vowel(20, 0.8), vowel(5, 0.7)]), with_fired("PD-01"))
    assert o.status == "not_fired" and o.data.get("display_only") is True
    assert o.evidence and "information only" in (o.note or "")


def test_pd03_voice_alone_never_fires_but_notes():
    o = run_rule("PD-03", C(task_sessions=[vowel(20, 0.8), vowel(5, 0.7)]), with_fired())
    assert o.status == "not_fired" and o.note


def test_pd03_not_fired_sessions_too_close_or_below_threshold():
    assert run_rule("PD-03", C(task_sessions=[vowel(8, 0.8), vowel(5, 0.7)]), with_fired("PD-01")).status == "not_fired"
    assert run_rule("PD-03", C(task_sessions=[vowel(20, 0.5), vowel(5, 0.7)]), with_fired("PD-01")).status == "not_fired"


def test_pd03_insufficient_without_valid_sessions():
    assert run_rule("PD-03", C(task_sessions=[vowel(20, 0.9, valid=False)]), with_fired("PD-01")).status == "insufficient"


def test_legacy_engine_ids_still_scored():
    assert score_pd_screener({"pd_rest_tremor": True, "pd_soft_voice": True}) == (2, 0, True)


def test_string_false_not_counted_as_yes():
    """'false'/'no'/'0' strings (e.g. from loosely-typed stored answers) must not count as symptoms."""
    assert score_pd_screener({"tremor_rest": "false", "soft_voice": "no", "smell_loss": "0"}) == (0, 0, False)
    assert score_pd_screener({"tremor_rest": "true", "soft_voice": "yes", "smell_loss": "1"}) == (2, 1, True)
    assert score_pd_screener({"tremor_rest": 1, "soft_voice": 0, "smell_loss": None}) == (1, 0, False)
    assert score_pd_screener({"tremor_rest": "FALSE", "soft_voice": " Yes "}) == (1, 0, False)
    assert score_pd_screener({"tremor_rest": "maybe", "soft_voice": 2}) == (0, 0, False)


def test_yes_helper():
    from app.ml.screener import _yes

    assert all(_yes(v) for v in (True, 1, 1.0, "yes", "Yes", "true", "1", "on", "y"))
    assert not any(_yes(v) for v in (False, 0, 0.0, "no", "false", "0", "off", "", None, "garbage", 2, [], {}))


def test_screener_note_exposed():
    from app.ml.screener import SCREENER_NOTE, ScreenerScore

    assert "not a validated instrument" in SCREENER_NOTE and "unvalidated" in SCREENER_NOTE
    assert "Tanner" in SCREENER_NOTE and "§5.1" in SCREENER_NOTE and "late PD sign" in SCREENER_NOTE
    res = score_pd_screener({"tremor_rest": True, "constipation": True})
    assert isinstance(res, ScreenerScore) and res.validation_note == SCREENER_NOTE
    d = res.to_dict()
    assert d == {"motor_count": 1, "nonmotor_count": 1, "pd01_would_fire": True, "validation_note": SCREENER_NOTE}
    motor, nonmotor, fired = res  # tuple unpacking still works
    assert (motor, nonmotor, fired) == (1, 1, True)


def test_engine_ids_match_content_bank():
    from app.content.loader import get_screener
    from app.ml.screener import PD_SCREENER_ITEMS

    s = get_screener("pd")
    assert [i["id"] for i in s.items] == [i.id for i in PD_SCREENER_ITEMS]
    assert [i["type"] == "motor" for i in s.items] == [i.motor for i in PD_SCREENER_ITEMS]


# PD-03B personal voice stability (spec §5.5)
from app.ml import rules as R  # noqa: E402
from tests.test_rules_helpers import (  # noqa: E402
    ADVERSE_STORY, ADVERSE_VOWEL, story_baseline, voice_baseline, voice_story, voice_vowel,
)


def _pd03b(sessions, *fired_ids):
    return run_rule("PD-03B", C(task_sessions=sessions), with_fired(*fired_ids))


def test_pd03b_display_only_alone_and_fires_with_supporting_rule():
    sessions = voice_baseline() + ADVERSE_VOWEL + story_baseline() + ADVERSE_STORY
    alone = _pd03b(sessions)
    assert alone.status == "not_fired" and alone.data["display_only"] is True
    assert set(alone.data["adverse_features"]) == {"MDVP:Shimmer", "NHR", "CPPS", "speech_rate", "pause_ratio"}
    assert alone.note.startswith("Change in voice measurements — Some measurements of your voice have changed")
    assert "cannot determine whether you have Parkinson's disease" in alone.note
    assert "voice stability, not disease progression" in alone.note
    assert alone.data["message"]["ja"]["title"] == "声の測定値の変化"
    assert [e["type"] for e in alone.evidence] == ["voice_trend_feature"] * 5
    shimmer = next(e["value"] for e in alone.evidence if e["value"]["feature"] == "MDVP:Shimmer")
    assert shimmer["k"] == 3.0 and shimmer["n_adverse"] == 3 and shimmer["status"] == "adverse"
    assert shimmer["no_change_alert_rates"]["two_of_three"] < 0.05 and shimmer["k_placeholder"] is False
    assert alone.data["k_by_feature"]["CPPS"] == 2.5 and alone.data["k_by_feature"]["speech_rate"] == 2.0
    with_pd = _pd03b(sessions, "PD-04")
    assert with_pd.fired and with_pd.level is None and with_pd.data["supporting_rules"] == ["PD-04"]
    assert with_pd.summary == ("Voice measurements have shifted from your usual range alongside another finding — "
                               "a change worth checking")


def test_pd03b_vowel_series_alone_can_satisfy_two_features():
    o = _pd03b(voice_baseline() + ADVERSE_VOWEL, "PD-01")
    assert o.fired and set(o.data["adverse_features"]) == {"MDVP:Shimmer", "NHR", "CPPS"}
    assert o.missing_inputs == ["voice_baseline_sessions"]  # story series not yet baselined (optional)


def test_pd03b_not_fired_one_feature_or_too_few_hits():
    one = voice_baseline() + [voice_vowel(o, 0.075) for o in (8, 4, 0)]  # shimmer only
    o = _pd03b(one, "PD-01")
    assert o.status == "not_fired" and o.data["adverse_features"] == ["MDVP:Shimmer"] and not o.note
    two_hits = voice_baseline() + [voice_vowel(8), voice_vowel(4, 0.075, 0.036, 10.5), voice_vowel(0, 0.075, 0.036, 10.5)]
    assert _pd03b(two_hits, "PD-01").status == "not_fired"
    assert _pd03b(voice_baseline() + [voice_vowel(o) for o in (8, 4, 0)], "PD-01").status == "not_fired"


def test_pd03b_insufficient_below_ten_baseline_sessions():
    o = _pd03b(voice_baseline(9) + story_baseline(4), "PD-01")
    assert o.status == "insufficient" and o.missing_inputs == ["voice_baseline_sessions"]
    assert o.data["baseline_sessions"] == {"sustained_vowel": 9, "story_recall": 4}
    assert "Fewer than 10 baseline recordings on this device so far (9 recorded)" in o.note
    assert _pd03b([]).status == "insufficient"
    assert _pd03b([voice_vowel(5, valid=False)] * 12).status == "insufficient"  # invalid sessions never count
    no_feats = [TaskSessionIn(at(o), "sustained_vowel", None, True, {}, id=f"x{o}") for o in range(12)]
    assert _pd03b(no_feats).status == "insufficient"


def test_pd03b_device_change_restarts_baseline():
    old = voice_baseline(device="Android Pixel 7")
    new = [voice_vowel(o, 0.075, 0.036, 10.5, device="iPhone") for o in (8, 4, 0)]
    o = _pd03b(old + new, "PD-01")
    assert o.status == "insufficient" and o.data["device_changed"] is True and o.data["device"] == "iPhone"
    assert "Recording device changed; the voice baseline restarts on the current device." in o.note
    assert o.data["baseline_sessions"]["sustained_vowel"] == 3
    # ten sessions on the new device → baseline complete there, the old device's sessions are ignored
    fresh = voice_baseline(start_offset=52, gap=4, device="iPhone") + new
    o2 = _pd03b(old + fresh, "PD-01")
    assert o2.fired and o2.data["device_changed"] is True
    # unknown device (None) is one device
    assert _pd03b(voice_baseline(device=None) + [voice_vowel(o, 0.075, 0.036, 10.5, device=None) for o in (8, 4, 0)],
                  "PD-01").fired


def test_pd03b_variability_floor_prevents_tiny_mad_firing():
    flat = [voice_vowel(60 - 5 * i, 0.0300 + 0.0001 * (i % 2), 0.0120 + 0.00001 * (i % 2), 14.0 + 0.01 * (i % 2))
            for i in range(10)]
    small = [voice_vowel(o, 0.036, 0.015, 13.0) for o in (8, 4, 0)]  # +20 % / +25 % / −1 dB: tiny MAD alone would trip
    o = _pd03b(flat + small, "PD-01")
    assert o.status == "not_fired" and o.data["adverse_features"] == []
    shimmer = next(e["value"] for e in o.evidence if e["value"]["feature"] == "MDVP:Shimmer")
    assert shimmer["scale_note"] == "variability_floor"
    assert shimmer["scale"] == pytest.approx(R.voice_variability_floor()["Shimmer"] * shimmer["baseline_median"])
    assert shimmer["threshold"] > 0.06  # ≥ ×2 the median at K=3 with the telemonitoring floor


def test_pd03b_floor_defaults_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "VOICE_FLOOR_PATH", tmp_path / "missing.json")
    R._voice_floor_doc.cache_clear()
    try:
        floor = R.voice_variability_floor()
        assert floor["_source"] == "defaults" and floor["Shimmer"] == R.VOICE_FLOOR_DEFAULTS["Shimmer"]
        assert R.voice_alert_rates("Shimmer", 3.0) is None
        o = _pd03b(voice_baseline() + ADVERSE_VOWEL, "PD-01")
        assert o.fired and o.data["floor_source"] == "defaults"
    finally:
        R._voice_floor_doc.cache_clear()


def test_pd03b_noise_floor_exclusion():
    noisy = [voice_vowel(o, 0.075, 0.036, 10.5, noise_floor=-40.0) for o in (8, 4, 0)]  # 20 dB noisier
    o = _pd03b(voice_baseline() + noisy, "PD-01")
    assert o.status == "not_fired" and o.data["adverse_features"] == []
    assert o.data["excluded_noise"] == {"sustained_vowel": ["vv_8", "vv_4", "vv_0"]}
    assert "3 recording(s) excluded: background noise more than 10 dB above your baseline recordings." in o.note
    # a session without a noise floor is kept
    assert _pd03b(voice_baseline() + [voice_vowel(o, 0.075, 0.036, 10.5, noise_floor=None) for o in (8, 4, 0)], "PD-01").fired


def test_pd03b_story_values_from_temporal_and_japanese_rate():
    ja = TaskSessionIn(at(1), "story_recall", 6.0, True, {}, features={"temporal": {
        "immediate_1": {"timing_source": "word_timestamps", "speech_span_s": 30, "speech_rate_wpm": None,
                        "speech_rate_cpm": 300.0, "pause_ratio": 0.3},
        "delayed": {"timing_source": "none", "speech_span_s": 0.0, "speech_rate_cpm": 1.0, "pause_ratio": 0.9},
        "session": {"delay_s": 180}}})
    assert R.voice_feature_values(ja) == {"speech_rate": 300.0, "pause_ratio": 0.3}
    assert R.voice_feature_values(voice_story(1, 120, 0.25)) == {"speech_rate": 120.0, "pause_ratio": 0.25}
    flat = TaskSessionIn(at(1), "story_recall", 6.0, True, {}, features={"speech_rate": 100, "pause_ratio": 0.4})
    assert R.voice_feature_values(flat) == {"speech_rate": 100.0, "pause_ratio": 0.4}
    assert R.voice_feature_values(TaskSessionIn(at(1), "finger_tap", None, True, {}, features={"tap_count": 5})) == {}
    assert R.session_noise_floor(voice_story(1).recording_quality) == -58.0
    assert R.session_noise_floor({"noise_floor_dbfs": -50}) == -50.0 and R.session_noise_floor({}) is None


def test_pd03b_story_only_fires_with_two_speech_features():
    o = _pd03b(story_baseline() + ADVERSE_STORY, "PD-02")
    assert o.fired and set(o.data["adverse_features"]) == {"speech_rate", "pause_ratio"}


def test_pd03b_registered_after_supporting_rules_with_sources():
    ids = [r.id for r in R.rules_for("parkinsons")]
    assert ids.index("PD-03B") > max(ids.index(x) for x in ("PD-01", "PD-02", "PD-04"))
    rule = R.RULES_BY_ID["PD-03B"]
    assert rule.level == "watch" and not rule.informational
    assert rule.sources == ["src_skodda2013", "src_wright2025", "src_bottalico2018", "src_barsties2025"]
