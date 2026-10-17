"""Follow-up priority score (spec §11.1) and dashboard headline text.

The score (0–100) is derived only from the rule-engine output. It is *not* a
disease probability and never triggers anything by itself; booking is driven
by ``level == followup_recommended`` (score ≥60 by construction).

| level                | base | additions (capped)                                         |
|----------------------|------|-----------------------------------------------------------|
| no_finding           | 0    | +5 per watch rule fired in last 30 days, now cleared; ≤15 |
| watch                | 25   | +10 per extra watch rule; +10 if persistent ≥2 windows; ≤55 |
| followup_recommended | 60   | +10 per extra follow-up rule; +10 if any watch rule also fired; +5 if persistent; ≤85 |
| emergency            | 100  | —                                                          |
| insufficient/invalid | None | UI shows "—"                                              |
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from app.ml.context import PriorAssessment
from app.ml.engine import PathwayResult, prior_rule_ids
from app.ml.rules import RULES_BY_ID

SCORE_KIND = "follow_up_priority"
SCORE_LABEL = "Follow-up priority (not a disease probability)"
SCORE_LABEL_I18N = {"en": SCORE_LABEL, "ja": "受診の優先度（病気の確率ではありません）"}
DISCLAIMER = {
    "en": "This is not a diagnosis. The priority score reflects which follow-up rules fired, "
          "not the chance that you have a condition.",
    "ja": "これは診断ではありません。優先度は、どの確認ルールが該当したかを示すもので、"
          "病気である確率ではありません。",
}

# spec §11.1 band table
NO_FINDING_BASE, NO_FINDING_PER_RESOLVED, NO_FINDING_MAX = 0, 5, 15
RESOLVED_LOOKBACK = timedelta(days=30)
WATCH_BASE, WATCH_PER_EXTRA, WATCH_PERSIST, WATCH_MAX = 25, 10, 10, 55
FU_BASE, FU_PER_EXTRA, FU_WITH_WATCH, FU_PERSIST, FU_MAX = 60, 10, 10, 5, 85
EMERGENCY_SCORE = 100
PERSISTENT_WINDOWS = 2


def _persistent(result: PathwayResult) -> bool:
    return any(n >= PERSISTENT_WINDOWS for n in result.persistence.values())


def _recently_resolved(result: PathwayResult, priors: Iterable[PriorAssessment]) -> int:
    priors = list(priors)
    as_of: datetime | None = result.as_of or max((p.created_at for p in priors), default=None)
    if as_of is None:
        return 0
    current = set(result.fired_rule_ids)
    resolved = {
        rid
        for p in priors
        if as_of - RESOLVED_LOOKBACK <= p.created_at < as_of
        for rid in prior_rule_ids(p)
        if rid in RULES_BY_ID and RULES_BY_ID[rid].level == "watch" and rid not in current
    }
    return len(resolved)


def priority_score(result: PathwayResult, prior_assessments: Iterable[PriorAssessment] = ()) -> int | None:
    """Follow-up priority 0–100 per spec §11.1, or ``None`` for insufficient/invalid."""
    level = result.level
    if level == "emergency":
        return EMERGENCY_SCORE
    if level in ("insufficient_data", "invalid_measurement"):
        return None
    levels = [f.level for f in result.fired_rules]
    n_watch = levels.count("watch")
    if level == "no_finding":
        n = _recently_resolved(result, prior_assessments)
        return min(NO_FINDING_BASE + NO_FINDING_PER_RESOLVED * n, NO_FINDING_MAX)
    if level == "watch":
        score = WATCH_BASE + WATCH_PER_EXTRA * max(n_watch - 1, 0)
        score += WATCH_PERSIST if _persistent(result) else 0
        return min(score, WATCH_MAX)
    if level == "followup_recommended":
        n_fu = levels.count("followup_recommended")
        score = FU_BASE + FU_PER_EXTRA * max(n_fu - 1, 0)
        score += FU_WITH_WATCH if n_watch else 0
        score += FU_PERSIST if _persistent(result) else 0
        return min(score, FU_MAX)
    raise ValueError(f"unknown level {level!r}")


_COUNT_EN = {1: "One finding suggests", 2: "Two findings suggest", 3: "Three findings suggest",
             4: "Four findings suggest"}


def headline_for(result: PathwayResult) -> dict[str, str]:
    """Short, neutral headline per level (JA and EN). Never names a disease."""
    level = result.level
    n = len(result.fired_rules)
    if level == "emergency":
        return {"en": "Please get urgent help now — follow the emergency guidance below",
                "ja": "すぐに助けを求めてください。下の緊急時の案内に従ってください"}
    if level == "followup_recommended":
        n_fu = sum(1 for f in result.fired_rules if f.level == "followup_recommended") or 1
        lead = _COUNT_EN.get(n_fu, f"{n_fu} findings suggest")
        return {"en": f"{lead} a check-up is worth arranging",
                "ja": f"受診を検討したほうがよい所見が{n_fu}件あります"}
    if level == "watch":
        return {"en": "A change worth keeping an eye on — keep tracking" if n <= 1
                else f"{n} changes worth keeping an eye on — keep tracking",
                "ja": "経過を見ていきたい変化があります。記録を続けましょう"}
    if level == "no_finding":
        return {"en": "No findings from your recent data", "ja": "最近のデータでは気になる所見はありません"}
    if level == "invalid_measurement":
        return {"en": "Some measurements need to be re-recorded", "ja": "測り直しが必要な測定があります"}
    return {"en": "More data is needed before this can be assessed",
            "ja": "判定にはもう少しデータが必要です"}
