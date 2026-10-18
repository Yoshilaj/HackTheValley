"""General rules: GEN-01 (spec §6)."""
from tests.test_rules_helpers import ctx, daily_series, run_rule, wobble


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

