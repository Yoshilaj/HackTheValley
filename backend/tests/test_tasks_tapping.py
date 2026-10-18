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


def _events(n, iti=480.0, dwell=95.0, start=100.0, keys=("s", ";")):
    return [{"key": keys[k % 2], "down_ms": start + k * iti, "up_ms": start + k * iti + dwell} for k in range(n)]


def _body(left, right, **kw):
    return {"input_method": "keyboard", "duration_s": 30, "keys": ["s", ";"], "left": {"events": left},
            "right": {"events": right}, **kw}


def test_tapping_valid(client, db, h):
    r = client.post("/api/tasks/tapping", headers=h, json=_body(_events(60, 500), _events(62, 480, 90)))
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["valid"] and b["reason"] is None and b["message"].startswith("Tapping recorded")
    f = b["features"]
    assert f["left"]["tap_count"] == 60 and f["right"]["tap_count"] == 62 and f["input_method"] == "keyboard"
    assert f["tap_count"] == 61 and f["kinesia_score"] == 61 and f["mean_iti_ms"] == pytest.approx(490)
    assert f["dwell_mean_ms"] == pytest.approx(92.5) and f["travel_time_cv"] == pytest.approx(0, abs=1e-6)
    assert f["asymmetry"] is not None and f["alternation_error_rate"] == 0
    db.expire_all()
    t = db.get(AssessmentTask, b["task_id"])
    assert t.task_type == "finger_tap" and t.valid and t.score is None and t.protocol_version == "tapping-2"
    assert t.recording_quality["device"] == "keyboard" and t.recording_quality["left"]["tap_count"] == 60
    assert t.raw_json["input_method"] == "keyboard" and len(t.raw_json["right"]["events"]) == 62
    assert t.raw_json["left"]["events"][0] == {"key": "s", "down_ms": 100.0, "up_ms": 195.0}


def test_tapping_keyboard_only(client, h):
    for method in ("mouse", "touch", "pointer"):
        r = client.post("/api/tasks/tapping", headers=h, json=_body(_events(60), _events(60), input_method=method))
        assert r.status_code == 422 and r.json()["detail"] == "keyboard tapping only"
    body = _body(_events(60), _events(60))
    del body["input_method"]
    assert client.post("/api/tasks/tapping", headers=h, json=body).status_code == 422
    assert client.post("/api/tasks/tapping", headers=h, json=_body(_events(60), _events(60), duration_s=4)).status_code == 422
    too_many = _events(4001, iti=7.0)
    assert client.post("/api/tasks/tapping", headers=h, json=_body(too_many, _events(60))).status_code == 422


def test_tapping_autorepeat_rejected(client, h):
    held = _events(60) + [{"key": ";", "down_ms": 29000.0 + 25 * k, "up_ms": None} for k in range(8)]
    r = client.post("/api/tasks/tapping", headers=h, json=_body(_events(60), held))
    b = r.json()
    assert r.status_code == 201 and not b["valid"] and b["reason"] == "right:key_autorepeat_detected"
    assert b["features"]["right"]["n_autorepeat"] == 7 and "auto-repeated" in b["message"]


def test_tapping_error_presses_and_too_few(client, h):
    left = _events(50)
    left.insert(5, {"key": "a", "down_ms": left[5]["down_ms"] - 100, "up_ms": left[5]["down_ms"] - 20})
    r = client.post("/api/tasks/tapping", headers=h, json=_body(left, _events(8)))
    b = r.json()
    assert r.status_code == 201 and not b["valid"] and b["reason"] == "right:fewer_than_10_taps"
    assert b["features"]["left"]["error_presses"] == 1 and b["features"]["left"]["valid"]
    assert b["features"]["left"]["alternation_error_rate"] == pytest.approx(1 / 51)
    assert "30 seconds" in b["message"]
    short = client.post("/api/tasks/tapping", headers=h, json=_body(_events(30), _events(30), duration_s=15)).json()
    assert short["reason"] == "left:duration_below_20_s; right:duration_below_20_s"


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
    client.post("/api/tasks/tapping", headers=h, json=_body(_events(30), _events(30)))
    client.post("/api/tasks/word-recall/start", headers=h, json={"language": "ja"})
    items = client.get("/api/tasks/history", headers=h).json()["items"]
    assert [i["task_type"] for i in items] == ["word_recall", "finger_tap"]
    assert items[0]["status"] == "in_progress" and items[1]["status"] == "complete"
    only = client.get("/api/tasks/history?task_type=finger_tap&limit=1", headers=h).json()["items"]
    assert len(only) == 1 and only[0]["valid"]
    summary = only[0]["summary"]
    assert summary["left_tap_count"] == 30 and summary["right_tap_count"] == 30 and summary["tap_count"] == 30
    assert {"mean_iti_ms", "iti_cv", "asymmetry", "left_iti_cv", "right_iti_cv"} <= set(summary)
    assert all(v is not None for v in summary.values()) and len(summary) <= 10
    assert items[0]["summary"] == {}  # in-progress word recall has no features yet
    assert client.get("/api/tasks/history?task_type=nope", headers=h).status_code == 422
    assert client.get("/api/tasks/history").status_code == 401
