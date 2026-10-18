"""C1 ingestion: metrics upsert, BP validity, daily log → observations, rhythm labels, summary."""

import uuid
from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api.routes.auth import create_user
from app.core.auth import create_access_token
from app.db.models import AuditLog, DeviceRhythmResult, MetricSample, Observation
from app.schemas.metrics import BpIn, DailyLogIn, MetricIn
from app.services import ingest_service as svc

EDT = timezone(timedelta(hours=-4))  # America/Toronto in October
TOR = ZoneInfo("America/Toronto")


@pytest.fixture()
def user(db):
    u = create_user(db, f"ingest-{uuid.uuid4().hex[:8]}@example.com", "pw123456")
    u.profile.timezone = "America/Toronto"
    db.commit()
    return u


@pytest.fixture()
def headers(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


# ---------------------------------------------------------------- metrics

def test_upsert_idempotent_and_rejections(db, user):
    t = datetime(2026, 10, 10, tzinfo=EDT)
    samples = [
        MetricIn(type="steps", value=8000, recorded_at=t, source="shortcut"),
        MetricIn(type="resting_hr", value=61, recorded_at=t, source="shortcut"),
        MetricIn(type="not_a_metric", value=1, recorded_at=t),
        MetricIn(type="resting_hr", value=500, recorded_at=t),
        MetricIn(type="steps", value=10, recorded_at=t, source="bogus"),
    ]
    r1 = svc.upsert_metrics(db, user.id, samples)
    assert (r1.inserted, r1.duplicates) == (2, 0)
    assert [x["index"] for x in r1.rejected] == [2, 3, 4]
    assert r1.rejected[0]["reason"].startswith("unknown_type")
    assert r1.rejected[1]["reason"].startswith("out_of_range")
    db.commit()

    r2 = svc.upsert_metrics(db, user.id, samples[:2])
    assert (r2.inserted, r2.duplicates, r2.updated) == (0, 2, 0)
    # same key, new value (Shortcut re-posting today's running total) → update
    r3 = svc.upsert_metrics(db, user.id, [MetricIn(type="steps", value=9000, recorded_at=t, source="shortcut")])
    assert (r3.inserted, r3.updated) == (0, 1)
    rows = db.scalars(select(MetricSample).where(MetricSample.user_id == user.id, MetricSample.type == "steps")).all()
    assert len(rows) == 1 and rows[0].value == 9000 and rows[0].unit == "count"


def test_post_metrics_endpoint_and_audit(client, db, user, headers):
    body = {"source": "shortcut", "samples": [
        {"type": "steps", "value": 1234, "recorded_at": "2026-10-16T00:00:00+09:00"},
        {"type": "hrv_sdnn_ms", "value": 40, "recorded_at": "2026-10-16T07:00:00+09:00"},
        {"type": "steps", "value": -1, "recorded_at": "2026-10-16T00:00:00+09:00"},
    ]}
    res = client.post("/api/metrics", json=body, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["inserted"] == 2 and res.json()["rejected"][0]["index"] == 2
    res = client.post("/api/metrics", json=body, headers=headers)
    assert res.json()["duplicates"] == 2
    assert db.scalar(select(AuditLog).where(AuditLog.user_id == user.id, AuditLog.resource == "metric_samples"))


def test_metrics_requires_auth(client):
    assert client.post("/api/metrics", json={"samples": []}).status_code == 401


# ---------------------------------------------------------------- BP

def test_bp_validity_reading_no_and_irregular(db, user):
    m = datetime(2026, 10, 12, 7, 0, tzinfo=EDT)
    rows = svc.add_bp_readings(db, user.id, [
        BpIn(systolic=142, diastolic=91, pulse=72, measured_at=m),
        BpIn(systolic=138, diastolic=88, pulse=70, measured_at=m + timedelta(minutes=2), irregular_pulse_flag=True),
        BpIn(systolic=80, diastolic=90, measured_at=m + timedelta(minutes=4)),
        BpIn(systolic=300, diastolic=90, measured_at=m + timedelta(minutes=5)),
        BpIn(systolic=130, diastolic=80, pulse=20, measured_at=m + timedelta(minutes=6)),
        BpIn(systolic=128, diastolic=80, measured_at=datetime(2026, 10, 12, 22, 0, tzinfo=EDT)),
        BpIn(systolic=128, diastolic=80, measured_at=datetime(2026, 10, 12, 14, 0, tzinfo=EDT)),
    ])
    db.commit()
    assert [r.session for r in rows] == ["morning"] * 5 + ["evening", "other"]
    assert [r.reading_no for r in rows[:5]] == [1, 2, 3, 4, 5]
    assert rows[0].valid and rows[1].valid
    assert (rows[2].valid, rows[2].invalid_reason) == (False, "systolic_not_above_diastolic")
    assert rows[3].invalid_reason == "systolic_out_of_range"
    assert rows[4].invalid_reason == "pulse_out_of_range"
    rr = db.scalars(select(DeviceRhythmResult).where(DeviceRhythmResult.user_id == user.id)).all()
    assert len(rr) == 1
    assert (rr[0].source, rr[0].label) == ("manual_irregular_pulse", "irregular_rhythm_notification")

    # a later reading in the same occasion continues the numbering; duplicates are not re-stored
    more = svc.add_bp_readings(db, user.id, [
        BpIn(systolic=135, diastolic=85, measured_at=m + timedelta(minutes=8)),
        BpIn(systolic=142, diastolic=91, pulse=72, measured_at=m),
    ])
    assert more[0].reading_no == 6 and more[1].id == rows[0].id


def test_bp_endpoints(client, headers):
    res = client.post("/api/metrics/bp", headers=headers, json={"readings": [
        {"systolic": 150, "diastolic": 95, "pulse": 70, "measured_at": "2026-10-16T07:00:00+09:00"},
        {"systolic": 146, "diastolic": 93, "pulse": 71, "measured_at": "2026-10-16T07:02:00+09:00"},
    ]})
    assert res.status_code == 201, res.text
    assert [r["reading_no"] for r in res.json()] == [1, 2]
    res = client.get("/api/metrics/bp?days=30", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body["readings"]) == 2 and "window_7d" in body["summary"]


# ---------------------------------------------------------------- rhythm labels

@pytest.mark.parametrize("source,raw,label", [
    ("apple_ecg", "Sinus Rhythm", "sinus_rhythm"),
    ("apple_ecg", "Atrial Fibrillation", "afib"),
    ("apple_ecg", "High Heart Rate", "high_hr"),
    ("apple_ecg", "Low Heart Rate", "low_hr"),
    ("apple_ecg", "Inconclusive", "inconclusive"),
    ("apple_ecg", "Poor Recording", "poor_recording"),
    ("apple_irn", "Irregular Rhythm", "irregular_rhythm_notification"),
    ("omron_connect", "心房細動の可能性", "possible_af"),
    ("omron_connect", "正常", "sinus_rhythm"),
    ("omron_connect", "謎の表示", "other"),
    ("apple_ecg", "something new", "other"),
])
def test_rhythm_label_normalisation(source, raw, label):
    assert svc.normalize_rhythm_label(source, raw) == label


def test_rhythm_endpoint_keeps_raw_label(client, headers):
    res = client.post("/api/metrics/rhythm", headers=headers, json={
        "source": "omron_connect", "label": "心房細動の可能性", "recorded_at": "2026-10-14T08:00:00+09:00"})
    assert res.status_code == 201, res.text
    assert res.json()["label"] == "possible_af" and res.json()["raw_label"] == "心房細動の可能性"
    assert client.post("/api/metrics/rhythm", headers=headers, json={
        "source": "fitbit", "label": "x", "recorded_at": "2026-10-14T08:00:00+09:00"}).status_code == 422
    listed = client.get("/api/metrics/rhythm?days=3650", headers=headers).json()
    assert any(r["label"] == "possible_af" for r in listed)


# ---------------------------------------------------------------- daily log

def test_daily_log_observations_and_upsert(db, user):
    log = DailyLogIn(date=date(2026, 10, 16), fatigue=4, flags={
        "headache": True, "palpitations": True, "fainting": True,
        "memory_lapse": True, "confusion": True, "tremor": False})
    row, obs = svc.add_daily_log(db, user.id, log)
    db.commit()
    got = {(o.kind, o.label) for o in obs}
    # no red-flag mapping: every unmapped true flag is a plain symptom observation
    assert got == {
        ("symptom", "headache"), ("symptom", "palpitations"), ("symptom", "fainting"),
        ("functional_change", "memory_lapse"), ("functional_change", "confusion"),
    }
    assert all(o.source == "log" and o.source_id == row.id and o.severity is None for o in obs)

    # re-submit the same date with fewer flags → same row, observations re-derived
    row2, obs2 = svc.add_daily_log(db, user.id, DailyLogIn(date=date(2026, 10, 16), flags={"headache": True}))
    db.commit()
    assert row2.id == row.id and row2.fatigue == 4
    stored = db.scalars(select(Observation).where(Observation.user_id == user.id, Observation.source_id == row.id)).all()
    assert [(o.kind, o.label) for o in stored] == [("symptom", "headache")]


def test_daily_log_endpoints(client, headers):
    res = client.post("/api/logs/daily", headers=headers, json={
        "date": date.today().isoformat(), "mood": 3, "flags": {"palpitations": True}})
    assert res.status_code == 200, res.text
    assert res.json()["observations"][0]["kind"] == "symptom" and "emergency_guidance" not in res.json()
    listed = client.get("/api/logs/daily?days=7", headers=headers).json()
    assert listed[0]["observations"][0]["label"] == "palpitations"
    syms = client.get("/api/observations?kind=symptom&days=1", headers=headers).json()
    assert [o["label"] for o in syms] == ["palpitations"]
    # red_flag is no longer an accepted observation kind
    bad = client.post("/api/observations", headers=headers, json={"kind": "red_flag", "label": "chest_pain"})
    assert bad.status_code == 422


def test_observations_crud(client, headers):
    res = client.post("/api/observations", headers=headers, json={"kind": "symptom", "label": "dizziness"})
    assert res.status_code == 201, res.text
    oid = res.json()["id"]
    assert client.post("/api/observations", headers=headers, json={"kind": "bogus", "label": "x"}).status_code == 422
    assert client.delete(f"/api/observations/{oid}", headers=headers).status_code == 204
    assert client.delete(f"/api/observations/{oid}", headers=headers).status_code == 404


# ---------------------------------------------------------------- summary

def test_metrics_summary_shape(client, db, user, headers):
    # Local (Toronto) midnight; the summary is evaluated at local noon so the result does not depend on
    # the wall-clock hour the suite runs at (samples at +1 h…+7 h used to be "in the future" after midnight).
    today = datetime.now(TOR).replace(hour=0, minute=0, second=0, microsecond=0)
    noon = today + timedelta(hours=12)
    samples = []
    for i in range(10):
        d = today - timedelta(days=i)
        samples.append(MetricIn(type="steps", value=5000 + i * 100, recorded_at=d + timedelta(hours=1), source="apple_health"))
        samples.append(MetricIn(type="steps", value=1000, recorded_at=d + timedelta(hours=2), source="apple_health"))
        samples.append(MetricIn(type="resting_hr", value=60 + (i % 3), recorded_at=d + timedelta(hours=7), source="apple_health"))
    svc.upsert_metrics(db, user.id, samples)
    svc.add_bp_readings(db, user.id, [
        BpIn(systolic=140, diastolic=90, measured_at=today + timedelta(hours=7)),
        BpIn(systolic=136, diastolic=88, measured_at=today + timedelta(hours=7, minutes=2)),
        BpIn(systolic=130, diastolic=80, measured_at=today - timedelta(hours=2)),  # yesterday evening
    ])
    db.commit()

    res = client.get("/api/metrics/summary?days=7", headers=headers)
    assert res.status_code == 200, res.text
    assert set(res.json()) >= {"days", "as_of", "metrics", "bp"}
    from fastapi.encoders import jsonable_encoder
    body = jsonable_encoder(svc.metrics_summary(db, user.id, 7, now=noon))
    steps = body["metrics"]["steps"]
    assert steps["aggregation"] == "sum" and steps["unit"] == "count"
    assert steps["n_days"] == 7 and len(steps["daily"]) == 7
    assert steps["daily"][-1]["value"] == 6000  # 5000 + 1000 summed within a day
    assert steps["baseline_30d"]["n_days"] == 10 and steps["baseline_30d"]["median"] > 0
    assert set(steps["baseline_30d"]) == {"median", "mad", "n_days"}
    hr = body["metrics"]["resting_hr"]
    assert hr["aggregation"] == "mean" and "value" in hr["last_value"]
    bp = body["bp"]
    today_entry = next(e for e in bp["per_day"] if e["date"] == today.date().isoformat())
    assert today_entry["morning"] == {"systolic": 138.0, "diastolic": 89.0, "n": 2}
    assert bp["window_7d"]["day_mean"]["n_days"] == 2
    assert bp["window_7d"]["day_mean"]["sufficient"] is False


def test_shortcut_device_token_push(client, user, headers):
    tok = client.post("/api/auth/device-token", headers=headers, json={"label": "test"}).json()["token"]
    res = client.post("/api/metrics", headers={"X-Device-Token": tok}, json={"source": "shortcut", "samples": [
        {"type": "spo2_pct", "value": 97, "recorded_at": "2026-10-17T00:00:00-04:00"}]})
    assert res.status_code == 200 and res.json()["inserted"] == 1
