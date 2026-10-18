"""Risk assessment service (BACKEND_PLAN C4; spec phase-a §8, §10, §11).

    assess_user(db, user, trigger="manual") -> list[RiskAssessment]   # one row per pathway

Pipeline: ``build_contexts`` → ``evaluate_all`` → per pathway: priority score
(§11.1), headline, preventive measures (§11.3), deterministic explanation →
``RiskAssessment`` row → transition handling:

* level changed vs the previous assessment → ``risk_level_change`` notification;
* newly ``followup_recommended`` → ``followup_recommended`` notification (+email) and,
  with an active ``ai_assessment`` consent, every callable in ``FOLLOWUP_HOOKS``
  (report / booking agent registers itself there);
* ``emergency`` → ``notify_emergency``; ``FOLLOWUP_HOOKS`` are NEVER called (§7) — not for the
  emergency pathway and not for any other pathway in the same run;
* ``insufficient_data`` → ``data_needed`` notification, at most once per 7 days per pathway.

The user-facing level comes only from fired rules (§10.4). No LLM is called here;
``explain_with_llm`` is an optional, separate step.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import audit, has_consent
from app.db.models import PATHWAYS, Notification, RiskAssessment, User
from app.ml.engine import PathwayResult, evaluate_all
from app.ml.priority import DISCLAIMER, headline_for, priority_score
from app.ml.rules import RULES_BY_ID
from app.services import notify as notify_svc
from app.services.context_builder import build_contexts, to_local, user_zone
from app.services.preventive import preventive_measures

log = get_logger(__name__)

TRIGGERS = ("manual", "nightly", "event")
DATA_NEEDED_THROTTLE = timedelta(days=7)

#: Called as ``hook(db, user, assessment)`` when a pathway newly reaches
#: ``followup_recommended`` and the user has an active ``ai_assessment`` consent.
#: Never called for ``emergency``. Hook exceptions are logged and swallowed.
FollowupHook = Callable[[Session, User, RiskAssessment], Any]
FOLLOWUP_HOOKS: list[FollowupHook] = []

PATHWAY_NAMES = {
    "cardio": {"en": "Heart & blood pressure", "ja": "心臓・血圧"},
    "cognitive": {"en": "Memory & thinking", "ja": "記憶・思考"},
    "parkinsons": {"en": "Movement", "ja": "運動機能"},
    "general": {"en": "General wellbeing", "ja": "全般的な体調"},
}

MISSING_INPUT_TEXT = {
    "bp_home_readings": "home blood-pressure readings on at least 5 of the last 7 days",
    "bp_morning_readings": "morning blood-pressure readings",
    "bp_evening_readings": "evening blood-pressure readings",
    "bp_remeasure_after_5min": "a repeat blood-pressure reading after 5 minutes of rest",
    "story_recall_sessions": "story-recall task sessions (5 for a personal baseline)",
    "story_recall_followup_sessions": "more story-recall task sessions after the baseline",
    "pd_screener": "the movement questionnaire",
    "sustained_vowel_sessions": "voice ('aah') recordings",
    "voice_baseline_sessions": "voice recordings on the same device (10 for a personal voice baseline)",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lang(user: User) -> str:
    lang = (user.profile.language if user.profile else None) or "en"
    return "ja" if lang.lower().startswith("ja") else "en"


# ---------------------------------------------------------------- serialisation

def fired_rules_payload(result: PathwayResult) -> list[dict[str, Any]]:
    return [{"id": f.id, "version": f.version, "level": f.level, "summary_text": f.summary_text,
             "evidence": f.evidence, "sources": f.sources, "persistence_windows": result.persistence.get(f.id, 1)}
            for f in result.fired_rules]


def flatten_evidence(result: PathwayResult) -> list[dict[str, Any]]:
    """All evidence items (fired + informational), each tagged with its rule id (§10.5)."""
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for f in result.fired_rules:
        for e in f.evidence:
            key = (f.id, e.get("type"), e.get("ref"))
            if key not in seen:
                seen.add(key)
                out.append({**e, "rule_id": f.id, "sources": f.sources})
    for info in result.informational:
        for e in info.get("evidence") or []:
            key = (info.get("rule_id"), e.get("type"), e.get("ref"))
            if key not in seen:
                seen.add(key)
                out.append({**e, "rule_id": info.get("rule_id"), "informational": True})
    return out


def model_versions(result: PathwayResult, contexts_tasks: list) -> dict[str, Any]:
    """Rule versions for every evaluated rule + model versions seen in task model_scores."""
    rules = {rid: RULES_BY_ID[rid].version for rid in result.outcomes if rid in RULES_BY_ID}
    models: dict[str, str] = {}
    for t in contexts_tasks:
        for name, m in (t.model_scores or {}).items():
            if isinstance(m, dict) and m.get("version"):
                models[name] = str(m["version"])
    return {"rules": rules, "models": models, "engine": "phase-a"}


def _json_safe(obj: Any) -> Any:
    """Make engine output JSON-serialisable (dates, tuples, sets)."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def build_explanation(result: PathwayResult, language: str = "en") -> str:
    """Deterministic template from fired rules' summary_text + missing inputs + disclaimer only."""
    lines: list[str] = []
    if result.level == "emergency":
        lines.append("Urgent: please follow the emergency guidance." if language == "en"
                     else "緊急: 緊急時の案内に従ってください。")
    if result.fired_rules:
        lines.append("What the rules found:" if language == "en" else "該当したルール:")
        lines += [f"- {f.summary_text} [{f.id} v{f.version}]" for f in result.fired_rules]
    else:
        lines.append("No follow-up rule fired." if language == "en" else "該当した確認ルールはありません。")
    if result.missing_inputs:
        lines.append("Still needed:" if language == "en" else "不足しているデータ:")
        lines += [f"- {MISSING_INPUT_TEXT.get(m, m.replace('_', ' '))}" for m in result.missing_inputs]
    if result.invalid_inputs:
        lines.append("Needs re-recording:" if language == "en" else "測り直しが必要:")
        lines += [f"- {m.replace('_', ' ')}" for m in result.invalid_inputs]
    lines.append(DISCLAIMER["en" if language == "en" else "ja"])
    return "\n".join(lines)


def explain_with_llm(assessment: RiskAssessment, llm: Any, language: str = "en") -> str:
    """Optional LLM explanation grounded ONLY in ``assessment.evidence`` (§10, BACKEND_PLAN C4).

    ``llm`` must expose ``complete(prompt: str) -> str`` (or ``generate``). Falls back to
    the stored deterministic explanation on any error. Never changes the level.
    """
    evidence = "\n".join(f"- [{e.get('rule_id')}] {e.get('summary')} (ref {e.get('ref')})"
                         for e in (assessment.evidence or []))
    missing = ", ".join(assessment.missing_inputs or []) or "none"
    prompt = (
        f"Explain to the user, in {'Japanese' if language == 'ja' else 'English'}, in 3-5 plain sentences, "
        f"why the '{assessment.pathway}' pathway is at level '{assessment.level}'. Use ONLY the evidence below; "
        "do not add facts, diagnoses or probabilities. State the limitations: this is not a diagnosis, "
        f"device and home measurements can be wrong, and missing inputs ({missing}) limit the assessment.\n"
        f"Evidence:\n{evidence or '- (none)'}\nDisclaimer to include: {DISCLAIMER['ja' if language == 'ja' else 'en']}"
    )
    try:
        fn = getattr(llm, "complete", None) or getattr(llm, "generate")
        text = fn(prompt)
        return text if isinstance(text, str) and text.strip() else (assessment.explanation or "")
    except Exception as exc:  # pragma: no cover - provider specific
        log.warning("explain_with_llm failed", extra={"error": str(exc)})
        return assessment.explanation or ""


# ---------------------------------------------------------------- queries

def previous_assessment(db: Session, user_id: str, pathway: str, before: datetime | None = None) -> RiskAssessment | None:
    stmt = select(RiskAssessment).where(RiskAssessment.user_id == user_id, RiskAssessment.pathway == pathway)
    if before is not None:
        stmt = stmt.where(RiskAssessment.created_at <= before)
    return db.scalar(stmt.order_by(RiskAssessment.created_at.desc()).limit(1))


def latest_assessments(db: Session, user_id: str) -> dict[str, RiskAssessment]:
    """Most recent RiskAssessment per pathway (pathways with none are omitted)."""
    out: dict[str, RiskAssessment] = {}
    for p in PATHWAYS:
        row = previous_assessment(db, user_id, p)
        if row is not None:
            out[p] = row
    return out


def assessment_history(db: Session, user_id: str, weeks: int = 8, now: datetime | None = None,
                       tz=None) -> list[dict[str, Any]]:
    """Weekly points ``[{week, pathway, level, priority_score, assessment_id}]`` (last assessment of each
    ISO week, Monday-dated), oldest first, ``weeks`` weeks per pathway (weeks without a row omitted)."""
    now = now or _utcnow()
    since = now - timedelta(weeks=weeks)
    rows = list(db.scalars(select(RiskAssessment).where(
        RiskAssessment.user_id == user_id, RiskAssessment.created_at >= since, RiskAssessment.created_at <= now)
        .order_by(RiskAssessment.created_at)))
    by_key: dict[tuple[str, str], RiskAssessment] = {}
    for r in rows:
        local = to_local(r.created_at, tz) if tz else r.created_at
        monday = (local.date() - timedelta(days=local.weekday())).isoformat()
        by_key[(r.pathway, monday)] = r  # later row in the week wins
    return [{"week": wk, "pathway": p, "level": r.level, "priority_score": r.priority_score, "assessment_id": r.id}
            for (p, wk), r in sorted(by_key.items(), key=lambda kv: (kv[0][1], PATHWAYS.index(kv[0][0])
                                                                     if kv[0][0] in PATHWAYS else 99))]


# ---------------------------------------------------------------- transitions

def _data_needed_recent(db: Session, user_id: str, pathway: str, now: datetime) -> bool:
    rows = db.scalars(select(Notification).where(
        Notification.user_id == user_id, Notification.kind == notify_svc.KIND_DATA_NEEDED,
        Notification.created_at >= now - DATA_NEEDED_THROTTLE))
    return any((r.payload or {}).get("pathway") == pathway for r in rows)


def _summary_text(row: RiskAssessment) -> str:
    return "; ".join(f"{f['id']}: {f['summary_text']}" for f in (row.fired_rules or [])) or row.level


def _handle_transition(db: Session, user: User, row: RiskAssessment, prev: RiskAssessment | None,
                       now: datetime, suppress_hooks: bool = False) -> list[str]:
    """Write notifications / call hooks for one pathway; returns the actions taken."""
    lang = _lang(user)
    name = PATHWAY_NAMES.get(row.pathway, {}).get(lang, row.pathway)
    prev_level = prev.level if prev else None
    payload = {"pathway": row.pathway, "assessment_id": row.id, "level": row.level, "previous_level": prev_level,
               "priority_score": row.priority_score}
    actions: list[str] = []

    if row.level == "emergency":
        from app.services.emergency import already_notified, notified_ids

        trigger_ids = sorted({str(e["ref"]) for f in row.fired_rules or [] if f.get("level") == "emergency"
                              for e in f.get("evidence") or [] if e.get("ref")})
        seen = notified_ids(db, user.id, now=now)
        new_ids = set(trigger_ids) - seen
        # §7: notify on the transition into emergency or when new red-flag evidence appears; never
        # repeat for ids already notified within 24 h (chat / check-in / log already notified them).
        if already_notified(db, user.id, trigger_ids, now=now) or (prev_level == "emergency" and not new_ids):
            actions.append("emergency_already_notified")
        else:
            notify_svc.notify_emergency(db, user, {"summary": _summary_text(row),
                                                   "fired_rules": [f["id"] for f in row.fired_rules or []],
                                                   "pathway": row.pathway, "assessment_id": row.id,
                                                   "observation_ids": trigger_ids}, commit=False)
            actions.append("emergency")
        return actions  # never FOLLOWUP_HOOKS, no booking (§7)

    if prev_level is not None and prev_level != row.level:
        title = (f"{name}: 判定が変わりました" if lang == "ja" else f"{name}: assessment level changed")
        notify_svc.notify(db, user.id, notify_svc.KIND_RISK_LEVEL_CHANGE, title, row.explanation,
                          payload=payload, commit=False)
        actions.append("risk_level_change")

    prev_deferred = bool(prev is not None and (prev.data_quality or {}).get("followup_deferred_emergency"))
    newly_followup = row.level == "followup_recommended" and prev_level != "followup_recommended"
    if newly_followup or (row.level == "followup_recommended" and prev_deferred):
        if newly_followup:
            title = (f"{name}: 受診をおすすめします" if lang == "ja" else f"{name}: a check-up is recommended")
            # One e-mail per finding: when the booking agent will produce an approval card in this run,
            # that card's e-mail carries the finding, so this notification stays in-app only.
            will_book = not suppress_hooks and _booking_will_follow(db, user, row)
            notify_svc.notify(db, user.id, notify_svc.KIND_FOLLOWUP_RECOMMENDED, title, row.explanation,
                              payload=payload | ({"email": {"sent": False, "reason": "covered_by_approval_email"}}
                                                 if will_book else {}),
                              email=not will_book, commit=False)
            actions.append("followup_recommended")
        if suppress_hooks:
            # §7: emergency elsewhere in this run → no hooks now; mark the row so the next
            # non-emergency run still runs them (an event run must not swallow the booking).
            row.data_quality = {**(row.data_quality or {}), "followup_deferred_emergency": True}
            actions.append("followup_hooks_suppressed_emergency")
        elif has_consent(db, user.id, "ai_assessment"):
            on_followup_recommended(db, user, row)
            actions.append("followup_hooks")

    # no "more data needed" nagging while another pathway is an emergency (§7: guidance first)
    if row.level == "insufficient_data" and not suppress_hooks and not _data_needed_recent(db, user.id, row.pathway, now):
        title = (f"{name}: データが不足しています" if lang == "ja" else f"{name}: more data needed")
        body = ", ".join(MISSING_INPUT_TEXT.get(m, m) for m in row.missing_inputs or [])
        notify_svc.notify(db, user.id, notify_svc.KIND_DATA_NEEDED, title, body or None,
                          payload=payload | {"missing_inputs": row.missing_inputs or []}, commit=False)
        actions.append("data_needed")
    return actions


def _booking_will_follow(db: Session, user: User, row: RiskAssessment) -> bool:
    """True when the follow-up hooks will run the booking agent for this pathway (consents present,
    no active appointment) — i.e. an `appointment_approval_needed` e-mail is about to be sent."""
    if not FOLLOWUP_HOOKS or not has_consent(db, user.id, "ai_assessment") or not has_consent(db, user.id, "share_with_clinic"):
        return False
    try:
        from app.services.followup import active_appointment_for_pathway

        return active_appointment_for_pathway(db, user.id, row.pathway) is None
    except Exception:  # pragma: no cover - defensive: never block the notification
        return False


def on_followup_recommended(db: Session, user: User, assessment: RiskAssessment) -> None:
    """Run every registered FOLLOWUP_HOOK (report + booking agent). Errors are logged, never raised."""
    for hook in list(FOLLOWUP_HOOKS):
        try:
            hook(db, user, assessment)
        except Exception as exc:
            log.exception("followup hook failed", extra={"hook": getattr(hook, "__name__", repr(hook))})
            audit(db, user.id, "risk.followup_hook_error", f"risk_assessment:{assessment.id}",
                  {"hook": getattr(hook, "__name__", repr(hook)), "error": str(exc)[:500]}, actor="system")


# ---------------------------------------------------------------- main entry

def assess_user(db: Session, user: User, as_of: datetime | None = None, trigger: str = "manual",
                *, commit: bool = True) -> list[RiskAssessment]:
    """Assess all four pathways for ``user`` and persist one RiskAssessment per pathway (§10).

    ``as_of`` defaults to now and is also used as ``created_at`` of the new rows (so replays
    and back-dated seeds are consistent). Returns rows in ``PATHWAYS`` order.
    """
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}")
    now = as_of or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    contexts = build_contexts(db, user, as_of=now)
    results = evaluate_all(contexts.values())
    lang = _lang(user)

    rows: list[RiskAssessment] = []
    summary: dict[str, Any] = {}
    # §7: while any pathway is an emergency, no booking/report hook runs for this user in this run.
    any_emergency = any(r.level == "emergency" for r in results)
    for result in results:
        ctx = contexts[result.pathway]
        on_bp_med = bool(ctx.profile.bp_treated)
        prev = previous_assessment(db, user.id, result.pathway, before=now)
        score = priority_score(result, ctx.prior_assessments)
        headline = headline_for(result)
        dq = dict(result.data_quality)
        dq.update(invalid_inputs=result.invalid_inputs, headline=headline, trigger=trigger,
                  informational=result.informational, persistence=result.persistence)
        row = RiskAssessment(
            user_id=user.id, pathway=result.pathway, level=result.level, priority_score=score,
            fired_rules=_json_safe(fired_rules_payload(result)),
            missing_inputs=list(result.missing_inputs),
            data_quality=_json_safe(dq),
            model_versions=model_versions(result, ctx.task_sessions),
            evidence=_json_safe(flatten_evidence(result)),
            probabilities=_json_safe(result.probabilities),
            preventive_measures=preventive_measures(result.pathway, result.fired_rule_ids, ctx.profile,
                                                    result.probabilities, on_bp_medication=on_bp_med),
            explanation=build_explanation(result, lang),
            created_at=now,
        )
        db.add(row)
        db.flush()
        actions = _handle_transition(db, user, row, prev, now, suppress_hooks=any_emergency)
        summary[result.pathway] = {"level": row.level, "priority_score": score,
                                   "fired": result.fired_rule_ids, "actions": actions}
        rows.append(row)

    audit(db, user.id, "risk.assess", f"user:{user.id}",
          {"trigger": trigger, "as_of": now.isoformat(), "pathways": summary}, actor="system")
    if commit:
        db.commit()
    return rows
