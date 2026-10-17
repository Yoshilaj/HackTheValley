"""Document ingestion with FakeLlm (doctor note PDF, BP photo, ECG, audio)."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.routes.auth import create_user
from app.api.routes.uploads import get_llm
from app.core.auth import create_access_token
from app.db.models import AuditLog, BpReading, DeviceRhythmResult, Observation, Profile
from app.providers.llm import FakeLlm
from app.providers.storage import LocalStorage
from app.services.document_service import ingest_document, match_diagnoses

FIX = Path(__file__).parent / "fixtures"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture()
def user(db):
    u = create_user(db, f"doc-{uuid.uuid4().hex[:8]}@example.com", "pw123456")
    db.commit()
    return u


@pytest.fixture()
def headers(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture()
def fake(app):
    llm = FakeLlm()
    app.dependency_overrides[get_llm] = lambda: llm
    yield llm
    app.dependency_overrides.pop(get_llm, None)


def test_doctor_note_pdf_creates_medication_observations(db, user, tmp_path):
    llm = FakeLlm()
    doc = ingest_document(db, user.id, "doctor_note", "note.pdf", (FIX / "doctor_note.pdf").read_bytes(),
                          "application/pdf", llm, LocalStorage(tmp_path))
    db.commit()
    assert doc.status == "done", doc.extracted
    data = doc.extracted["data"]
    assert [m["name"] for m in data["medications"]] == ["Amlodipine", "Atorvastatin"]
    assert data["medications"][0]["dose"] == "5 mg" and data["medications"][0]["schedule"] == "once daily"
    obs = db.scalars(select(Observation).where(Observation.user_id == user.id, Observation.kind == "medication")).all()
    assert sorted(o.label for o in obs) == ["Amlodipine", "Atorvastatin"]
    assert all(o.source == "document" and o.source_id == doc.id for o in obs)
    prof = db.scalar(select(Profile).where(Profile.user_id == user.id))
    db.refresh(prof)
    assert prof.known_hypertension is True
    assert prof.known_af is None            # "suspected atrial fibrillation" is not a diagnosis
    assert prof.ldl_mg_dl == 152 and prof.hdl_mg_dl == 44
    assert [m["name"] for m in prof.medications] == ["Amlodipine", "Atorvastatin"]
    assert Path(doc.file_url).exists()
    audit = db.scalars(select(AuditLog).where(AuditLog.user_id == user.id, AuditLog.action == "llm_call")).all()
    assert audit and audit[0].detail["purpose"] == "document_extract:doctor_note"
    assert {"model", "input_tokens", "output_tokens"} <= set(audit[0].detail)


def test_match_labs_converts_canadian_mmol_l():
    from app.services.document_service import match_labs

    out = match_labs([{"name": "LDL-C", "value": 3.9, "unit": "mmol/L"}, {"name": "HDL", "value": 1.15, "unit": None},
                      {"name": "Total cholesterol", "value": 224, "unit": "mg/dL"}, {"name": "eGFR", "value": 72}])
    assert out == {"ldl_mg_dl": 150.8, "hdl_mg_dl": 44.5, "total_chol_mg_dl": 224.0, "egfr": 72.0}


def test_match_diagnoses():
    assert match_diagnoses(["高血圧症", "心房細動", "Parkinson's disease", "Alzheimer type dementia"]) == {
        "known_hypertension": True, "known_af": True, "known_pd": True, "known_dementia": True}
    assert match_diagnoses(["No history of hypertension", "心房細動の疑い", "rule out angina"]) == {}


def test_bp_photo_route_creates_reading_and_irregular_rhythm(client, db, user, headers, fake):
    fake.vision_result = {"systolic": 158, "diastolic": 97, "pulse": 88, "irregular_icon": True,
                          "measured_at": "2026-10-16T07:15:00-04:00"}  # Toronto (EDT) morning
    res = client.post("/api/uploads/bp-photo", headers=headers, files={"file": ("bp.png", PNG, "image/png")})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["document"]["status"] == "done"
    [r] = body["bp_readings"]
    assert (r["systolic"], r["diastolic"], r["pulse"], r["source"], r["session"]) == (158, 97, 88, "omron_photo", "morning")
    assert r["irregular_pulse_flag"] is True
    rr = db.scalars(select(DeviceRhythmResult).where(DeviceRhythmResult.user_id == user.id)).all()
    assert [(x.source, x.label) for x in rr] == [("manual_irregular_pulse", "irregular_rhythm_notification")]


def test_bp_photo_unreadable_marks_failed(client, headers, fake):
    fake.vision_result = {"systolic": None, "diastolic": None, "pulse": None, "irregular_icon": False}
    res = client.post("/api/uploads/bp-photo", headers=headers, files={"file": ("bp.jpg", PNG, "image/jpeg")})
    assert res.status_code == 201
    assert res.json()["document"]["status"] == "failed" and res.json()["bp_readings"] == []


def test_ecg_pdf_image_and_audio_documents(client, db, user, headers, fake):
    fake.vision_queue = [{"device": "OMRON HCR-7800T", "classification": "心房細動の可能性",
                          "recorded_at": "2026-10-14T08:00:00+09:00"}]
    res = client.post("/api/uploads/document", headers=headers, data={"kind": "ecg_pdf"},
                      files={"file": ("ecg.png", PNG, "image/png")})
    assert res.status_code == 201, res.text
    assert res.json()["extracted"]["created"]["label"] == "possible_af"
    rr = db.scalar(select(DeviceRhythmResult).where(DeviceRhythmResult.user_id == user.id))
    assert (rr.source, rr.raw_label, rr.file_url is not None) == ("omron_connect", "心房細動の可能性", True)

    res = client.post("/api/uploads/document", headers=headers, data={"kind": "audio"},
                      files={"file": ("memo.m4a", b"\x00\x00\x00 ftypM4A ", "audio/mp4")})
    assert res.status_code == 201 and res.json()["status"] == "pending_transcription"

    res = client.post("/api/uploads/document", headers=headers, data={"kind": "doctor_note"},
                      files={"file": ("note.txt", (FIX / "doctor_note.txt").read_bytes(), "text/plain")})
    assert res.json()["status"] == "done"
    assert res.json()["extracted"]["data"]["follow_ups"] == ["Recheck home BP in 4 weeks"]

    listed = client.get("/api/uploads", headers=headers).json()
    assert {d["kind"] for d in listed} == {"ecg_pdf", "audio", "doctor_note"}


def test_document_validation(client, headers, fake):
    r = client.post("/api/uploads/document", headers=headers, data={"kind": "nope"},
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 422
    r = client.post("/api/uploads/document", headers=headers, data={"kind": "lab"},
                    files={"file": ("a.heic", b"xxxx", "image/heic")})
    assert r.status_code == 415
