"""Apple Health export parser (fixture export.xml + ECG CSV) and the background import route."""

import io
import uuid
import zipfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.routes.auth import create_user
from app.core.auth import create_access_token
from app.db.models import BpReading, DeviceRhythmResult, MetricSample
from app.providers.storage import LocalStorage
from app.services.apple_health import import_apple_health, parse_apple_health_export, parse_ecg_csv

FIX = Path(__file__).parent / "fixtures"


def export_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(FIX / "export.xml", "apple_health_export/export.xml")
        z.write(FIX / "ecg_2026-10-14.csv", "apple_health_export/electrocardiograms/ecg_2026-10-14.csv")
    return buf.getvalue()


def by_key(parsed):
    return {(s.type, s.recorded_at.date().isoformat()): s.value for s in parsed.samples}


def test_parse_daily_aggregation_and_units():
    p = parse_apple_health_export(export_zip())
    v = by_key(p)
    # steps: per-source sums (iPhone 3000, Watch 2500) → max, not 5500
    assert v[("steps", "2026-10-10")] == 3000
    assert v[("steps", "2026-10-11")] == 4000
    assert v[("resting_hr", "2026-10-10")] == 62          # mean of 60, 64
    assert v[("spo2_pct", "2026-10-10")] == 96            # fractions → percent
    assert v[("walking_speed_mps", "2026-10-10")] == pytest.approx(1.0)  # 3.6 km/h
    assert v[("step_length_m", "2026-10-10")] == pytest.approx(0.65)     # 65 cm
    assert v[("distance_m", "2026-10-10")] == 1500
    assert v[("double_support_pct", "2026-10-10")] == pytest.approx(28)
    assert ("heart_rate", "2026-10-10") not in v           # unmapped types ignored
    assert all(s.source == "apple_health" for s in p.samples)


def test_parse_sleep_minutes_per_night():
    v = by_key(parse_apple_health_export(export_zip()))
    night = "2026-10-11"  # wake date
    assert v[("sleep_inbed_minutes", night)] == 480
    assert v[("sleep_asleep_minutes", night)] == 420
    assert v[("sleep_core_minutes", night)] == 300
    assert v[("sleep_deep_minutes", night)] == 60
    assert v[("sleep_rem_minutes", night)] == 60
    assert v[("sleep_awake_count", night)] == 1


def test_parse_bp_pairing_rhythm_and_ecg():
    p = parse_apple_health_export(export_zip())
    assert [(b.systolic, b.diastolic, b.session) for b in p.bp_readings] == [(142, 91, "morning"), (135, 85, "evening")]
    labels = sorted((r.source, r.label) for r in p.rhythm_results)
    assert labels == [("apple_ecg", "afib"), ("apple_irn", "high_hr"), ("apple_irn", "irregular_rhythm_notification")]
    ecg = next(r for r in p.rhythm_results if r.source == "apple_ecg")
    assert ecg.raw_label == "Atrial Fibrillation" and ecg.meta["sample_rate"] == "512 hertz"
    assert "Taro" not in str(ecg.meta)  # name / DOB not kept
    assert [n for n, _ in p.ecg_files] == ["ecg_2026-10-14.csv"]
    assert p.counts["bp_readings"] == 2 and p.counts["ecg_files"] == 1 and p.counts["nights"] == 1


def test_parse_bare_xml_and_since_filter():
    full = by_key(parse_apple_health_export((FIX / "export.xml").read_bytes()))
    assert ("steps", "2026-09-01") in full
    since = by_key(parse_apple_health_export(FIX / "export.xml", since=date(2026, 10, 1)))
    assert ("steps", "2026-09-01") not in since and ("steps", "2026-10-10") in since


def test_parse_ecg_csv_japanese_header():
    data = "記録日,2026-10-14 08:12:30 +0900\n分類,洞調律\nサンプルレート,512ヘルツ\n\n1.0\n".encode()
    ev = parse_ecg_csv(data, "ecg.csv")
    assert ev is not None and ev.label == "sinus_rhythm" and ev.raw_label == "洞調律"


def test_import_persists_and_is_idempotent(db, tmp_path):
    u = create_user(db, f"ah-{uuid.uuid4().hex[:8]}@example.com", "pw123456")
    db.commit()
    storage = LocalStorage(tmp_path)
    r1 = import_apple_health(db, u.id, export_zip(), storage=storage)
    db.commit()
    assert r1["metrics"]["inserted"] == 17 and r1["bp_readings"] == 2 and r1["rhythm_results"] == 3
    r2 = import_apple_health(db, u.id, export_zip(), storage=storage)
    db.commit()
    assert r2["metrics"]["inserted"] == 0 and r2["metrics"]["duplicates"] == 17
    assert len(db.scalars(select(BpReading).where(BpReading.user_id == u.id)).all()) == 2
    rr = db.scalars(select(DeviceRhythmResult).where(DeviceRhythmResult.user_id == u.id)).all()
    assert len(rr) == 3
    ecg = next(r for r in rr if r.source == "apple_ecg")
    assert ecg.file_url and Path(ecg.file_url).read_bytes().startswith(b"Name,")


def test_upload_route_runs_background_job(client, db):
    u = create_user(db, f"ahr-{uuid.uuid4().hex[:8]}@example.com", "pw123456")
    db.commit()
    h = {"Authorization": f"Bearer {create_access_token(u.id)}"}
    res = client.post("/api/uploads/apple-health", headers=h,
                      files={"file": ("export.zip", export_zip(), "application/zip")})
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]
    job = client.get(f"/api/uploads/jobs/{job_id}", headers=h).json()
    assert job["status"] == "done", job
    assert job["result"]["metrics"]["inserted"] == 17
    assert db.scalar(select(MetricSample).where(MetricSample.user_id == u.id, MetricSample.type == "steps"))
    other = create_user(db, f"ahx-{uuid.uuid4().hex[:8]}@example.com", "pw123456")
    db.commit()
    oh = {"Authorization": f"Bearer {create_access_token(other.id)}"}
    assert client.get(f"/api/uploads/jobs/{job_id}", headers=oh).status_code == 404


def test_upload_route_bad_file_marks_job_failed(client, auth_headers):
    res = client.post("/api/uploads/apple-health", headers=auth_headers,
                      files={"file": ("export.zip", b"PK\x03\x04not really a zip", "application/zip")})
    assert res.status_code == 202
    job = client.get(f"/api/uploads/jobs/{res.json()['job_id']}", headers=auth_headers).json()
    assert job["status"] == "failed" and job["error"]
