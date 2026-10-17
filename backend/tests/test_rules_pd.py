"""Parkinson's rules (spec §5)."""
from app.ml.context import DailySeries
from app.ml.rules import RuleOutcome, Scratch
from app.ml.screener import PD_SCREENER_ITEMS, score_pd_screener
from tests.test_rules_helpers import ctx, daily_series, run_rule, screener, vowel, wobble


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


def test_pd03_fires_with_supporting_rule():
    c = C(task_sessions=[vowel(20, 0.8), vowel(5, 0.7)])
    o = run_rule("PD-03", c, with_fired("PD-01"))
    assert o.fired and o.data["supporting_rules"] == ["PD-01"]


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
