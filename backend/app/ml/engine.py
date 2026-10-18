"""Rule engine (spec §8, §10).

``evaluate_pathway(ctx)`` runs every rule registered for ``ctx.pathway``
independently and combines them:

* level = max over fired, non-informational rules by
  ``no_finding < watch < followup_recommended < emergency``;
* when nothing fired: ``invalid_measurement`` if a GATE rule reported invalid
  input, else ``insufficient_data`` if a gate rule reported missing input, else
  ``no_finding`` (see ``GATE_RULES``; other rules' missing inputs are listed in
  ``data_quality.missing_optional_inputs``);
* missing/invalid inputs are always reported alongside the level.

Persistence: for each fired rule, the number of consecutive weekly windows
(current one included) in which it fired. Prior window k (k ≥ 1) is
``(as_of − 7(k+1) days, as_of − 7k days]``; the rule counts for that window
if any prior assessment in it lists the rule id.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from app.ml import bp as bpm
from app.ml.context import PathwayContext, PriorAssessment
from app.ml.rules import LEVEL_ORDER, RULES_BY_ID, VOICE_BASELINE_SESSIONS, Rule, RuleOutcome, Scratch, rules_for
from app.ml.framingham import FraminghamResult, framingham_probability_entry, framingham_score
from app.ml.suita import SuitaResult, suita_probability_entry, suita_score

PERSISTENCE_WINDOW = timedelta(days=7)
#: Gate rules (review fix 13): only their missing / invalid inputs can make the pathway
#: ``insufficient_data`` / ``invalid_measurement``. Parkinson's has no single gate: it is
#: insufficient only when every PD rule lacks input (no screener, mobility, tapping or voice).
GATE_RULES: dict[str, frozenset[str]] = {
    "cardio": frozenset({"BP-00", "RHY-02"}),
    "cognitive": frozenset({"COG-01"}),
    "general": frozenset({"GEN-01"}),
}
MAX_PERSISTENCE_LOOKBACK_WINDOWS = 52


@dataclass(frozen=True)
class FiredRule:
    id: str
    version: str
    level: str
    evidence: list[dict[str, Any]]
    summary_text: str
    sources: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class PathwayResult:
    pathway: str
    level: str
    fired_rules: list[FiredRule] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    invalid_inputs: list[str] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)
    probabilities: list[dict[str, Any]] = field(default_factory=list)
    informational: list[dict[str, Any]] = field(default_factory=list)
    persistence: dict[str, int] = field(default_factory=dict)
    user_id: str | None = None
    as_of: datetime | None = None
    outcomes: dict[str, RuleOutcome] = field(default_factory=dict)

    @property
    def fired_rule_ids(self) -> list[str]:
        return [f.id for f in self.fired_rules]


def level_rank(level: str) -> int:
    return LEVEL_ORDER.index(level) if level in LEVEL_ORDER else -1


def prior_rule_ids(p: PriorAssessment) -> set[str]:
    """Accept ``["BP-02"]`` or ``[{"id": "BP-02", ...}]``."""
    out = set()
    for f in p.fired_rules:
        rid = f.get("id") if isinstance(f, dict) else f
        if rid:
            out.add(str(rid))
    return out


def consecutive_windows(rule_id: str, as_of: datetime, priors: Iterable[PriorAssessment]) -> int:
    """Consecutive weekly windows (including the current one) in which ``rule_id`` fired, judged by
    the LATEST prior assessment of each window."""
    priors = [p for p in priors if p.created_at < as_of]
    n = 1
    for k in range(1, MAX_PERSISTENCE_LOOKBACK_WINDOWS + 1):
        hi = as_of - k * PERSISTENCE_WINDOW
        lo = hi - PERSISTENCE_WINDOW
        in_window = [p for p in priors if lo < p.created_at <= hi]
        latest = max(in_window, key=lambda p: p.created_at, default=None)
        if latest is not None and rule_id in prior_rule_ids(latest):
            n += 1
        else:
            break
    return n


def _uniq(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _data_quality(ctx: PathwayContext, sc: Scratch, outcomes: dict[str, RuleOutcome]) -> dict[str, Any]:
    dq: dict[str, Any] = {}
    if ctx.pathway == "cardio":
        w: bpm.BpWindow = sc.cache.get("bpw0") or bpm.bp_window(ctx.bp_readings, ctx.as_of)
        dq.update(valid_bp_days=w.n_days, needed_bp_days=bpm.MIN_WINDOW_DAYS, valid_readings=w.n_valid,
                  invalid_readings=w.n_invalid, morning_days=w.n_morning_days, evening_days=w.n_evening_days,
                  irregular_pulse_flags=w.n_irregular_pulse_flags, rhythm_results=len(ctx.rhythm_results))
    if ctx.pathway == "cognitive":
        b = sc.cache.get("cog_baseline")
        if b:
            dq.update(valid_story_sessions=b["n_valid"], baseline_sessions=len(b["baseline"]),
                      baseline_n_days=len(b["baseline"]))
    if ctx.pathway == "parkinsons":  # PD-03B voice baseline (spec §5.5), like the cognitive counts
        voice = {t: sc.cache.get(f"voice_series_{t}") for t in ("sustained_vowel", "story_recall")}
        if any(voice.values()):
            dq.update(voice_baseline_sessions={t: len(v["baseline"]) for t, v in voice.items() if v},
                      voice_valid_sessions={t: v["n_valid"] for t, v in voice.items() if v},
                      voice_baseline_needed=VOICE_BASELINE_SESSIONS,
                      voice_device=next((v["device"] for v in voice.values() if v and v["device"]), None),
                      voice_sessions_excluded_noise=sum(len(v["excluded_noise"]) for v in voice.values() if v))
    baseline_days = {}
    for o in outcomes.values():
        for name, info in (o.data.get("baseline") or {}).items():
            baseline_days[name] = info["n_baseline_days"]
    if baseline_days:
        dq["baseline_n_days"] = baseline_days
    dq["insufficient_rules"] = [rid for rid, o in outcomes.items() if o.status == "insufficient"
                                and not getattr(RULES_BY_ID.get(rid), "informational", False)]
    dq["invalid_rules"] = [rid for rid, o in outcomes.items() if o.status == "invalid"]
    dq["context_missing_inputs"] = _uniq(m for rid, o in outcomes.items() if getattr(RULES_BY_ID.get(rid), "informational", False)
                                         and o.status == "insufficient" for m in o.missing_inputs)
    return dq


def long_term_calculator(region: str | None) -> str:
    """Long-term cardio context calculator for a region: Suita in Japan, Framingham (CCS) elsewhere."""
    return "suita" if (region or "").upper() == "JP" else "framingham_2008"


def _probabilities(ctx: PathwayContext, sc: Scratch) -> list[dict[str, Any]]:
    probs: list[dict[str, Any]] = []
    if ctx.pathway == "cardio":
        w = sc.cache.get("bpw0") or bpm.bp_window(ctx.bp_readings, ctx.as_of)
        if long_term_calculator(ctx.region) == "suita":
            res: SuitaResult | None = sc.cache.get("suita")
            if res is None:
                res = suita_score(ctx.profile, w.systolic_mean, w.diastolic_mean)
            probs.append(suita_probability_entry(res))
        else:
            frs: FraminghamResult | None = sc.cache.get("framingham")
            if frs is None:
                frs = framingham_score(ctx.profile, w.systolic_mean)
            probs.append(framingham_probability_entry(frs))
    if ctx.pathway == "parkinsons":
        vowel = sorted((s for s in ctx.task_sessions if s.task_type == "sustained_vowel" and s.valid
                        and s.created_at <= ctx.as_of and s.model_scores.get("pd_vowel")), key=lambda s: s.created_at)
        if vowel:
            m = vowel[-1].model_scores["pd_vowel"]
            probs.append({"name": "experimental voice-model score", "kind": "experimental_model_score",
                          "detail_only": True, "value": m.get("probability"), "threshold": m.get("threshold"),
                          "model_version": m.get("version"), "calibration_note": m.get("calibration_note"),
                          "evidence_ref": vowel[-1].id})
    return probs


def evaluate_pathway(ctx: PathwayContext, rules: list[Rule] | None = None) -> PathwayResult:
    """Evaluate all rules for ``ctx.pathway`` and combine them (spec §8)."""
    sc = Scratch()
    outcomes: dict[str, RuleOutcome] = {}
    for rule in rules if rules is not None else rules_for(ctx.pathway, ctx.region):
        o = rule.condition(ctx, sc)
        outcomes[rule.id] = o
        sc.outcomes[rule.id] = o

    fired: list[FiredRule] = []
    informational: list[dict[str, Any]] = []
    missing: list[str] = []
    invalid: list[str] = []
    optional: list[str] = []
    gates = GATE_RULES.get(ctx.pathway)
    if gates is None:  # parkinsons: gate = "no PD input at all"
        scored = [rid for rid in outcomes if not getattr(RULES_BY_ID.get(rid), "informational", False)]
        all_missing = bool(scored) and all(outcomes[rid].status == "insufficient" for rid in scored)
        gates = frozenset(scored) if all_missing else frozenset()
    for rid, o in outcomes.items():
        rule = RULES_BY_ID.get(rid) or next(r for r in (rules or []) if r.id == rid)
        if rule.informational:
            if o.fired:
                informational.append({"rule_id": rid, "kind": "informational_rule", "summary": o.summary,
                                      "note": o.note, "evidence": o.evidence})
            continue
        if o.note:
            entry = {"rule_id": rid, "kind": "note", "summary": o.note, "evidence": o.evidence}
            if o.data.get("message"):  # i18n user-facing text (PD-03B: {"en": {title, body}, "ja": {...}})
                entry["message"] = o.data["message"]
            informational.append(entry)
        if o.fired:
            fired.append(FiredRule(rid, rule.version, o.level or rule.level, o.evidence,
                                   o.summary or rule.rationale, list(rule.sources), rule.rationale))
        elif o.status == "insufficient":
            (missing if rid in gates else optional).extend(o.missing_inputs)
        elif o.status == "invalid":
            (invalid if rid in gates else optional).extend(o.invalid_inputs)

    if fired:
        level = max((f.level for f in fired), key=level_rank)
    elif invalid:
        level = "invalid_measurement"
    elif missing:
        level = "insufficient_data"
    else:
        level = "no_finding"

    persistence = {f.id: consecutive_windows(f.id, ctx.as_of, ctx.prior_assessments) for f in fired}
    dq = _data_quality(ctx, sc, outcomes)
    dq["missing_optional_inputs"] = [m for m in _uniq(optional) if m not in missing and m not in invalid]
    return PathwayResult(
        pathway=ctx.pathway, level=level, fired_rules=fired, missing_inputs=_uniq(missing),
        invalid_inputs=_uniq(invalid), data_quality=dq,
        probabilities=_probabilities(ctx, sc), informational=informational, persistence=persistence,
        user_id=ctx.user_id, as_of=ctx.as_of, outcomes=outcomes,
    )


def evaluate_all(contexts: Iterable[PathwayContext]) -> list[PathwayResult]:
    return [evaluate_pathway(c) for c in contexts]
