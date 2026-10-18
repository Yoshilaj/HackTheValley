"""Dashboard / pathway cards (BACKEND_PLAN C8; spec phase-a §11.4).

``pathway_card`` returns exactly the §11.4 JSON keys (``CARD_KEYS``); ``dashboard``
bundles the four cards with metric summaries, pending appointments and
notifications for the Home page. A user without assessments is assessed on
demand so the dashboard never 404s.
"""
from __future__ import annotations

import importlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Appointment, RiskAssessment, User
from app.ml.priority import DISCLAIMER, SCORE_KIND, SCORE_LABEL_I18N
from app.ml.suita import SOURCE_ID as SUITA_SOURCE, SUITA_ELEVATED_PCT
from app.services import notify as notify_svc
from app.services.context_builder import to_local, user_zone
from app.services.risk_service import assess_user, latest_assessments

log = get_logger(__name__)

DASHBOARD_ORDER = ("cardio", "cognitive", "parkinsons", "general")
TREND_WEEKS = 8
CARD_KEYS = ("pathway", "level", "priority_score", "score_kind", "score_label", "headline", "drivers",
             "missing_inputs", "data_quality", "trend", "probabilities", "long_term_risk", "preventive_measures",
             "next_action", "disclaimer")
#: internal bookkeeping stored in RiskAssessment.data_quality, not shown on the card
_DQ_INTERNAL = ("headline", "trigger", "informational", "persistence")
ACTIVE_APPOINTMENT_STATUSES = ("proposed", "awaiting_approval", "processing", "requested", "needs_user_info", "confirmed")
PENDING_APPOINTMENT_STATUSES = ("proposed", "awaiting_approval", "processing", "requested", "needs_user_info")
#: appointment status -> card next_action.type (anything else active -> appointment_requested)
NEXT_ACTION_BY_STATUS = {"confirmed": "appointment_confirmed", "awaiting_approval": "appointment_approval_needed",
                         "needs_user_info": "appointment_needs_info"}


def normalize_language(language: str | None, user: User | None = None) -> str:
    lang = language or (user.profile.language if user is not None and user.profile else None) or "en"
    return "ja" if lang.lower().startswith("ja") else "en"


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_trend(db: Session, user: User, pathway: str, now: datetime | None = None,
                 weeks: int = TREND_WEEKS) -> list[dict[str, Any]]:
    """Exactly ``weeks`` points (oldest first, Monday-dated, local tz): last priority_score of that week or None."""
    tz = user_zone(user.profile)
    now_local = to_local(now or datetime.now(timezone.utc), tz)
    this_week = _monday(now_local.date())
    first = this_week - timedelta(weeks=weeks - 1)
    since = datetime.combine(first, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    rows = db.scalars(select(RiskAssessment).where(
        RiskAssessment.user_id == user.id, RiskAssessment.pathway == pathway, RiskAssessment.created_at >= since)
        .order_by(RiskAssessment.created_at))
    by_week: dict[date, int | None] = {}
    for r in rows:
        by_week[_monday(to_local(r.created_at, tz).date())] = r.priority_score
    return [{"week": (first + timedelta(weeks=i)).isoformat(), "priority_score": by_week.get(first + timedelta(weeks=i))}
            for i in range(weeks)]


def _drivers(a: RiskAssessment) -> list[dict[str, Any]]:
    out = []
    for f in a.fired_rules or []:
        ids = [e.get("ref") for e in f.get("evidence") or [] if e.get("ref")]
        out.append({"rule_id": f.get("id"), "text": f.get("summary_text"), "evidence_ids": list(dict.fromkeys(ids))})
    return out


def _probabilities(a: RiskAssessment) -> list[dict[str, Any]]:
    """Long-term estimate entries as stored (Framingham in Canada / Suita in Japan, with what_if);
    model scores only flagged ``detail_only``."""
    out = []
    for p in a.probabilities or []:
        if p.get("kind") == "experimental_model_score":
            out.append({**p, "detail_only": True})
        else:
            out.append(p)
    return out


def _long_term_risk(a: RiskAssessment) -> dict[str, Any] | None:
    """Generic long-term cardio estimate for the card (``None`` off the cardio pathway / when absent).

    ``{calculator: "framingham_2008"|"suita", name, status, reason, percent_10y, display, category, inputs,
    inputs_used, missing_inputs, what_if: [{change, percent_10y, display, category}], source_id,
    guideline_source_id, note}``. Canada uses Framingham (CCS); the Suita shape is mapped for JP.
    """
    for p in a.probabilities or []:
        if p.get("kind") != "population_estimate":
            continue
        calc = p.get("calculator") or ("suita" if p.get("source_id") == SUITA_SOURCE else None)
        if calc is None:
            continue
        pct = p.get("value_pct")
        if calc == "suita":
            category = None if pct is None else ("elevated" if pct >= SUITA_ELEVATED_PCT else "not_elevated")
            inputs = {"sheet": p.get("sheet"), "points": p.get("points")}
        else:
            category, inputs = p.get("category"), dict(p.get("inputs") or {})
        return {
            "calculator": calc, "name": p.get("name"), "status": p.get("status"), "reason": p.get("reason"),
            "percent_10y": pct, "display": p.get("value_display"), "category": category, "inputs": inputs,
            "inputs_used": list(p.get("inputs_used") or []), "missing_inputs": list(p.get("missing_inputs") or []),
            "what_if": [{"change": w.get("change"), "percent_10y": w.get("value_pct"), "display": w.get("value_display"),
                         "category": w.get("category")} for w in p.get("what_if") or []],
            "source_id": p.get("source_id"), "guideline_source_id": p.get("guideline_source_id"),
            "note": p.get("note"),
        }
    return None


def _preventive(a: RiskAssessment, lang: str) -> list[dict[str, Any]]:
    return [{"text": m.get(f"text_{lang}") or m.get("text_en"), "source_id": m.get("source_id")}
            for m in a.preventive_measures or []]


def _next_action(db: Session, a: RiskAssessment) -> dict[str, Any]:
    appt = db.scalar(
        select(Appointment).join(RiskAssessment, Appointment.assessment_id == RiskAssessment.id)
        .where(Appointment.user_id == a.user_id, RiskAssessment.pathway == a.pathway,
               Appointment.status.in_(ACTIVE_APPOINTMENT_STATUSES))
        .order_by(Appointment.created_at.desc()).limit(1))
    if appt is not None:
        kind = NEXT_ACTION_BY_STATUS.get(appt.status, "appointment_requested")
        return {"type": kind, "appointment_id": appt.id}
    if a.level in ("insufficient_data", "invalid_measurement"):
        return {"type": "collect_data"}
    return {"type": "none"}


def pathway_card(db: Session, user: User, assessment: RiskAssessment, language: str | None = None) -> dict[str, Any]:
    """One §11.4 card. Keys are exactly ``CARD_KEYS``."""
    lang = normalize_language(language, user)
    dq = dict(assessment.data_quality or {})
    headline = dq.get("headline") or {}
    card = {
        "pathway": assessment.pathway,
        "level": assessment.level,
        "priority_score": assessment.priority_score,
        "score_kind": SCORE_KIND,
        "score_label": SCORE_LABEL_I18N[lang],
        "headline": headline.get(lang) or headline.get("en") or "",
        "drivers": _drivers(assessment),
        "missing_inputs": list(assessment.missing_inputs or []),
        "data_quality": {k: v for k, v in dq.items() if k not in _DQ_INTERNAL},
        "trend": weekly_trend(db, user, assessment.pathway),
        "probabilities": _probabilities(assessment),
        "long_term_risk": _long_term_risk(assessment),
        "preventive_measures": _preventive(assessment, lang),
        "next_action": _next_action(db, assessment),
        "disclaimer": DISCLAIMER[lang],
    }
    assert tuple(card) == CARD_KEYS
    return card


def latest_cards(db: Session, user: User, language: str | None = None) -> list[dict[str, Any]]:
    """Cards for the four pathways in ``DASHBOARD_ORDER``; assesses on demand if any is missing."""
    latest = latest_assessments(db, user.id)
    if any(p not in latest for p in DASHBOARD_ORDER):
        assess_user(db, user, trigger="event")
        latest = latest_assessments(db, user.id)
    return [pathway_card(db, user, latest[p], language) for p in DASHBOARD_ORDER if p in latest]


def _metrics_summary(db: Session, user: User) -> dict[str, Any]:
    try:
        mod = importlib.import_module("app.services.ingest_service")
        fn = getattr(mod, "metrics_summary", None)
        return fn(db, user.id) if fn else {}
    except Exception as exc:  # another agent's module: never break the dashboard
        log.warning("metrics_summary unavailable", extra={"error": str(exc)})
        return {}


def _appointment_dict(a: Appointment) -> dict[str, Any]:
    return {"id": a.id, "status": a.status, "assessment_id": a.assessment_id,
            "clinic_name": (a.clinic or {}).get("name"), "chosen_slot": a.chosen_slot,
            "proposed_slots": a.proposed_slots or [], "created_at": a.created_at.isoformat() if a.created_at else None}


def _notification_dict(n) -> dict[str, Any]:
    return {"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "payload": n.payload,
            "read_at": n.read_at.isoformat() if n.read_at else None,
            "created_at": n.created_at.isoformat() if n.created_at else None}


def dashboard(db: Session, user: User, language: str | None = None) -> dict[str, Any]:
    """``{pathways, metrics_summary, appointments, notifications}`` for GET /api/dashboard."""
    pathways = latest_cards(db, user, language)
    appts = db.scalars(select(Appointment).where(Appointment.user_id == user.id,
                                                 Appointment.status.in_(PENDING_APPOINTMENT_STATUSES))
                       .order_by(Appointment.created_at.desc()))
    return {
        "pathways": pathways,
        "metrics_summary": _metrics_summary(db, user),
        "appointments": [_appointment_dict(a) for a in appts],
        "notifications": {"unread": notify_svc.unread_count(db, user.id),
                          "recent": [_notification_dict(n) for n in notify_svc.list_notifications(db, user.id, limit=5)]},
    }
