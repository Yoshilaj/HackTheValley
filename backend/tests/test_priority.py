"""Follow-up priority score (spec §11.1 worked examples)."""
from app.ml.engine import FiredRule, PathwayResult
from app.ml.priority import SCORE_LABEL, headline_for, priority_score
from app.ml.rules import RULES_BY_ID
from tests.test_rules_helpers import AS_OF, prior


def result(*rule_ids, persistence=None, level=None):
    fired = [FiredRule(i, "1", RULES_BY_ID[i].level, [], i) for i in rule_ids]
    order = ["no_finding", "watch", "followup_recommended", "emergency"]
    lv = level or max((f.level for f in fired), key=order.index, default="no_finding")
    return PathwayResult("x", lv, fired, persistence=persistence or {i: 1 for i in rule_ids}, as_of=AS_OF)


def test_worked_examples():
    assert priority_score(result("BP-02")) == 60
    assert priority_score(result("BP-02", "RHY-01")) == 70
    assert priority_score(result("BP-02", "RHY-01", "BP-04")) == 80
    assert priority_score(result("PD-01")) == 25
    assert priority_score(result("PD-01", "PD-02")) == 35
    assert priority_score(result("PD-01", "PD-02", persistence={"PD-01": 1, "PD-02": 2})) == 45


def test_emergency_and_insufficient():
    assert priority_score(result("EMG-01")) == 100
    assert priority_score(result(level="insufficient_data")) is None
    assert priority_score(result(level="invalid_measurement")) is None


def test_band_caps():
    many_watch = result("PD-01", "PD-02", "PD-04", "PD-03", "BP-01", persistence={"PD-01": 3})
    assert priority_score(many_watch) == 55
    fu = result("BP-02", "RHY-01", "COG-03", "BP-04", persistence={"BP-02": 2})
    assert priority_score(fu) == 85  # 60 + 20 + 10 + 5 = 95 → capped
    assert priority_score(result("BP-02", persistence={"BP-02": 2})) == 65


def test_no_finding_recently_resolved():
    priors = [prior(10, ["BP-01"]), prior(20, ["PD-01", "BP-02"]), prior(40, ["RHY-03"])]
    # BP-01, PD-01 are watch rules within 30 days; BP-02 is follow-up; RHY-03 too old
    assert priority_score(result(), priors) == 10
    priors = [prior(5, ["BP-01", "BP-03", "BP-04", "PD-01"])]
    assert priority_score(result(), priors) == 15


def test_headline_and_label():
    assert headline_for(result("BP-02", "RHY-01"))["en"] == "Two findings suggest a check-up is worth arranging"
    for lv in ["no_finding", "insufficient_data", "invalid_measurement"]:
        h = headline_for(result(level=lv))
        assert h["en"] and h["ja"]
    assert headline_for(result("PD-01"))["ja"]
    assert "not a disease probability" in SCORE_LABEL
