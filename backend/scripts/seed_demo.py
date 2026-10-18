"""Planted-trend demo seed (spec phase-a §8 replay; BACKEND_PLAN demo script).

    .venv/bin/python -m scripts.seed_demo            # seeds demo@example.com (no assessment yet)
    .venv/bin/python -m scripts.seed_demo --assess   # + assess_user with report/booking hooks, prints cards

Extends ``scripts.seed.seed_demo_user`` with 35 days of data:
* steps, resting_hr, hrv_sdnn_ms, sleep_asleep_minutes, walking metrics — stable baseline;
* home BP twice daily (2 readings per occasion), stable ~128/80, then rising over the last
  14 days to ~149/94 (7-day mean ≈146/92 → BP-02);
* OMRON Connect "Possible AFib" (possible_af) 3 days ago (→ RHY-01), a palpitations observation;
* a completed PD screener (all "no"), daily fatigue logs;
* **simulated** Parkinson's-pathway trend (every row carries ``raw_json.simulated=True``): 35 daily
  finger_tap sessions, stable then tap count down / interval up over the last 7 days (→ PD-04), plus
  12 story_recall and 12 sustained_vowel sessions over 8 weeks on one device with stable acoustic
  features, the last 3 shifted in the PD direction (shimmer ×2.4, NHR ×3, CPPS −3.5 dB; speech rate
  −25 %, pause ratio ×1.6 — sized to clear the per-feature K of spec §5.5) → PD-03B fires alongside
  PD-04 (parkinsons → watch). Story scores stay stable (cognitive → no finding); the vowel model
  score stays below threshold (display-only);
* two prior weekly cardio RiskAssessments (watch, BP-01) so the trend/persistence shows.

Idempotent: the demo profile (incl. the onboarding identity fields the booking agent may share:
legal name, DOB, sex, address, phone, family doctor, has_referral) is always refreshed in place on an
existing demo user; the planted trend is skipped when the user's ``seed.planted_trend`` marker
(AuditLog) exists.
After seeding, ``assess_user`` yields cardio → followup_recommended [BP-02, RHY-01], score 70 and
parkinsons → watch [PD-04, PD-03B].
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AssessmentTask, AuditLog, BpReading, DailyLog, DeviceRhythmResult, MetricSample, Observation, RiskAssessment,
    ScreenerResponse, User,
)
from app.ml.screener import PD_SCREENER_ITEMS
from app.services.context_builder import user_zone

MARKER_ACTION = "seed.planted_trend"
DAYS = 35
RISE_DAYS = 14
BP_BASE = (128, 80)
BP_PEAK = (149, 94)
SOURCE = "apple_health"

# metric type → (center, wobble amplitude, unit, hour of day)
STABLE_METRICS: dict[str, tuple[float, float, str, int]] = {
    "steps": (6500, 600, "count", 22),
    "resting_hr": (64, 2, "bpm", 8),
    "hrv_sdnn_ms": (38, 3, "ms", 8),
    "sleep_asleep_minutes": (405, 20, "min", 7),
    "walking_speed_mps": (1.18, 0.03, "m/s", 18),
    "step_length_m": (0.66, 0.015, "m", 18),
    "double_support_pct": (28.5, 0.6, "%", 18),
    "walking_asymmetry_pct": (3.0, 0.4, "%", 18),
}
_WOBBLE = (-1.0, 0.0, 1.0, -0.5, 0.5, 0.25, -0.25)
SIMULATED = {"seed": MARKER_ACTION, "simulated": True, "note": "planted demo trend, not a real recording"}
VOICE_DEVICE = "iPhone"
VOICE_SESSION_OFFSETS = tuple(55 - 5 * i for i in range(12))  # 12 sessions over 8 weeks, oldest first
VOICE_ADVERSE_SESSIONS = 3                                     # the last 3 are shifted
TAP_DROP_DAYS = 7
#: sustained-vowel features (UCI keys + CPPS), stable centre and wobble amplitude
VOWEL_FEATURES: dict[str, tuple[float, float]] = {
    "MDVP:Fo(Hz)": (118.0, 2.0), "MDVP:Fhi(Hz)": (131.0, 3.0), "MDVP:Flo(Hz)": (108.0, 2.5),
    "MDVP:Jitter(%)": (0.0042, 0.0005), "MDVP:Jitter(Abs)": (0.000036, 0.000004), "MDVP:RAP": (0.0021, 0.0003),
    "MDVP:PPQ": (0.0023, 0.0003), "Jitter:DDP": (0.0063, 0.0009), "MDVP:Shimmer": (0.030, 0.003),
    "MDVP:Shimmer(dB)": (0.28, 0.03), "Shimmer:APQ3": (0.016, 0.002), "Shimmer:APQ5": (0.019, 0.002),
    "MDVP:APQ": (0.024, 0.003), "Shimmer:DDA": (0.048, 0.006), "NHR": (0.012, 0.0015), "HNR": (22.5, 0.8),
    "RPDE": (0.42, 0.02), "DFA": (0.69, 0.01), "spread1": (-5.8, 0.2), "spread2": (0.20, 0.02),
    "D2": (2.3, 0.1), "PPE": (0.16, 0.015), "CPPS": (14.0, 0.4),
}
#: PD-direction shift of the last VOICE_ADVERSE_SESSIONS vowel sessions (multiplier, or dB offset for CPPS)
VOWEL_SHIFT = {"MDVP:Shimmer": 2.4, "MDVP:Shimmer(dB)": 2.2, "Shimmer:APQ3": 2.4, "Shimmer:APQ5": 2.4,
               "MDVP:APQ": 2.4, "Shimmer:DDA": 2.4, "NHR": 3.0}
CPPS_SHIFT_DB = -3.5
STORY_SPEECH = {"speech_rate_wpm": (132.0, 4.0), "pause_ratio": (0.20, 0.012)}
STORY_SHIFT = {"speech_rate_wpm": 0.75, "pause_ratio": 1.6}


def _wobble(center: float, amp: float, i: int) -> float:
    return center + amp * _WOBBLE[i % len(_WOBBLE)]


def planted_marker(db: Session, user_id: str) -> AuditLog | None:
    return db.scalar(select(AuditLog).where(AuditLog.user_id == user_id, AuditLog.action == MARKER_ACTION).limit(1))


def bp_for_offset(i: int) -> tuple[int, int]:
    """Day offset i (0 = today): stable before RISE_DAYS, then a concave rise to BP_PEAK."""
    if i >= RISE_DAYS - 1:
        return BP_BASE
    frac = math.sqrt((RISE_DAYS - 1 - i) / (RISE_DAYS - 1))
    return (round(BP_BASE[0] + (BP_PEAK[0] - BP_BASE[0]) * frac), round(BP_BASE[1] + (BP_PEAK[1] - BP_BASE[1]) * frac))


def vowel_features_for(i: int, adverse: bool) -> dict[str, float]:
    """Session ``i`` of the planted vowel series; ``adverse`` applies the PD-direction shift (§5.5)."""
    out = {}
    for k, (c, amp) in VOWEL_FEATURES.items():
        v = _wobble(c, amp, i)
        if adverse:
            if k == "CPPS":
                v += CPPS_SHIFT_DB
            else:
                v *= VOWEL_SHIFT.get(k, 1.0)
        out[k] = round(v, 6)
    return out


def story_temporal_for(i: int, adverse: bool) -> dict[str, dict[str, Any]]:
    """``features.temporal`` of a planted story session: three recalls with speech rate / pause ratio."""
    temporal: dict[str, dict[str, Any]] = {}
    for n, label in enumerate(("immediate_1", "immediate_2", "delayed")):
        rate = _wobble(*STORY_SPEECH["speech_rate_wpm"], i + n)
        pause = _wobble(*STORY_SPEECH["pause_ratio"], i + 2 * n)
        if adverse:
            rate *= STORY_SHIFT["speech_rate_wpm"]
            pause *= STORY_SHIFT["pause_ratio"]
        span = 38.0 + n
        temporal[label] = {"timing_source": "word_timestamps", "audio_duration_s": span + 2, "rate_unit": "words",
                           "unit_count": int(rate * span / 60), "speech_span_s": span,
                           "speech_duration_s": round(span * (1 - pause), 2), "n_pauses_gt_200ms": int(8 + 20 * pause),
                           "total_pause_s": round(span * pause, 2), "mean_pause_s": 0.45, "pause_ratio": round(pause, 4),
                           "speech_rate_wpm": round(rate, 1), "speech_rate_cpm": None, "speech_rate_cps": None,
                           "articulation_rate": round(rate / (1 - pause), 1)}
    temporal["session"] = {"delay_s": 180.0}
    return temporal


def tap_features_for(i: int) -> dict[str, Any]:
    """Day offset ``i`` (0 = today): stable tapping, with count down / interval up over the last TAP_DROP_DAYS."""
    drop = i < TAP_DROP_DAYS
    count = _wobble(46 if drop else 62, 1.5, i)
    iti = _wobble(320 if drop else 238, 6, i)
    cv = _wobble(0.21 if drop else 0.12, 0.01, i)
    hands = {h: {"tap_count": round(count + d), "mean_iti_ms": round(iti - 4 * d, 1), "iti_cv": round(cv, 3),
                 "decrement_slope_ms_per_tap": round(0.6 + 0.1 * d, 2), "valid": True} for h, d in (("left", -1), ("right", 1))}
    return {**hands, "valid": True, "asymmetry_count": 2, "tap_count": round(count), "mean_iti_ms": round(iti, 1),
            "iti_cv": round(cv, 3), "decrement_slope_ms_per_tap": 0.6}


def seed_planted_trend(db: Session, user: User, now: datetime | None = None) -> dict[str, Any]:
    """Insert the planted demo trend for ``user`` (idempotent). Commits. Returns counts."""
    if planted_marker(db, user.id) is not None:
        return {"skipped": True}
    tz = user_zone(user.profile)
    now = now or datetime.now(timezone.utc)
    today: date = now.astimezone(tz).date()

    def at(offset: int, hh: int, mm: int = 0) -> datetime:
        return datetime.combine(today - timedelta(days=offset), time(hh, mm), tzinfo=tz).astimezone(timezone.utc)

    counts = {"metrics": 0, "bp": 0, "logs": 0}
    for i in range(DAYS):
        for mtype, (center, amp, unit, hh) in STABLE_METRICS.items():
            t = at(i, hh)
            if t > now:
                continue
            val = _wobble(center, amp, i)
            db.add(MetricSample(user_id=user.id, type=mtype, value=round(val, 3), unit=unit, recorded_at=t,
                                source=SOURCE, meta={"seed": MARKER_ACTION}))
            counts["metrics"] += 1
        sys_, dia = bp_for_offset(i)
        for session, hh, dsys in (("morning", 7, 3), ("evening", 21, -3)):
            for k in range(2):
                t = at(i, hh, k * 2)
                if t > now:
                    continue
                db.add(BpReading(user_id=user.id, session=session, reading_no=k + 1, systolic=sys_ + dsys - k,
                                 diastolic=dia + (1 if dsys > 0 else -1) - k, pulse=70 + k, measured_at=t,
                                 arm="left", position="seated", source="omron_connect"))
                counts["bp"] += 1
        if at(i, 21) <= now or i > 0:
            db.add(DailyLog(user_id=user.id, date=today - timedelta(days=i), fatigue=2 + (i % 3 == 0),
                            sleep_quality=4, mood=4, flags={}, exercise_minutes=30))
            counts["logs"] += 1

    db.add(DeviceRhythmResult(user_id=user.id, source="omron_connect", raw_label="Possible AFib",
                              label="possible_af", recorded_at=at(3, 7, 10), meta={"device": "HCR-7800T"}))
    db.add(Observation(user_id=user.id, kind="symptom", label="palpitations", severity="mild", onset="3 days ago",
                       free_text="Sometimes I feel my heart fluttering", source="chat", created_at=at(2, 12)))
    db.add(ScreenerResponse(user_id=user.id, screener="pd", answers={i.id: False for i in PD_SCREENER_ITEMS},
                            score=0.0, created_at=at(20, 10)))
    # ---- simulated Parkinson's-pathway trend (PD-04 tapping + PD-03B voice; spec §5.3, §5.5)
    for i in range(DAYS):
        t = at(i, 19)
        if t > now:
            continue
        feats = tap_features_for(i)
        db.add(AssessmentTask(user_id=user.id, task_type="finger_tap", protocol_version="tapping-1", language="en",
                              raw_json={**SIMULATED, "duration_s": 15}, features=feats, model_scores={}, score=None,
                              valid=True, created_at=t))
        counts["tapping"] = counts.get("tapping", 0) + 1
    n_sessions = len(VOICE_SESSION_OFFSETS)
    for i, off in enumerate(VOICE_SESSION_OFFSETS):
        adverse = i >= n_sessions - VOICE_ADVERSE_SESSIONS
        score = round(_wobble(6.2, 0.3, i), 2)
        temporal = story_temporal_for(i, adverse)
        rq_audio = {"snr_db": 24 + _wobble(0, 1, i), "duration_s": 40, "clip_frac": 0.0, "ok": True,
                    "noise_floor_dbfs": round(_wobble(-58, 1.5, i), 1), "level_dbfs": -22.0, "clipping_ratio": 0.0}
        db.add(AssessmentTask(user_id=user.id, task_type="story_recall", protocol_version="storyteller-adapted-1",
                              language="en", score=score, valid=True,
                              raw_json={**SIMULATED, "device": VOICE_DEVICE, "story_ids": [f"demo_{i}a", f"demo_{i}b"]},
                              features={"semantic_recall_mean": score, "score_name": "semantic_recall", "status": "ok",
                                        "invalid_reasons": [], "temporal": temporal,
                                        "per_recall": {k: score for k in ("immediate_1", "immediate_2", "delayed")}},
                              recording_quality={**{k: {"audio": rq_audio, "asr_confidence": 0.9}
                                                    for k in ("immediate_1", "immediate_2", "delayed")},
                                                 "device": VOICE_DEVICE},
                              created_at=at(off, 10)))
        prob = round(0.18 + 0.04 * (i % 3), 2)  # always below the 0.6 threshold: v1 score stays display-only
        db.add(AssessmentTask(user_id=user.id, task_type="sustained_vowel", protocol_version="vowel-1", language="en",
                              valid=True, raw_json={**SIMULATED, "repetition_index": 1, "sample_rate": 16000},
                              features=vowel_features_for(i, adverse),
                              model_scores={"pd_vowel": {"probability": prob, "threshold": 0.6,
                                                         "version": "pd_vowel-demo", "calibration_note": "experimental"}},
                              score=prob,
                              recording_quality={"duration_s": 6.0, "voiced_s": 5.2, "snr_db": 26 + _wobble(0, 1, i),
                                                 "clip_frac": 0.0, "ok": True, "reasons": [],
                                                 "noise_floor_dbfs": round(_wobble(-60, 1.5, i), 1), "level_dbfs": -20.0,
                                                 "clipping_ratio": 0.0, "device": VOICE_DEVICE},
                              created_at=at(off, 10, 30)))
        counts["voice_sessions"] = counts.get("voice_sessions", 0) + 2
    base_bp = {"systolic": 136.0, "diastolic": 84.0}
    for off in (14, 7):
        db.add(RiskAssessment(
            user_id=user.id, pathway="cardio", level="watch", priority_score=25 + (10 if off == 7 else 0),
            fired_rules=[{"id": "BP-01", "version": "1", "level": "watch",
                          "summary_text": "7-day home blood pressure averaged 136/84 (at or above 135/85)",
                          "evidence": [{"type": "bp_window", "ref": f"bpw_seed_{off}", "summary": "seeded",
                                        "value": base_bp}],
                          "sources": ["src_hypertension_canada_2020", "src_jsh2019"]}],
            missing_inputs=[], data_quality={"valid_bp_days": 7, "headline": {
                "en": "A change worth keeping an eye on — keep tracking", "ja": "経過を見ていきたい変化があります。記録を続けましょう"}},
            model_versions={"rules": {"BP-01": "1"}}, evidence=[], probabilities=[], preventive_measures=[],
            explanation="Seeded prior assessment (demo).", created_at=now - timedelta(days=off)))
    db.add(AuditLog(user_id=user.id, actor="system", action=MARKER_ACTION, resource=f"user:{user.id}",
                    detail=counts))
    db.commit()
    return {"skipped": False, **counts}


def main(argv: list[str] | None = None) -> None:
    """Seed only (default), so the first ``POST /api/risk/run`` in the demo is the one that newly
    reaches ``followup_recommended`` and triggers report + booking. ``--assess`` also runs
    ``assess_user`` here, with the follow-up hooks registered (report, booking e-mail)."""
    import argparse

    from app.db.session import SessionLocal, init_db
    from app.services.dashboard_service import latest_cards
    from app.services.followup import register_followup_hooks
    from app.services.risk_service import assess_user
    from scripts.seed import DEMO_EMAIL, seed_demo_user

    ap = argparse.ArgumentParser(description="Seed the demo user + planted 35-day trend.")
    ap.add_argument("--assess", action="store_true", help="also run the risk assessment (report + booking)")
    args = ap.parse_args(argv)
    init_db()
    with SessionLocal() as db:
        user = seed_demo_user(db)
        print(f"seeded {DEMO_EMAIL} (password demo1234): {seed_planted_trend(db, user)}  id={user.id}")
        from app.services.booking.common import missing_profile_fields

        print(f"profile refreshed; booking fields missing: {missing_profile_fields(user) or 'none'}")
        if not args.assess:
            print("next: start the API and POST /api/risk/run as the demo user (or rerun with --assess)")
            return
        register_followup_hooks()
        for row in assess_user(db, user, trigger="manual"):
            print(f"  {row.pathway:<11} {row.level:<22} score={row.priority_score} "
                  f"fired={[f['id'] for f in row.fired_rules or []]}")
        print(f"  cards: {len(latest_cards(db, user, 'en'))}")


if __name__ == "__main__":
    main()
