"""General rules: GEN-01 (spec §6) and EMG-01 (spec §7)."""
from tests.test_rules_helpers import ctx, daily_series, obs, run_rule, wobble


def C(**kw):
    return ctx("general", **kw)


def test_gen01_fires_on_short_sleep():
    s = {"sleep_duration": daily_series("sleep_duration", wobble(7.0, 30, 0.3), [5.0] * 6)}
    o = run_rule("GEN-01", C(series=s))
    assert o.fired and "usual range" in o.summary


def test_gen01_fires_on_fatigue_up():
    s = {"fatigue": daily_series("fatigue", wobble(2.0, 30, 0.5), [4.5] * 7)}
    assert run_rule("GEN-01", C(series=s)).fired


def test_gen01_not_fired_long_sleep_is_not_adverse():
    s = {"sleep_duration": daily_series("sleep_duration", wobble(7.0, 30, 0.3), [9.0] * 7)}
    assert run_rule("GEN-01", C(series=s)).status == "not_fired"


def test_gen01_insufficient():
    assert run_rule("GEN-01", C()).status == "insufficient"


def test_emg01_fires_on_red_flag_label_and_kind():
    assert run_rule("EMG-01", C(observations=[obs(1, "chest_pain")])).fired
    assert run_rule("EMG-01", C(observations=[obs(1, "something_else", kind="red_flag")])).fired
    assert run_rule("EMG-01", C(observations=[obs(1, "suicidal_ideation", kind="mood")])).fired


def test_emg01_not_fired_on_ordinary_symptom():
    assert run_rule("EMG-01", C(observations=[obs(1, "headache")])).status == "not_fired"


def test_emg01_not_fired_when_stale():
    assert run_rule("EMG-01", C(observations=[obs(30, "fainting")])).status == "not_fired"
