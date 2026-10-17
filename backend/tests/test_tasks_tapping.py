"""Finger tapping (§5.3), 3-word recall (§1.4) and task history over HTTP."""

import uuid

import pytest

from app.core.auth import create_access_token
from app.db.models import AssessmentTask, User


@pytest.fixture()
def h(db):
    u = User(email=f"tap-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(u.id)}"}


def _taps(n, iti=200.0, start=1000.0):
    return [start + i * iti for i in range(n)]


def test_tapping_valid(client, db, h):
    r = client.post("/api/tasks/tapping", headers=h, json={"left_ms": _taps(60, 220), "right_ms": _taps(70, 200)})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["valid"] and b["reason"] is None
    f = b["features"]
    assert f["left"]["tap_count"] == 60 and f["right"]["tap_count"] == 70
    assert f["tap_count"] == 65 and f["mean_iti_ms"] == pytest.approx(210) and f["asymmetry"] is not None
    db.expire_all()
    t = db.get(AssessmentTask, b["task_id"])
    assert t.task_type == "finger_tap" and t.valid and t.score is None


def test_tapping_too_few_taps_invalid(client, h):
    r = client.post("/api/tasks/tapping", headers=h, json={"left_ms": _taps(4), "right_ms": _taps(60)})
    b = r.json()
    assert r.status_code == 201 and not b["valid"] and "fewer_than_5_taps" in b["reason"]
    assert "15 seconds" in b["message"]


def test_word_recall_flow(client, h):
    r = client.post("/api/tasks/word-recall/start", headers=h, json={"language": "en"})
    assert r.status_code == 201, r.text
    s = r.json()
    assert len(s["words"]) == 3 and s["language"] == "en" and s["recall_prompt"]
    resp = f"um, {s['words'][0]} and {s['words'][2]}"
    r = client.post(f"/api/tasks/word-recall/{s['task_id']}", headers=h, json={"response_text": resp, "latency_ms": 1800})
    b = r.json()
    assert r.status_code == 200 and b["score"] == 2 and b["max_score"] == 3 and b["missed"] == [s["words"][1]]
    assert b["valid"] and b["latency_ms"] == 1800
    again = client.post(f"/api/tasks/word-recall/{s['task_id']}", headers=h, json={"response_text": resp})
    assert again.status_code == 409
    # next trial uses a different set
    s2 = client.post("/api/tasks/word-recall/start?language=en", headers=h).json()
    assert s2["set_id"] != s["set_id"]


def test_history(client, h):
    client.post("/api/tasks/tapping", headers=h, json={"left_ms": _taps(30), "right_ms": _taps(30)})
    client.post("/api/tasks/word-recall/start", headers=h, json={"language": "ja"})
    items = client.get("/api/tasks/history", headers=h).json()["items"]
    assert [i["task_type"] for i in items] == ["word_recall", "finger_tap"]
    assert items[0]["status"] == "in_progress" and items[1]["status"] == "complete"
    only = client.get("/api/tasks/history?task_type=finger_tap&limit=1", headers=h).json()["items"]
    assert len(only) == 1 and only[0]["valid"]
    assert client.get("/api/tasks/history?task_type=nope", headers=h).status_code == 422
    assert client.get("/api/tasks/history").status_code == 401
