"""Synthetic-context factories shared by the rule/engine tests (no tests here)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from app.ml.context import (
    BpReadingIn, DailySeries, ObservationIn, PathwayContext, PriorAssessment, ProfileIn,
    RhythmResultIn, ScreenerIn, TaskSessionIn,
)
from app.ml.rules import RULES_BY_ID, RuleOutcome, Scratch

AS_OF = datetime(2026, 10, 17, 22, 0)
TODAY = AS_OF.date()


def day(offset: int) -> date:
    """Calendar day ``offset`` days before AS_OF's date."""
    return TODAY - timedelta(days=offset)


def at(offset_days: int, hh: int = 9, mm: int = 0) -> datetime:
    return datetime.combine(day(offset_days), time(hh, mm))


def bp_days(
    n_days: int,
    morning: tuple[int, int] | None = (130, 80),
    evening: tuple[int, int] | None = (130, 80),
    start_offset: int = 0,
    readings_per_occasion: int = 2,
) -> list[BpReadingIn]:
    """``n_days`` consecutive days ending ``start_offset`` days before AS_OF."""
    out: list[BpReadingIn] = []
    for i in range(start_offset, start_offset + n_days):
        for session, val, hh in (("morning", morning, 7), ("evening", evening, 21)):
            if val is None:
                continue
            for k in range(readings_per_occasion):
                out.append(BpReadingIn(at(i, hh, k), session, val[0], val[1], 70, False, True,
                                       id=f"bp_{i}_{session}_{k}"))
    return out


def daily_series(name: str, baseline: list[float] | float, recent: list[float],
                 n_baseline: int = 30) -> DailySeries:
    """Baseline covering offsets 7..(7+n_baseline-1), recent covering offsets 0..len(recent)-1
    (recent[0] is the most recent day)."""
    base = baseline if isinstance(baseline, list) else [baseline] * n_baseline
    pts = [(day(7 + i), float(v)) for i, v in enumerate(base)]
    pts += [(day(i), float(v)) for i, v in enumerate(recent)]
    return DailySeries(name, pts)


def wobble(center: float, n: int = 30, amp: float = 1.0) -> list[float]:
    """Deterministic non-flat baseline (MAD > 0)."""
    pattern = [-amp, 0.0, amp, -amp / 2, amp / 2]
    return [center + pattern[i % len(pattern)] for i in range(n)]


def story_sessions(scores: list[float], start_offset: int = 60, gap_days: int = 4) -> list[TaskSessionIn]:
    out = []
    for i, s in enumerate(scores):
        out.append(TaskSessionIn(at(start_offset - i * gap_days), "story_recall", s, True, {}, id=f"story_{i}"))
    return out


def vowel(offset: int, prob: float, threshold: float = 0.6, valid: bool = True) -> TaskSessionIn:
    return TaskSessionIn(at(offset), "sustained_vowel", None, valid,
                         {"pd_vowel": {"probability": prob, "threshold": threshold, "version": "v1"}},
                         id=f"vowel_{offset}")


def rhythm(offset: int, label: str, source: str = "apple_ecg") -> RhythmResultIn:
    return RhythmResultIn(at(offset), source, label, id=f"drr_{offset}_{label}")


def obs(offset_hours: float, label: str, kind: str = "symptom") -> ObservationIn:
    return ObservationIn(AS_OF - timedelta(hours=offset_hours), kind, label, None, None, id=f"obs_{label}")


def screener(answers: dict[str, bool], offset: int = 2) -> ScreenerIn:
    return ScreenerIn(at(offset), "pd", answers, None, id="scr_1")


def prior(offset_days: int, rules: Iterable[str], level: str = "watch") -> PriorAssessment:
    return PriorAssessment(AS_OF - timedelta(days=offset_days), level, list(rules))


def ctx(pathway: str = "cardio", **kw) -> PathwayContext:
    kw.setdefault("profile", ProfileIn())
    return PathwayContext(user_id="u1", pathway=pathway, as_of=AS_OF, **kw)


def run_rule(rule_id: str, c: PathwayContext, scratch: Scratch | None = None) -> RuleOutcome:
    return RULES_BY_ID[rule_id].condition(c, scratch or Scratch())


# ---------------------------------------------------------------- PD-03B voice sessions (spec §5.5)
VOICE_WOBBLE = (-1, 0, 1, -0.5, 0.5, 0.25, -0.25, 0.75, -0.75, 0.1)


def voice_vowel(offset: int, shimmer: float = 0.030, nhr: float = 0.012, cpps: float = 14.0, *, device: str | None = "iPhone",
                noise_floor: float | None = -60.0, valid: bool = True, extra: dict | None = None) -> TaskSessionIn:
    features = {"MDVP:Shimmer": shimmer, "NHR": nhr, "CPPS": cpps, "MDVP:Jitter(%)": 0.004, "HNR": 22.0}
    if extra:
        features.update(extra)
    rq = {"noise_floor_dbfs": noise_floor} if noise_floor is not None else {}
    return TaskSessionIn(at(offset, 10), "sustained_vowel", None, valid, {}, id=f"vv_{offset}", features=features,
                         device=device, recording_quality=rq)


def voice_story(offset: int, rate: float = 130.0, pause: float = 0.20, *, device: str | None = "iPhone",
                noise_floor: float | None = -58.0, language: str = "en") -> TaskSessionIn:
    rate_key = "speech_rate_wpm" if language == "en" else "speech_rate_cpm"
    temporal = {label: {"timing_source": "word_timestamps", "speech_span_s": 30.0, rate_key: rate, "pause_ratio": pause}
                for label in ("immediate_1", "immediate_2", "delayed")}
    temporal["session"] = {"delay_s": 180.0}
    rq = {label: {"audio": {"noise_floor_dbfs": noise_floor}, "asr_confidence": 0.9} for label in ("immediate_1", "delayed")}
    return TaskSessionIn(at(offset, 11), "story_recall", 6.0, True, {}, id=f"vs_{offset}",
                         features={"semantic_recall_mean": 6.0, "temporal": temporal}, device=device,
                         recording_quality=rq if noise_floor is not None else {})


def voice_baseline(n: int = 10, start_offset: int = 60, gap: int = 5, **kw) -> list[TaskSessionIn]:
    """``n`` stable vowel sessions (shimmer/NHR/CPPS wobble ±10 %), oldest first."""
    return [voice_vowel(start_offset - i * gap, 0.030 * (1 + 0.1 * VOICE_WOBBLE[i % 10]),
                        0.012 * (1 + 0.1 * VOICE_WOBBLE[i % 10]), 14.0 + 0.3 * VOICE_WOBBLE[i % 10], **kw) for i in range(n)]


def story_baseline(n: int = 10, start_offset: int = 60, gap: int = 5, **kw) -> list[TaskSessionIn]:
    return [voice_story(start_offset - i * gap, 130 + 3 * VOICE_WOBBLE[i % 10], 0.20 + 0.01 * VOICE_WOBBLE[i % 10], **kw)
            for i in range(n)]


#: three adverse sessions after a 10-session baseline (offsets 8, 4, 0): shimmer ×2.5, NHR ×3, CPPS −3.5 dB
ADVERSE_VOWEL = [voice_vowel(o, 0.075, 0.036, 10.5) for o in (8, 4, 0)]
ADVERSE_STORY = [voice_story(o, 95.0, 0.36) for o in (8, 4, 0)]
