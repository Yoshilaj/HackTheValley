"""Cognitive rules (spec §1.3)."""
from app.ml.context import TaskSessionIn
from tests.test_rules_helpers import at, ctx, obs, run_rule, story_sessions

BASE = [70, 72, 71, 69, 73]  # median 71, MAD 1.4826 → 1.5 MAD ≈ 2.2


def C(scores=None, **kw):
    return ctx("cognitive", task_sessions=story_sessions(scores) if scores is not None else [], **kw)


# COG-01
def test_cog01_insufficient_under_five_sessions():
    o = run_rule("COG-01", C(BASE[:4]))
    assert o.status == "insufficient" and o.data["n_baseline"] == 4


def test_cog01_complete_baseline():
    assert run_rule("COG-01", C(BASE)).status == "not_fired"


def test_cog01_sessions_closer_than_3_days_do_not_count():
    sessions = [TaskSessionIn(at(30 - i), "story_recall", 70, True) for i in range(6)]  # daily
    assert run_rule("COG-01", ctx("cognitive", task_sessions=sessions)).status == "insufficient"


def test_cog01_invalid_sessions_do_not_count():
    sessions = [TaskSessionIn(at(60 - 4 * i), "story_recall", 70, i != 2) for i in range(5)]
    assert run_rule("COG-01", ctx("cognitive", task_sessions=sessions)).status == "insufficient"


# COG-02
def test_cog02_fires_three_of_last_five_low():
    o = run_rule("COG-02", C(BASE + [60, 61, 70, 59, 71]))
    assert o.fired and len(o.evidence) == 3 and "usual range" in o.summary


def test_cog02_not_fired_two_low():
    assert run_rule("COG-02", C(BASE + [60, 61, 70, 72, 71])).status == "not_fired"


def test_cog02_insufficient_without_followup_sessions():
    assert run_rule("COG-02", C(BASE + [60, 61])).status == "insufficient"
    assert run_rule("COG-02", C(BASE[:3])).status == "insufficient"


# COG-03
def test_cog03_fires_functional_change_without_task_data():
    o = run_rule("COG-03", C(observations=[obs(48, "getting_lost", "functional_change")]))
    assert o.fired


def test_cog03_not_fired_for_other_observations():
    assert run_rule("COG-03", C(observations=[obs(48, "headache")])).status == "not_fired"


def test_cog03_not_fired_when_older_than_30_days():
    assert run_rule("COG-03", C(observations=[obs(24 * 31, "repeating_questions", "functional_change")])).status == "not_fired"


def test_cog03_fires_on_each_spec_listed_label():
    for label in ("missed_appointments", "getting_lost", "repeating_questions",
                  "trouble_managing_money", "trouble_managing_medication"):
        assert run_rule("COG-03", C(observations=[obs(48, label, "functional_change")])).fired, label


def test_cog03_single_checkin_memory_lapse_does_not_fire():
    """A daily check-in "yes" to memory lapse / confusion is stored as kind=functional_change
    (ingest_service.FLAG_OBSERVATIONS) but is not a spec §1.3 functional change → no follow-up."""
    assert run_rule("COG-03", C(observations=[obs(2, "memory_lapse", "functional_change")])).status == "not_fired"
    assert run_rule("COG-03", C(observations=[obs(2, "confusion", "functional_change")])).status == "not_fired"
    # repeated check-in flags still do not qualify on their own
    many = [obs(24 * d + 1, "memory_lapse", "functional_change") for d in range(5)]
    assert run_rule("COG-03", C(observations=many)).status == "not_fired"


def test_cog03_fires_on_free_text_functional_change_report():
    """Check-in free text / chat extraction: a user-described functional change (generic label)."""
    assert run_rule("COG-03", C(observations=[obs(2, "functional_change_reported", "functional_change")])).fired
    assert run_rule("COG-03", C(observations=[obs(2, "functional_change", "functional_change")])).fired


# COG-04 (informational)
def test_cog04_retest_prompt():
    o = run_rule("COG-04", C(BASE + [50]))
    assert o.fired and o.note == "retest"


def test_cog04_not_fired_mild_drop():
    assert run_rule("COG-04", C(BASE + [68])).status == "not_fired"


def test_cog04_insufficient():
    assert run_rule("COG-04", C(BASE[:2] + [10])).status == "insufficient"
