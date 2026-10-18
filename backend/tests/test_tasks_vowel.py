"""Sustained-vowel task over HTTP (spec phase-a-assessments §5.4, §10, §11.2)."""

import io
import uuid

import numpy as np
import pytest
import soundfile as sf

from app.api.routes import tasks as routes
from app.core.auth import create_access_token
from app.db.models import AssessmentTask, User
from app.providers.storage import LocalStorage
from tests.test_vowel_features import SR, synth_vowel


def _wav(y, sr=SR) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, y, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture()
def h(app, db, tmp_path):
    u = User(email=f"vowel-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.commit()
    app.dependency_overrides[routes.get_task_storage] = lambda: LocalStorage(tmp_path)
    yield {"Authorization": f"Bearer {create_access_token(u.id)}"}
    app.dependency_overrides.pop(routes.get_task_storage, None)


def test_vowel_valid_with_model_score(client, db, h):
    r = client.post("/api/tasks/vowel", headers=h, data={"repetition": "2"},
                    files={"audio": ("ahh.wav", _wav(synth_vowel()), "audio/wav")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["valid"] and body["status"] == "ok" and body["tips"] == [] and body["repetition_index"] == 2
    exp = body["experimental_score"]
    assert exp["detail_only"] and exp["label"] == "experimental voice-model score" and 0 <= exp["probability"] <= 1
    db.expire_all()
    t = db.get(AssessmentTask, body["task_id"])
    m = t.model_scores["pd_vowel"]
    assert {"probability", "threshold", "version", "above_threshold", "calibration_note"} <= set(m)
    assert t.score == m["probability"] and t.valid and t.recording_quality["ok"]
    assert t.features and "MDVP:Fo(Hz)" in t.features and t.raw_url
    hist = client.get("/api/tasks/history?task_type=sustained_vowel", headers=h).json()["items"]
    assert hist[0]["model_scores"]["pd_vowel"]["detail_only"] is True


@pytest.mark.parametrize("kind", ["clipped", "noisy"])
def test_vowel_invalid_with_tips(client, db, h, kind):
    y = synth_vowel()
    y = np.clip(y * 4, -1, 1) if kind == "clipped" else y + 0.2 * np.random.default_rng(1).standard_normal(len(y))
    r = client.post("/api/tasks/vowel", headers=h, data={"repetition": "1"},
                    files={"audio": ("ahh.wav", _wav(np.clip(y, -1, 1)), "audio/wav")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert not body["valid"] and body["status"] == "invalid_measurement" and body["reasons"]
    assert body["experimental_score"] is None and len(body["tips"]) >= 4
    assert any("10" in t and "15" in t for t in body["tips"])
    db.expire_all()
    t = db.get(AssessmentTask, body["task_id"])
    assert t.valid is False and t.model_scores == {} and t.invalid_reason


def test_vowel_undecodable(client, h):
    r = client.post("/api/tasks/vowel", headers=h, files={"audio": ("x.wav", b"garbage-bytes", "audio/wav")})
    assert r.status_code == 422


def test_vowel_stores_device_label(client, db, h):
    """PD-03B (spec §5.5): the recording device is stored with every repetition (User-Agent or explicit field)."""
    from app.services.task_service import device_label

    ua = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"}
    r = client.post("/api/tasks/vowel", headers=h | ua, data={"repetition": "1"},
                    files={"audio": ("ahh.wav", _wav(synth_vowel()), "audio/wav")})
    assert r.status_code == 201, r.text
    assert r.json()["recording_quality"]["device"] == "iPhone"
    r2 = client.post("/api/tasks/vowel", headers=h | ua, data={"repetition": "2", "device": "my kitchen phone"},
                     files={"audio": ("ahh.wav", _wav(synth_vowel()), "audio/wav")})
    db.expire_all()
    assert db.get(AssessmentTask, r2.json()["task_id"]).recording_quality["device"] == "my kitchen phone"
    assert device_label("Mozilla/5.0 (Linux; Android 14; Pixel 7 Build/UQ1A.240105) AppleWebKit") == "Android Pixel 7"
    assert device_label("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)") == "Mac"
    assert device_label("Mozilla/5.0 (Windows NT 10.0; Win64; x64)") == "Windows"
    assert device_label("curl/8.0") == "other" and device_label(None) is None and device_label("", "  ") is None
