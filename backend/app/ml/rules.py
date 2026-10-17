"""Decision-rule registry (spec §8; BACKEND_PLAN A0/A3).

Every rule is evaluated independently on a ``PathwayContext`` and returns a
``RuleOutcome``. No rule's AND-condition suppresses another rule's finding.
The only cross-rule dependency is PD-03, which by spec (§5.4) can only
*escalate* a voice-model score when another PD rule already fired; it never
hides PD-01/02/04.

Gate rules (BP-00, COG-01, RHY-02) never "fire": they return
``insufficient``/``invalid`` and the engine reports them alongside.
Informational rules (FRS-01, SUITA-01, COG-04) never change the pathway level.

Region: ``Rule.regions`` limits a rule to some ``PathwayContext.region`` values (``rules_for``).
Canada (default) uses FRS-01 (Framingham Risk Score, CCS 2021); SUITA-01 (Japan) is kept but only
registered for region "JP".

Thresholds are module constants with the spec section in a comment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Literal

from app.ml import baseline as bl
from app.ml import bp as bpm
from app.ml.context import BpReadingIn, ObservationIn, PathwayContext, TaskSessionIn
from app.ml.framingham import FRS_ELEVATED_PCT, framingham_score
from app.ml.rhythm import AF_LABELS, CLASSIFIED_LABELS, HR_LABELS, UNCLASSIFIED_LABELS, rhythm_findings
from app.ml.screener import score_pd_screener
from app.ml.suita import SUITA_ELEVATED_PCT, suita_score

LEVEL_ORDER = ("no_finding", "watch", "followup_recommended", "emergency")
LEVELS = ("insufficient_data", "invalid_measurement") + LEVEL_ORDER

# ---------------------------------------------------------------- thresholds
# §2 Home BP
BP_MIN_DAYS = bpm.MIN_WINDOW_DAYS                 # BP-00: ≥5 of 7 days
BP_MORNING_EVENING_DIFF = 15                      # BP-04: morning − evening SBP ≥15
BP_SEVERE_SYS, BP_SEVERE_DIA = 180, 110           # BP-05: ≥180/110 (any valid reading)
BP_REMEASURE_MIN = timedelta(minutes=5)           # BP-05: re-measure after 5 min
BP_REMEASURE_MAX = timedelta(minutes=60)          # decision: re-measure must be within 1 h
BP_SEVERE_LOOKBACK = timedelta(hours=24)          # decision: "any occasion" = last 24 h
BP_SYMPTOM_LABELS = frozenset({                   # BP-05 symptoms (§2)
    "headache", "severe_headache", "chest_pain", "chest_pressure", "dyspnoea", "dyspnea",
    "shortness_of_breath", "breathlessness", "neuro_signs", "face_droop", "arm_weakness",
    "speech_difficulty", "vision_disturbance", "confusion", "sudden_confusion", "numbness",
})
# §4 Rhythm
RHY_LOOKBACK_DAYS = 30                            # decision: RHY-01/03 look-back
RHY02_WINDOW_DAYS = 7                             # RHY-02: "in 7 days"
RHY02_MIN_UNCLASSIFIED = 2                        # RHY-02: ≥2 inconclusive/poor
RHY03_MIN_OCCASIONS = 2                           # RHY-03: ≥2 occasions
# §1.3 Cognitive
COG_BASELINE_SESSIONS = 5                         # COG-01: first 5 valid sessions
COG_BASELINE_MIN_GAP = timedelta(days=3)          # "≥3 days apart"
COG02_RECENT_N, COG02_MIN_HITS, COG02_K = 5, 3, 1.5  # ≥3 of last 5 ≥1.5 MAD below
COG03_LOOKBACK = timedelta(days=30)               # COG-03: within 30 days
COG04_K = 3.0                                     # COG-04: >3 MAD below → re-test
#: COG-03 (spec §1.3) functional-change labels: missed appointments, getting lost, repeating
#: questions, trouble managing money / medication. ``functional_change`` / ``functional_change_reported``
#: are the generic labels of a user-described functional change (check-in free text, chat extraction).
FUNCTIONAL_CHANGE_LABELS = frozenset({
    "missed_appointments", "getting_lost", "repeating_questions", "trouble_managing_money",
    "trouble_managing_medication", "functional_change", "functional_change_reported",
})
#: Decision: these daily check-in yes/no flags are stored as ``kind="functional_change"`` but are
#: symptoms, not functional changes in the spec's sense — a single "yes" must not start the
#: follow-up / booking flow. Sudden confusion is handled by the emergency detector.
COG03_EXCLUDED_LABELS = frozenset({"memory_lapse", "confusion"})
# §5 Parkinson's
PD01_LOOKBACK = timedelta(days=90)                # "last 3 months"
PD02_METRICS = (("walking_speed", "down"), ("step_length", "down"),
                ("double_support_pct", "up"), ("walking_asymmetry_pct", "up"))
PD04_METRICS = (("tap_count", "down"), ("tap_interval_mean", "up"), ("tap_interval_cv", "up"))
PD_BASELINE = dict(baseline_days=30, recent_days=7, min_baseline_days=14, k=1.5, min_recent_hits=5)  # §5.2/§6
PD03_MIN_SESSIONS, PD03_MIN_SPAN = 2, timedelta(days=7)  # ≥2 sessions ≥7 days apart
PD03_LOOKBACK = timedelta(days=90)                # decision
PD_VOWEL_MODEL_KEY = "pd_vowel"
# §6 General
GEN01_METRICS = (("sleep_duration", "down"), ("fatigue", "up"))
GEN_BASELINE = dict(baseline_days=30, recent_days=7, min_baseline_days=14, k=1.5, min_recent_hits=5)
# §7 Emergency
EMG_LOOKBACK = timedelta(hours=24)                # decision: red flag reported in last 24 h
RED_FLAG_LABELS = frozenset({
    "chest_pain", "chest_pressure", "face_droop", "arm_weakness", "speech_difficulty",
    "fainting", "syncope", "severe_sudden_headache", "suicidal_ideation", "vision_disturbance",
    "shortness_of_breath", "sudden_confusion", "loss_of_balance",  # guardrails.RED_FLAG_LABELS additions
})

# --------------------------------------------------------------- data types
Status = Literal["fired", "not_fired", "insufficient", "invalid", "not_applicable"]


@dataclass
class RuleOutcome:
    """Result of one rule's condition.

    ``level`` overrides the rule's default level (used by BP-05, whose spec
    has a symptomatic → emergency and an asymptomatic → follow-up branch).
    ``note`` is surfaced as informational even when the rule did not fire.
    """

    status: Status
    evidence: list[dict[str, Any]] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    invalid_inputs: list[str] = field(default_factory=list)
    summary: str | None = None
    level: str | None = None
    note: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return self.status == "fired"


@dataclass
class Scratch:
    """Per-evaluation cache shared by rules of one pathway."""

    outcomes: dict[str, RuleOutcome] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)

    def memo(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]


Condition = Callable[[PathwayContext, Scratch], RuleOutcome]


@dataclass(frozen=True)
class Rule:
    id: str
    pathway: str
    version: str
    inputs: list[str]
    level: str
    rationale: str
    sources: list[str]
    review_status: str
    condition: Condition
    informational: bool = False
    #: ``None`` = all regions; else the ``PathwayContext.region`` values the rule applies to
    regions: tuple[str, ...] | None = None

    def applies_to(self, region: str | None) -> bool:
        return region is None or self.regions is None or region in self.regions


def ev(type_: str, ref: str | None, summary: str, value: Any = None) -> dict[str, Any]:
    """Evidence item ``{type, ref, summary, value}``."""
    return {"type": type_, "ref": ref, "summary": summary, "value": value}


def _fmt_bp(s: float | None, d: float | None) -> str:
    return f"{s:.0f}/{d:.0f}" if s is not None and d is not None else "n/a"


# ------------------------------------------------------------------ BP rules
def _window(ctx: PathwayContext, sc: Scratch, offset_days: int = 0) -> bpm.BpWindow:
    as_of = ctx.as_of - timedelta(days=offset_days)
    return sc.memo(f"bpw{offset_days}", lambda: bpm.bp_window(ctx.bp_readings, as_of))


def _grade(s: float, d: float) -> int:
    """0 below 135/85, 1 Grade I (≥135 or ≥85), 2 Grade II+ (≥145 or ≥90). Verified cutoffs only."""
    if s >= bpm.GRADE_II_SYS or d >= bpm.GRADE_II_DIA:
        return 2
    if s >= bpm.HOME_HTN_SYS or d >= bpm.HOME_HTN_DIA:
        return 1
    return 0


def _window_ev(w: bpm.BpWindow) -> dict[str, Any]:
    return ev("bp_window", w.ref, f"7-day home blood pressure averaged {_fmt_bp(w.systolic_mean, w.diastolic_mean)} "
              f"over {w.n_days} days", {"systolic": w.systolic_mean, "diastolic": w.diastolic_mean,
                                        "n_days": w.n_days, "start": str(w.start), "end": str(w.end)})


def _bp_insufficient(w: bpm.BpWindow) -> RuleOutcome:
    return RuleOutcome("insufficient", missing_inputs=["bp_home_readings"],
                       summary=f"{w.n_days} of {BP_MIN_DAYS} BP days recorded",
                       data={"n_days": w.n_days, "needed_days": BP_MIN_DAYS})


def bp00(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    w = _window(ctx, sc)
    return _bp_insufficient(w) if w.n_days < BP_MIN_DAYS else RuleOutcome("not_fired")


def _prev_grade(ctx: PathwayContext, sc: Scratch) -> tuple[int | None, bpm.BpWindow]:
    pw = _window(ctx, sc, offset_days=bpm.WINDOW_DAYS)
    if not pw.sufficient:
        return None, pw
    return _grade(pw.systolic_mean, pw.diastolic_mean), pw  # type: ignore[arg-type]


def bp01(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    """Grade I on the current window, not yet sustained (sustained → BP-02).

    Decision: BP-01 and BP-02's "Grade I sustained over two windows" branch
    are the same finding at two durations; BP-01 is reported until it
    escalates so the finding is not double-counted in the priority score.
    """
    w = _window(ctx, sc)
    if not w.sufficient:
        return _bp_insufficient(w)
    if _grade(w.systolic_mean, w.diastolic_mean) != 1:  # type: ignore[arg-type]
        return RuleOutcome("not_fired")
    pg, _ = _prev_grade(ctx, sc)
    if pg is not None and pg >= 1:
        return RuleOutcome("not_fired", note="Grade I sustained over two windows — reported under BP-02")
    return RuleOutcome("fired", [_window_ev(w)], summary=f"7-day home blood pressure averaged "
                       f"{_fmt_bp(w.systolic_mean, w.diastolic_mean)} (at or above 135/85)")


def bp02(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    w = _window(ctx, sc)
    if not w.sufficient:
        return _bp_insufficient(w)
    g = _grade(w.systolic_mean, w.diastolic_mean)  # type: ignore[arg-type]
    if g == 2:
        return RuleOutcome("fired", [_window_ev(w)], summary=f"7-day home blood pressure averaged "
                           f"{_fmt_bp(w.systolic_mean, w.diastolic_mean)} (at or above 145/90)")
    if g == 1:
        pg, pw = _prev_grade(ctx, sc)
        if pg is not None and pg >= 1:
            return RuleOutcome("fired", [_window_ev(pw), _window_ev(w)],
                               summary="Home blood pressure has been at or above 135/85 for two consecutive weeks")
    return RuleOutcome("not_fired")


def _session_insufficient(w: bpm.BpWindow) -> RuleOutcome | None:
    missing = []
    if w.morning_sys is None:
        missing.append("bp_morning_readings")
    if w.evening_sys is None:
        missing.append("bp_evening_readings")
    if missing:
        return RuleOutcome("insufficient", missing_inputs=missing,
                           data={"n_morning_days": w.n_morning_days, "n_evening_days": w.n_evening_days,
                                 "needed_days": BP_MIN_DAYS})
    return None


def _session_ev(w: bpm.BpWindow) -> dict[str, Any]:
    return ev("bp_window_sessions", w.ref,
              f"Morning mean {_fmt_bp(w.morning_sys, w.morning_dia)}, evening mean {_fmt_bp(w.evening_sys, w.evening_dia)}",
              {"morning_sys": w.morning_sys, "morning_dia": w.morning_dia,
               "evening_sys": w.evening_sys, "evening_dia": w.evening_dia})


def bp03(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    w = _window(ctx, sc)
    if (bad := _session_insufficient(w)) is not None:
        return bad
    morning_high = w.morning_sys >= bpm.HOME_HTN_SYS or w.morning_dia >= bpm.HOME_HTN_DIA  # type: ignore[operator]
    evening_ok = w.evening_sys < bpm.HOME_HTN_SYS and w.evening_dia < bpm.HOME_HTN_DIA  # type: ignore[operator]
    if morning_high and evening_ok:
        return RuleOutcome("fired", [_session_ev(w)],
                           summary=f"Morning home blood pressure averaged {_fmt_bp(w.morning_sys, w.morning_dia)} "
                                   f"while evening readings were below 135/85")
    return RuleOutcome("not_fired")


def bp04(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    w = _window(ctx, sc)
    if (bad := _session_insufficient(w)) is not None:
        return bad
    diff = w.morning_sys - w.evening_sys  # type: ignore[operator]
    if diff >= BP_MORNING_EVENING_DIFF:
        return RuleOutcome("fired", [_session_ev(w)], data={"morning_minus_evening_sys": diff},
                           summary=f"Morning systolic pressure was {diff:.0f} mmHg higher than evening on average")
    return RuleOutcome("not_fired", data={"morning_minus_evening_sys": diff})


def _recent(items, attr: str, as_of: datetime, lookback: timedelta):
    return [x for x in items if as_of - lookback < getattr(x, attr) <= as_of]


def _is_severe(s: float, d: float) -> bool:
    return s >= BP_SEVERE_SYS or d >= BP_SEVERE_DIA


def _severe_ev(r: BpReadingIn) -> dict[str, Any]:
    return ev("bp_reading", r.id, f"Blood pressure of {r.systolic}/{r.diastolic} on {r.measured_at.date()} ({r.session})",
              {"systolic": r.systolic, "diastolic": r.diastolic})


def _bp05_assess(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    """BP-05 (spec §2). Decision: triggered by any *valid reading* ≥180/110 in
    the last 24 h, not only by the occasion mean, so a severe reading is not
    diluted when it is averaged with earlier readings of the same session.
    The 5-minute re-measure then decides persistence.

    Each severe reading is its own occasion (evaluated latest first):
    * any severe reading followed 5–60 min later by a still-severe reading →
      ``followup_recommended`` (evidence = that pair). A persistent episode
      inside the 24 h lookback counts even if a later episode came down.
    * otherwise, the latest severe reading has no re-measure yet → ``insufficient``.
    * otherwise every severe reading came down on re-measurement → ``not_fired``.
    """
    def run() -> RuleOutcome:
        readings = _recent(ctx.bp_readings, "measured_at", ctx.as_of, BP_SEVERE_LOOKBACK)
        valid = sorted((r for r in readings if bpm.is_valid_reading(r)), key=lambda r: r.measured_at)
        severe = [r for r in valid if _is_severe(r.systolic, r.diastolic)]
        if not severe:
            return RuleOutcome("not_fired")
        top = max(severe, key=lambda r: (r.systolic, r.diastolic))
        symptoms = [ob for ob in _recent(ctx.observations, "created_at", ctx.as_of, BP_SEVERE_LOOKBACK)
                    if ob.label in BP_SYMPTOM_LABELS]
        if symptoms:
            evidence = [_severe_ev(top)]
            evidence += [ev("observation", ob.id, f"Reported {ob.label.replace('_', ' ')}", ob.label) for ob in symptoms]
            return RuleOutcome("fired", evidence, level="emergency", data={"symptomatic": True},
                               summary=f"Very high blood pressure ({top.systolic}/{top.diastolic}) with symptoms")

        def remeasures(s: BpReadingIn) -> list[BpReadingIn]:
            return [r for r in valid if BP_REMEASURE_MIN <= r.measured_at - s.measured_at <= BP_REMEASURE_MAX]

        for s in reversed(severe):  # latest occasion first
            persistent = [r for r in remeasures(s) if _is_severe(r.systolic, r.diastolic)]
            if persistent:
                r = persistent[-1]
                mins = (r.measured_at - s.measured_at).total_seconds() / 60
                evidence = [_severe_ev(s), ev("bp_reading", r.id, f"Re-measured {r.systolic}/{r.diastolic} after {mins:.0f} minutes",
                                              {"systolic": r.systolic, "diastolic": r.diastolic})]
                return RuleOutcome("fired", evidence, level="followup_recommended", data={"symptomatic": False, "urgent": True},
                                   summary="Very high blood pressure persisted on re-measurement — please contact a doctor promptly")
        latest = severe[-1]
        if not remeasures(latest):
            return RuleOutcome("insufficient", [_severe_ev(latest)], missing_inputs=["bp_remeasure_after_5min"],
                               note="Blood pressure was very high. Rest for 5 minutes and measure again.")
        return RuleOutcome("not_fired", [_severe_ev(top)], note="A very high reading came down on re-measurement.")

    return sc.memo("bp05", run)


def bp05(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    return _bp05_assess(ctx, sc)


def emg02(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    """EMG-02 = BP-05 symptomatic branch (spec §7)."""
    o = _bp05_assess(ctx, sc)
    if o.fired and o.data.get("symptomatic"):
        return RuleOutcome("fired", list(o.evidence), summary=o.summary, data=dict(o.data))
    return RuleOutcome("not_fired")


def suita01(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    """Context only (spec §3): ≥9% → noted for the visit. Never changes level."""
    w = _window(ctx, sc)
    res = sc.memo("suita", lambda: suita_score(ctx.profile, w.systolic_mean, w.diastolic_mean))
    if res.status == "not_applicable":
        return RuleOutcome("not_applicable", data={"reason": res.reason})
    if res.status == "insufficient_data":
        return RuleOutcome("insufficient", missing_inputs=list(res.missing_inputs),
                           note="Lipid / kidney values needed for the long-term risk estimate; ask your doctor.")
    e = [ev("suita_score", "src_suita2014", f"Suita score {res.points} points → 10-year CHD risk {res.probability_display}%",
            {"points": res.points, "probability_pct": res.probability_pct, "sheet": res.sheet})]
    if res.probability_pct is not None and res.probability_pct >= SUITA_ELEVATED_PCT:
        return RuleOutcome("fired", e, summary=f"Elevated long-term coronary risk estimate "
                           f"({res.probability_display}% over 10 years) — discuss at the visit")
    return RuleOutcome("not_fired", e)


def frs01(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    """Context only (CCS 2021): Framingham 10-year CVD risk ≥10% (intermediate/high) is noted for the
    visit. Never changes the level."""
    if ctx.region == "JP":
        return RuleOutcome("not_applicable", data={"reason": "region_uses_suita"})
    w = _window(ctx, sc)
    res = sc.memo("framingham", lambda: framingham_score(ctx.profile, w.systolic_mean))
    if res.status == "not_applicable":
        return RuleOutcome("not_applicable", data={"reason": res.reason})
    if res.status == "insufficient_data":
        return RuleOutcome("insufficient", missing_inputs=list(res.missing_inputs),
                           note="Cholesterol values (total and HDL) and blood-pressure treatment status are needed "
                                "for the long-term heart-risk estimate; ask your doctor.")
    e = [ev("framingham_risk", "src_framingham2008",
            f"Framingham Risk Score → 10-year cardiovascular disease risk {res.display}% ({res.category})",
            {"percent_10y": res.percent_10y, "base_percent_10y": res.base_percent_10y, "category": res.category,
             "family_history_doubled": res.family_history_doubled})]
    if res.percent_10y is not None and res.percent_10y >= FRS_ELEVATED_PCT:
        return RuleOutcome("fired", e, summary=f"{res.category.capitalize()} long-term cardiovascular risk estimate "
                           f"({res.display}% over 10 years, Framingham) — discuss at the visit")
    return RuleOutcome("not_fired", e)


# ---------------------------------------------------------------- rhythm
def _rf(ctx: PathwayContext, sc: Scratch):
    return sc.memo("rhythm", lambda: rhythm_findings(ctx.rhythm_results, ctx.profile, ctx.as_of, RHY_LOOKBACK_DAYS))


def _rhythm_ev(f) -> dict[str, Any]:
    return ev("device_rhythm_result", f.ref,
              f"{f.result.source} recorded '{f.result.label}' on {f.result.recorded_at.date()}", f.label)


def rhy01(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    rf = _rf(ctx, sc)
    hits = rf.with_labels(AF_LABELS)
    if not hits:
        return RuleOutcome("not_fired")
    if ctx.profile.known_af:
        return RuleOutcome("not_fired", [_rhythm_ev(f) for f in hits],
                           note="Known AF diagnosis: rhythm results are logged for the report.")
    return RuleOutcome("fired", [_rhythm_ev(f) for f in hits],
                       summary=f"A device recorded a possible atrial-fibrillation / irregular-rhythm result "
                               f"on {hits[0].result.recorded_at.date()}")


def rhy02(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    rf = _rf(ctx, sc)
    since = ctx.as_of - timedelta(days=RHY02_WINDOW_DAYS)
    unclassified = rf.with_labels(UNCLASSIFIED_LABELS, since)
    classified = rf.with_labels(CLASSIFIED_LABELS, since)
    if len(unclassified) >= RHY02_MIN_UNCLASSIFIED and not classified:
        return RuleOutcome("invalid", [_rhythm_ev(f) for f in unclassified], invalid_inputs=["rhythm_recording"],
                           note="Recent ECG recordings could not be classified. Sit still, rest your arm on a "
                                "table, keep the watch snug, and record again.")
    return RuleOutcome("not_fired")


def rhy03(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    hits = _rf(ctx, sc).with_labels(HR_LABELS)
    if len(hits) >= RHY03_MIN_OCCASIONS:
        return RuleOutcome("fired", [_rhythm_ev(f) for f in hits],
                           summary=f"High or low heart-rate results on {len(hits)} occasions")
    return RuleOutcome("not_fired")


# ------------------------------------------------------------- cognitive
def _story_sessions(ctx: PathwayContext) -> list[TaskSessionIn]:
    return sorted((s for s in ctx.task_sessions if s.task_type == "story_recall" and s.valid
                   and s.score is not None and s.created_at <= ctx.as_of), key=lambda s: s.created_at)


def _cog_baseline(ctx: PathwayContext, sc: Scratch) -> dict[str, Any]:
    """First 5 valid sessions ≥3 days apart form the baseline (spec §1.3)."""
    def run() -> dict[str, Any]:
        sessions = _story_sessions(ctx)
        base: list[TaskSessionIn] = []
        for s in sessions:
            if len(base) >= COG_BASELINE_SESSIONS:
                break
            if not base or s.created_at - base[-1].created_at >= COG_BASELINE_MIN_GAP:
                base.append(s)
        out: dict[str, Any] = {"baseline": base, "n_valid": len(sessions), "post": []}
        if len(base) == COG_BASELINE_SESSIONS:
            med, mad, scale, note = bl.robust_center_scale(s.score for s in base)  # type: ignore[misc]
            out.update(median=med, mad=mad, scale=scale, note=note,
                       post=[s for s in sessions if s.created_at > base[-1].created_at])
        return out
    return sc.memo("cog_baseline", run)


def _cog_insufficient(b: dict[str, Any]) -> RuleOutcome:
    return RuleOutcome("insufficient", missing_inputs=["story_recall_sessions"],
                       summary=f"{len(b['baseline'])} of {COG_BASELINE_SESSIONS} baseline story sessions completed",
                       data={"n_baseline": len(b["baseline"]), "needed": COG_BASELINE_SESSIONS})


def cog01(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    b = _cog_baseline(ctx, sc)
    return _cog_insufficient(b) if "median" not in b else RuleOutcome("not_fired")


def _story_ev(s: TaskSessionIn) -> dict[str, Any]:
    return ev("assessment_task", s.id, f"Story task score {s.score:.1f} on {s.created_at.date()}", s.score)


def cog02(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    b = _cog_baseline(ctx, sc)
    if "median" not in b:
        return _cog_insufficient(b)
    recent = b["post"][-COG02_RECENT_N:]
    if len(recent) < COG02_MIN_HITS:
        return RuleOutcome("insufficient", missing_inputs=["story_recall_followup_sessions"],
                           data={"n_recent": len(recent), "needed": COG02_MIN_HITS})
    low = [s for s in recent if bl.is_adverse(s.score, b["median"], b["scale"], COG02_K, "down")]
    if len(low) >= COG02_MIN_HITS:
        return RuleOutcome("fired", [_story_ev(s) for s in low],
                           data={"baseline_median": b["median"], "mad": b["mad"]},
                           summary="Performance on your story task has been lower than your usual range")
    return RuleOutcome("not_fired")


def _obs_matching(ctx: PathwayContext, lookback: timedelta, pred: Callable[[ObservationIn], bool]) -> list[ObservationIn]:
    return [o for o in _recent(ctx.observations, "created_at", ctx.as_of, lookback) if pred(o)]


def _is_functional_change(o: ObservationIn) -> bool:
    """COG-03 match (spec §1.3): a spec-listed label, or any ``functional_change`` observation
    (user/family-described) other than the excluded check-in symptom flags."""
    if o.label in COG03_EXCLUDED_LABELS:
        return False
    return o.label in FUNCTIONAL_CHANGE_LABELS or o.kind == "functional_change"


def cog03(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    obs = _obs_matching(ctx, COG03_LOOKBACK, _is_functional_change)
    if not obs:
        return RuleOutcome("not_fired")
    return RuleOutcome("fired", [ev("observation", o.id, f"Reported: {o.label.replace('_', ' ')}", o.label) for o in obs],
                       summary="A change in everyday functioning was reported in the last 30 days")


def cog04(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    """Informational: one session far below baseline → re-test prompt, not a flag."""
    b = _cog_baseline(ctx, sc)
    if "median" not in b:
        return RuleOutcome("insufficient", missing_inputs=["story_recall_sessions"])
    if not b["post"]:
        return RuleOutcome("not_fired")
    last = b["post"][-1]
    if b["scale"] > 0 and last.score < b["median"] - COG04_K * b["scale"]:
        return RuleOutcome("fired", [_story_ev(last)], note="retest",
                           summary="Your last story task was well below your usual range — please try it again another day")
    return RuleOutcome("not_fired")


# --------------------------------------------------------------- Parkinson's
def pd01(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    recent = sorted(_recent([s for s in ctx.screeners if s.screener == "pd"], "created_at", ctx.as_of, PD01_LOOKBACK),
                    key=lambda s: s.created_at)
    if not recent:
        return RuleOutcome("insufficient", missing_inputs=["pd_screener"])
    s = recent[-1]
    motor, nonmotor, fired = score_pd_screener(s.answers)
    data = {"motor_count": motor, "nonmotor_count": nonmotor}
    if not fired:
        return RuleOutcome("not_fired", data=data)
    return RuleOutcome("fired", [ev("screener_response", s.id, f"Screener: {motor} motor and {nonmotor} non-motor items", data)],
                       data=data | {"schedule_tasks": ["sustained_vowel", "finger_tap"]},
                       summary=f"Screener answers ({motor} movement-related, {nonmotor} other) suggest tracking movement tasks")


def _baseline_rule(ctx: PathwayContext, metrics, params: dict[str, Any], summary: str) -> RuleOutcome:
    results = []
    for name, direction in metrics:
        series = ctx.series.get(name)
        if series is None:
            results.append(bl.BaselineResult("insufficient_data", None, None, name=name, direction=direction))
            continue
        results.append(bl.baseline_change(series, ctx.as_of, direction=direction, **params))
    data = {"baseline": {r.name: {"status": r.status, "n_baseline_days": r.n_baseline_days,
                                  "median": r.baseline_median, "mad": r.mad, "n_hits": r.n_hits} for r in results}}
    missing = [r.name for r in results if r.status == "insufficient_data"]
    changed = [r for r in results if r.status == "change"]
    if changed:
        return RuleOutcome("fired", [ev("daily_series", r.name,
                                        f"{r.name.replace('_', ' ')}: {r.n_hits} of last {len(r.recent_values)} days "
                                        f"{'below' if r.direction == 'down' else 'above'} your usual range",
                                        {"median": r.baseline_median, "mad": r.mad, "recent": r.recent_values})
                                     for r in changed], missing_inputs=missing, data=data, summary=summary)
    if len(missing) == len(results):
        return RuleOutcome("insufficient", missing_inputs=missing, data=data)
    return RuleOutcome("not_fired", missing_inputs=missing, data=data)


def pd02(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    return _baseline_rule(ctx, PD02_METRICS, PD_BASELINE,
                          "Walking measures have shifted from your usual range — a change worth checking")


def pd04(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    return _baseline_rule(ctx, PD04_METRICS, PD_BASELINE,
                          "Finger-tapping results have shifted from your usual range — a change worth checking")


def _pd_vowel_rule_enabled() -> bool:
    from app.core.config import settings  # lazy: rules are importable without app settings

    return bool(settings.pd_vowel_rule_enabled)


def pd03(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    """Voice score ≥ threshold on ≥2 sessions ≥7 days apart AND PD-01/02/04 fired (spec §5.4).

    Fires only when ``settings.pd_vowel_rule_enabled`` is true; otherwise the same condition is
    reported as a display-only note (the served model is unvalidated on app-extracted audio)."""
    scored = []
    for s in _recent(ctx.task_sessions, "created_at", ctx.as_of, PD03_LOOKBACK):
        m = s.model_scores.get(PD_VOWEL_MODEL_KEY) if s.task_type == "sustained_vowel" and s.valid else None
        if m and m.get("probability") is not None and m.get("threshold") is not None:
            scored.append((s, m))
    if not scored:
        return RuleOutcome("insufficient", missing_inputs=["sustained_vowel_sessions"])
    pos = sorted((s for s, m in scored if m["probability"] >= m["threshold"]), key=lambda s: s.created_at)
    voice = len(pos) >= PD03_MIN_SESSIONS and pos[-1].created_at - pos[0].created_at >= PD03_MIN_SPAN
    if not voice:
        return RuleOutcome("not_fired")
    evidence = [ev("assessment_task", s.id, f"Experimental voice-model score above threshold on {s.created_at.date()}",
                   s.model_scores[PD_VOWEL_MODEL_KEY]) for s in pos]
    others = [rid for rid in ("PD-01", "PD-02", "PD-04") if sc.outcomes.get(rid) and sc.outcomes[rid].fired]
    if not others:
        return RuleOutcome("not_fired", evidence,
                           note="Experimental voice-model score was above its threshold; on its own this does not raise the level.")
    if not _pd_vowel_rule_enabled():
        # Display-only until a model validated on app-extracted audio is served (config.pd_vowel_rule_enabled).
        return RuleOutcome("not_fired", evidence, data={"supporting_rules": others, "display_only": True},
                           note="Experimental voice-model score was above its threshold on two sessions; the model is "
                                "not yet validated on app recordings, so this is shown for information only.")
    return RuleOutcome("fired", evidence, data={"supporting_rules": others},
                       summary="Experimental voice-model score was above threshold on two sessions, alongside another finding")


# ------------------------------------------------------------------ general
def gen01(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    return _baseline_rule(ctx, GEN01_METRICS, GEN_BASELINE,
                          "Sleep or fatigue has shifted from your usual range — a change worth checking")


def emg01(ctx: PathwayContext, sc: Scratch) -> RuleOutcome:
    obs = _obs_matching(ctx, EMG_LOOKBACK, lambda o: o.kind == "red_flag" or o.label in RED_FLAG_LABELS)
    if not obs:
        return RuleOutcome("not_fired")
    return RuleOutcome("fired", [ev("observation", o.id, f"Reported: {o.label.replace('_', ' ')}", o.label) for o in obs],
                       summary="A symptom that needs urgent attention was reported")


# ----------------------------------------------------------------- registry
#: Hypertension Canada 2020 is the primary BP source (HBPM series mean ≥135/85, ≥180/110 severe);
#: JSH 2019 stays as the secondary source for the 145/90, morning-hypertension and ME-difference rules.
_HTN = ["src_hypertension_canada_2020", "src_jsh2019"]

RULES: list[Rule] = [
    # cognitive (§1.3)
    Rule("COG-01", "cognitive", "1", ["story_recall_sessions"], "insufficient_data",
         "Personal baseline needs 5 valid sessions ≥3 days apart.", ["src_skirrow2024", "src_skirrow2022"], "draft", cog01),
    Rule("COG-02", "cognitive", "1", ["story_recall_sessions"], "watch",
         "≥3 of the last 5 sessions ≥1.5 MAD below the personal baseline.", ["src_skirrow2024", "src_skirrow2022"], "draft", cog02),
    Rule("COG-03", "cognitive", "1", ["observations"], "followup_recommended",
         "Reported functional change (within 30 days) warrants follow-up regardless of task score.", ["src_skirrow2024"], "draft", cog03),
    Rule("COG-04", "cognitive", "1", ["story_recall_sessions"], "no_finding",
         "Single session >3 MAD below baseline with good recording quality → re-test prompt, not a flag.",
         ["src_skirrow2024"], "draft", cog04, informational=True),
    # cardio — home BP (§2)
    Rule("BP-00", "cardio", "1", ["bp_readings"], "insufficient_data",
         "Home BP series: morning and evening readings for 7 days (Hypertension Canada); evaluated on ≥5 of 7 days "
         "(JSH 2019 minimum).", _HTN, "draft", bp00),
    Rule("BP-01", "cardio", "1", ["bp_readings"], "watch",
         "7-day home mean at or above the Hypertension Canada home threshold (≥135 systolic or ≥85 diastolic), "
         "below 145/90.", _HTN, "draft", bp01),
    Rule("BP-02", "cardio", "1", ["bp_readings"], "followup_recommended",
         "7-day home mean ≥135/85 (Hypertension Canada HBPM diagnostic threshold) in two consecutive 7-day windows, "
         "or ≥145/90 in one window (JSH 2019 home grade II).", _HTN, "draft", bp02),
    Rule("BP-03", "cardio", "1", ["bp_readings"], "watch",
         "Morning hypertension: morning mean ≥135/85 with evening mean <135/85 (JSH 2019; 135/85 = Hypertension "
         "Canada home threshold).", _HTN, "draft", bp03),
    Rule("BP-04", "cardio", "1", ["bp_readings"], "watch",
         "Morning − evening systolic ≥15 mmHg is an independent risk factor (JSH 2019).", _HTN, "draft", bp04),
    Rule("BP-05", "cardio", "1", ["bp_readings", "observations"], "followup_recommended",
         "Occasion ≥180/110 (Hypertension Canada: mean ≥180/110 = hypertension at once): with symptoms → emergency; "
         "without symptoms, persistent after 5-min re-measure → urgent follow-up.", _HTN, "draft", bp05),
    Rule("EMG-02", "cardio", "1", ["bp_readings", "observations"], "emergency",
         "= BP-05 symptomatic branch.", _HTN, "draft", emg02),
    Rule("FRS-01", "cardio", "1", ["profile", "bp_readings"], "no_finding",
         "Framingham 10-year CVD risk (D'Agostino 2008, CCS 2021 modified for family history) ≥10% — CCS "
         "intermediate/high — noted as elevated long-term risk (context only).",
         ["src_framingham2008", "src_ccs_lipids_2021"], "draft", frs01, informational=True),
    Rule("SUITA-01", "cardio", "1", ["profile", "bp_readings"], "no_finding",
         "Suita 10-year CHD risk ≥9% noted as elevated long-term risk (context only; Japan only).", ["src_suita2014"],
         "draft", suita01, informational=True, regions=("JP",)),
    # cardio — rhythm (§4)
    Rule("RHY-01", "cardio", "1", ["rhythm_results", "profile.known_af"], "followup_recommended",
         "Possible AF / AFib / irregular rhythm notification without a known AF diagnosis (device instruction: talk to your doctor).",
         ["src_apple_irn", "src_apple_ecg", "src_omron_connect", "src_ccs_af_2020"], "draft", rhy01),
    Rule("RHY-02", "cardio", "1", ["rhythm_results"], "invalid_measurement",
         "≥2 inconclusive/poor recordings in 7 days and no classified result.", ["src_apple_ecg"], "draft", rhy02),
    Rule("RHY-03", "cardio", "1", ["rhythm_results"], "watch",
         "High HR or low HR result on ≥2 occasions.", ["src_apple_ecg"], "draft", rhy03),
    # Parkinson's (§5) — PD-03 must come after PD-01/02/04
    Rule("PD-01", "parkinsons", "1", ["screeners"], "watch",
         "≥2 motor items, or 1 motor + ≥1 non-motor item.", ["src_strivepd"], "draft", pd01),
    Rule("PD-02", "parkinsons", "1", [m for m, _ in PD02_METRICS], "watch",
         "Gait metric ≥1.5 MAD from 30-day baseline on ≥5 of last 7 days.", ["src_hearo"], "draft", pd02),
    Rule("PD-04", "parkinsons", "1", [m for m, _ in PD04_METRICS], "watch",
         "Finger-tapping metric ≥1.5 MAD from baseline on ≥5 of last 7 days (as PD-02).", ["src_hearo"], "draft", pd04),
    Rule("PD-03", "parkinsons", "1", ["task_sessions.sustained_vowel"], "watch",
         "Voice-model score ≥ threshold on ≥2 sessions ≥7 days apart and PD-01/02/04 fired; voice alone never raises the level.",
         ["src_little2008"], "draft", pd03),
    # general (§6, §7)
    Rule("GEN-01", "general", "1", [m for m, _ in GEN01_METRICS], "watch",
         "Sleep duration or fatigue ≥1.5 MAD from baseline on ≥5 of last 7 days.", ["src_hearo"], "draft", gen01),
    Rule("EMG-01", "general", "1", ["observations"], "emergency",
         "Red-flag symptom (chest pain, FAST signs, fainting, severe sudden headache, suicidal ideation).", [], "draft", emg01),
]
RULES_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}


def rules_for(pathway: str, region: str | None = None) -> list[Rule]:
    """Rules of ``pathway``; with ``region``, only those that apply there (``Rule.regions``)."""
    return [r for r in RULES if r.pathway == pathway and r.applies_to(region)]
