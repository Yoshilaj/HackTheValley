"""Adaptive daily check-in (BACKEND_PLAN C2 ``GET/POST /api/chat/checkin``). Deterministic, no LLM.

Question selection (in order, each at most once):
1. Missing data today: sleep quality (no sleep metric for last night and not yet logged),
   home BP reminder with the JSH measurement protocol (no valid reading today),
   fatigue / mood (not yet in today's DailyLog).
2. Pathways whose latest RiskAssessment is ``watch`` / ``followup_recommended``:
   cardio → palpitations, dizziness, breathlessness; parkinsons → tremor, stiffness;
   cognitive → memory lapse + free-text functional change.
3. At most one task prompt: sustained vowel if the latest PD screener is positive (PD-01)
   and no vowel task in 7 days, else story recall if none in 7 days.

Submission maps answers onto ``ingest_service.add_daily_log`` (scales → fields, symptom
booleans → flags → Observations) plus a functional-change Observation for free text.
Free text is scanned by the red-flag detector; a hit adds the red-flag flag, triggers
``notify_emergency`` and returns emergency guidance.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AssessmentTask, BpReading, DailyLog, MetricSample, Observation, ScreenerResponse, User
from app.prompts.chat_system import latest_levels
from app.services.guardrails import detect_red_flags

SLEEP_TYPES = ("sleep_asleep_minutes", "sleep_inbed_minutes", "sleep_core_minutes", "sleep_deep_minutes",
               "sleep_rem_minutes")
TASK_LOOKBACK_DAYS = 7
CONCERN_LEVELS = ("watch", "followup_recommended")

#: id → (type, ja, en). ``type`` ∈ scale (1–5) | bool | text.
QUESTIONS: dict[str, tuple[str, str, str]] = {
    "sleep_quality": ("scale", "昨夜の睡眠の質はどうでしたか？(1=とても悪い〜5=とても良い)",
                      "How well did you sleep last night? (1 = very poorly … 5 = very well)"),
    "fatigue": ("scale", "今日の疲れ具合は？(1=全くない〜5=とても強い)", "How tired do you feel today? (1 = not at all … 5 = extremely)"),
    "mood": ("scale", "今日の気分は？(1=とても落ち込んでいる〜5=とても良い)", "How is your mood today? (1 = very low … 5 = very good)"),
    "bp_reminder": ("bool", "", ""),  # text built from the protocol
    "palpitations": ("bool", "今日、動悸(心臓がドキドキ・脈が飛ぶ感じ)はありましたか？",
                     "Did you notice palpitations (racing, pounding or skipped heartbeats) today?"),
    "dizziness": ("bool", "今日、めまいやふらつきはありましたか？", "Did you feel dizzy or light-headed today?"),
    "breathlessness": ("bool", "今日、普段より息切れしやすいと感じましたか？", "Were you more short of breath than usual today?"),
    "tremor": ("bool", "今日、手足のふるえに気づきましたか？", "Did you notice any shaking or tremor in your hands or legs today?"),
    "stiffness": ("bool", "今日、体のこわばりや動きにくさを感じましたか？", "Did you feel stiffness or slowness of movement today?"),
    "memory_lapse": ("bool", "今日、物忘れで困ったことはありましたか？", "Did you have any memory lapses that bothered you today?"),
    "functional_change": ("text", "最近、お金の管理・薬の管理・約束・道順などで以前よりできにくくなったことがあれば教えてください。",
                          "Has anything become harder recently — managing money or medication, appointments, finding your way? Describe it briefly."),
    "task_sustained_vowel": ("bool", "今日「あー」の発声テスト(約10秒)をしますか？",
                             "Would you like to do the sustained 'aah' voice task (about 10 seconds) now?"),
    "task_story_recall": ("bool", "今日、短いお話を聞いて思い出すテスト(約3分)をしますか？",
                          "Would you like to do the short story-recall task (about 3 minutes) now?"),
}

BP_PROTOCOL = {
    "ja": {"morning": "朝: 起床後1時間以内・排尿後・朝食前・服薬前に、座って1〜2分安静にしてから2回測定",
           "evening": "夜: 就寝前に、座って1〜2分安静にしてから2回測定"},
    "en": {"morning": "Morning: within 1 hour of waking, after using the toilet, before breakfast and medication, "
                      "seated after 1–2 minutes' rest — take 2 readings",
           "evening": "Evening: just before bed, seated after 1–2 minutes' rest — take 2 readings"},
}

_REASONS = {
    "sleep_quality": ("昨夜の睡眠データがありません", "No sleep data for last night"),
    "fatigue": ("今日の記録がまだありません", "Today's check-in is not filled in yet"),
    "mood": ("今日の記録がまだありません", "Today's check-in is not filled in yet"),
    "bp_reminder": ("今日の家庭血圧の記録がありません(7日間のうち5日以上必要)", "No home BP today (≥5 of 7 days are needed)"),
    "cardio": ("心臓・血圧の項目が「経過観察」です", "Your heart/BP pathway is on watch"),
    "parkinsons": ("パーキンソン関連の項目が「経過観察」です", "Your movement/voice pathway is on watch"),
    "cognitive": ("認知機能の項目が「経過観察」です", "Your memory pathway is on watch"),
    "task_sustained_vowel": ("スクリーナーで追加の発声テストが推奨されています", "Your screener answers suggest the voice task"),
    "task_story_recall": ("過去7日間にお話の記憶テストをしていません", "No story-recall task in the last 7 days"),
}

PATHWAY_QUESTIONS = {
    "cardio": ("palpitations", "dizziness", "breathlessness"),
    "parkinsons": ("tremor", "stiffness"),
    "cognitive": ("memory_lapse", "functional_change"),
}
SYMPTOM_FLAGS = ("palpitations", "dizziness", "breathlessness", "tremor", "stiffness", "memory_lapse")
SCALE_FIELDS = ("sleep_quality", "fatigue", "mood")
#: guardrail label → DailyLog flag understood by ingest_service.FLAG_OBSERVATIONS
RED_FLAG_TO_LOG_FLAG = {
    "chest_pain": "chest_pain", "chest_pressure": "chest_discomfort", "speech_difficulty": "speech_disturbance",
    "vision_disturbance": "vision_disturbance", "fainting": "fainting", "face_droop": "face_droop",
    "arm_weakness": "arm_weakness", "severe_sudden_headache": "severe_sudden_headache",
    "suicidal_ideation": "suicidal_ideation",
}


def _lang(language: str | None) -> str:
    return "ja" if (language or "").lower().startswith("ja") else "en"


def _q(qid: str, lang: str, reason_key: str | None = None, text: str | None = None, **extra: Any) -> dict[str, Any]:
    typ, ja, en = QUESTIONS[qid]
    r = _REASONS.get(reason_key or qid, ("", ""))
    out = {"id": qid, "text": text or (ja if lang == "ja" else en), "type": typ,
           "reason": r[0] if lang == "ja" else r[1]}
    out.update(extra)
    return out


def _today(db: Session, user: User, now: datetime | None) -> tuple[date, datetime, datetime, Any, datetime]:
    from app.services.ingest_service import as_aware, day_bounds_utc, user_tz

    tz = user_tz(db, user.id)
    now = as_aware(now or datetime.now(timezone.utc), timezone.utc)
    today = now.astimezone(tz).date()
    start, end = day_bounds_utc(today, tz)
    return today, start, end, tz, now


def _pd_screener_positive(db: Session, user_id: str) -> bool:
    row = db.scalars(select(ScreenerResponse).where(ScreenerResponse.user_id == user_id, ScreenerResponse.screener == "pd")
                     .order_by(ScreenerResponse.created_at.desc()).limit(1)).first()
    if not row:
        return False
    try:
        from app.ml.screener import score_pd_screener

        return bool(score_pd_screener(row.answers or {})[2])
    except Exception:
        return False


def _has_task(db: Session, user_id: str, task_type: str, since: datetime) -> bool:
    return db.scalar(select(AssessmentTask.id).where(AssessmentTask.user_id == user_id,
                                                     AssessmentTask.task_type == task_type,
                                                     AssessmentTask.created_at >= since).limit(1)) is not None


def daily_checkin_questions(db: Session, user: User, language: str | None = None,
                            now: datetime | None = None) -> list[dict[str, Any]]:
    """Adaptive questions ``[{id, text, type: scale|bool|text, reason, ...}]`` for today."""
    lang = _lang(language or (user.profile.language if user.profile else None) or "en")
    today, start, end, tz, now = _today(db, user, now)
    qs: list[dict[str, Any]] = []

    log = db.scalar(select(DailyLog).where(DailyLog.user_id == user.id, DailyLog.date == today))
    # last night = from 18:00 yesterday (local) until now
    night_start = start - timedelta(hours=6)
    has_sleep = db.scalar(select(MetricSample.id).where(
        MetricSample.user_id == user.id, MetricSample.type.in_(SLEEP_TYPES),
        MetricSample.recorded_at >= night_start, MetricSample.recorded_at < end).limit(1)) is not None
    if not has_sleep and not (log and log.sleep_quality):
        qs.append(_q("sleep_quality", lang))

    bp_today = db.scalars(select(BpReading.session).where(
        BpReading.user_id == user.id, BpReading.valid.is_(True), BpReading.measured_at >= start,
        BpReading.measured_at < end)).all()
    local_hour = now.astimezone(tz).hour
    if local_hour < 15:  # before mid-afternoon: remind of the whole day's protocol unless morning is done
        sessions = [] if "morning" in bp_today else ["morning", "evening"]
    else:
        sessions = [s for s in ("morning", "evening") if s not in bp_today]
    if sessions:
        proto = BP_PROTOCOL[lang]
        head = ("今日の家庭血圧はまだ記録されていません。測定しましたか？" if lang == "ja"
                else "No home blood pressure recorded today yet. Have you measured it?")
        qs.append(_q("bp_reminder", lang, text=head + "\n" + "\n".join(proto[s] for s in sessions),
                     sessions=sessions))

    for f in ("fatigue", "mood"):
        if not (log and getattr(log, f)):
            qs.append(_q(f, lang))

    try:
        levels = latest_levels(db, user.id)
    except Exception:
        levels = {}
    asked = {q["id"] for q in qs}
    for pathway, qids in PATHWAY_QUESTIONS.items():
        if levels.get(pathway, {}).get("level") in CONCERN_LEVELS:
            for qid in qids:
                if qid not in asked:
                    qs.append(_q(qid, lang, reason_key=pathway, pathway=pathway))
                    asked.add(qid)

    since = now - timedelta(days=TASK_LOOKBACK_DAYS)
    if _pd_screener_positive(db, user.id) and not _has_task(db, user.id, "sustained_vowel", since):
        qs.append(_q("task_sustained_vowel", lang, task="sustained_vowel"))
    elif not _has_task(db, user.id, "story_recall", since):
        qs.append(_q("task_story_recall", lang, task="story_recall"))
    return qs


def _as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("yes", "y", "true", "1", "はい", "あり", "ある"):
            return True
        if s in ("no", "n", "false", "0", "いいえ", "なし", "ない"):
            return False
    return None


def submit_checkin(db: Session, user: User, answers: dict[str, Any], on_date: date | None = None,
                   language: str | None = None) -> dict[str, Any]:
    """Persist answers → DailyLog upsert (+ Observations). Returns
    ``{date, daily_log, observations, red_flags, emergency_guidance, ignored}``. Commits."""
    from app.schemas.metrics import DailyLogIn
    from app.services.ingest_service import add_daily_log

    lang = _lang(language or (user.profile.language if user.profile else None) or "en")
    today, *_ = _today(db, user, None)
    day = on_date or today
    existing = db.scalar(select(DailyLog).where(DailyLog.user_id == user.id, DailyLog.date == day))
    # Only flags answered in THIS submission are sent; earlier same-day flags are merged back in
    # below without re-deriving their observations (review fix 8).
    prev_flags: dict[str, bool] = dict((existing.flags if existing else None) or {})
    flags: dict[str, bool] = {}
    fields: dict[str, Any] = {}
    ignored: list[str] = []
    texts: dict[str, str] = {}

    for qid, value in (answers or {}).items():
        if qid in SCALE_FIELDS:
            try:
                iv = int(value)
            except (TypeError, ValueError):
                ignored.append(qid)
                continue
            if 1 <= iv <= 5:
                fields[qid] = iv
            else:
                ignored.append(qid)
        elif qid in SYMPTOM_FLAGS:
            b = _as_bool(value)
            if b is None:
                ignored.append(qid)
            else:
                flags[qid] = b
        elif qid == "functional_change" or (qid == "notes" and isinstance(value, str)):
            if isinstance(value, str) and value.strip():
                texts[qid] = value.strip()[:2000]
        elif qid in ("bp_reminder", "task_story_recall", "task_sustained_vowel"):
            continue  # acknowledgements only
        else:
            ignored.append(qid)

    red_flags = sorted({lbl for t in texts.values() for lbl in detect_red_flags(t, lang)})
    for lbl in red_flags:
        flags[RED_FLAG_TO_LOG_FLAG[lbl]] = True
    if flags:
        fields["flags"] = {**prev_flags, **flags}
    if texts:
        note = " / ".join(texts.values())
        prev = existing.notes if existing and existing.notes else None
        fields["notes"] = (f"{prev}\n{note}" if prev and note not in prev else (prev or note))[:5000]

    row, obs = add_daily_log(db, user.id, DailyLogIn(date=day, **fields))
    if "functional_change" in texts:
        o = Observation(user_id=user.id, kind="functional_change", label="functional_change_reported",
                        onset=day.isoformat(), free_text=texts["functional_change"], source="log", source_id=row.id)
        db.add(o)
        db.flush()
        obs.append(o)

    from app.services.emergency import handle_red_flags

    red_obs = [o for o in obs if o.kind == "red_flag" and o.label in red_flags]
    guidance = handle_red_flags(db, user, red_flags, source="checkin", source_id=row.id, language=lang,
                                observations=red_obs, extra={"daily_log_id": row.id})
    from app.core.security import audit

    audit(db, user.id, "checkin.submit", f"daily_log:{row.id}",
          {"fields": sorted(k for k in fields if k != "flags"), "flags": sorted(k for k, v in flags.items() if v),
           "red_flags": red_flags})
    db.commit()
    return {"date": day, "daily_log": row, "observations": obs, "red_flags": red_flags,
            "emergency_guidance": guidance, "ignored": ignored}
