"""Story-recall battery over HTTP (spec phase-a-assessments §1.1, §1.3, §10)."""

import uuid

import pytest

from app.api.routes import tasks as routes
from app.content.loader import get_story_by_id
from app.core.auth import create_access_token
from app.db.models import AssessmentTask, User
from app.providers.embeddings import FakeEmbeddings
from app.providers.storage import LocalStorage
from app.providers.stt import FakeStt
from app.providers.tts import FakeTts


class CountingTts(FakeTts):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def synthesize(self, text, language, voice=None):
        self.calls += 1
        return super().synthesize(text, language, voice)


@pytest.fixture()
def user_headers(db):
    u = User(email=f"story-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(u.id)}"}


@pytest.fixture()
def overrides(app, tmp_path):
    tts = CountingTts()
    app.dependency_overrides[routes.get_tts] = lambda: tts
    app.dependency_overrides[routes.get_embeddings] = lambda: FakeEmbeddings()
    app.dependency_overrides[routes.get_task_storage] = lambda: LocalStorage(tmp_path)
    app.dependency_overrides[routes.get_stt] = lambda: FakeStt(text="")
    yield tts
    for dep in (routes.get_tts, routes.get_embeddings, routes.get_task_storage, routes.get_stt):
        app.dependency_overrides.pop(dep, None)


def _stories(db, task_id):
    db.expire_all()
    return [get_story_by_id(s) for s in db.get(AssessmentTask, task_id).raw_json["story_ids"]]


def _step(client, headers, task_id, step, **form):
    files = {"audio": ("rec.webm", b"not-really-audio", "audio/webm")} if form.pop("audio", False) else None
    return client.post(f"/api/tasks/story/{task_id}/step", data={"step": step, **form}, files=files, headers=headers)


def _walk(client, app, db, headers, texts_for):
    """Run the whole battery; ``texts_for(stories)`` → {step: STT text}."""
    r = client.post("/api/tasks/story/start?language=en", headers=headers)
    assert r.status_code == 201, r.text
    st = r.json()
    tid = st["task_id"]
    assert st["current_step"] == "story1_listen" and st["audio_url"] == f"/api/tasks/story/{tid}/audio/story1_listen"
    assert st["instructions"]["ja"] and st["instruction"].startswith("Listen")
    texts = texts_for(_stories(db, tid))
    for step in ("story1_listen", "story1_recall", "story2_listen", "story2_recall", "fluency_60s", "delay",
                 "story1_delayed_recall"):
        if step in texts:
            app.dependency_overrides[routes.get_stt] = lambda t=texts[step]: FakeStt(text=t)
            r = _step(client, headers, tid, step, audio=True)
        elif step == "delay":
            r = _step(client, headers, tid, step, force_delay="true")
        else:
            r = _step(client, headers, tid, step, played_s="50")
        assert r.status_code == 200, (step, r.text)
    return tid, r.json()


def test_full_session_valid(client, app, db, user_headers, overrides):
    user, h = user_headers
    tid, st = _walk(client, app, db, h, lambda s: {
        "story1_recall": s[0].text, "story2_recall": s[1].text, "fluency_60s": "dog cat horse lion",
        "story1_delayed_recall": s[0].text})
    assert st["finished"] and st["current_step"] is None
    res = st["result"]
    assert res["valid"] and res["status"] == "ok" and res["score"] > 90
    assert set(res["per_recall"]) == {"immediate_1", "immediate_2", "delayed"}
    db.expire_all()
    t = db.get(AssessmentTask, tid)
    assert t.valid and t.score == res["score"] and t.task_type == "story_recall"
    assert t.language == "en" and t.protocol_version and t.model_scores == {}
    assert set(t.raw_json["audio_urls"]) == {"story1_recall", "story2_recall", "fluency_60s", "story1_delayed_recall"}
    assert t.raw_json["transcripts"]["story1_recall"]["language"] == "en"
    assert t.features["temporal"]["session"]["delay_s"] is not None
    # GET status after finish
    g = client.get(f"/api/tasks/story/{tid}", headers=h).json()
    assert g["finished"] and g["result"]["valid"]
    # submitting again → 409
    assert _step(client, h, tid, "story1_delayed_recall").status_code == 409


def test_empty_transcript_invalid(client, app, db, user_headers, overrides):
    _, h = user_headers
    tid, st = _walk(client, app, db, h, lambda s: {k: "" for k in
                                                     ("story1_recall", "story2_recall", "fluency_60s",
                                                      "story1_delayed_recall")})
    res = st["result"]
    assert not res["valid"] and res["status"] == "invalid_measurement" and res["score"] is None
    assert "immediate_1:empty_transcript" in res["invalid_reasons"]
    db.expire_all()
    t = db.get(AssessmentTask, tid)
    assert t.valid is False and "empty_transcript" in t.invalid_reason


def test_delay_409_then_force(client, app, db, user_headers, overrides):
    _, h = user_headers
    tid = client.post("/api/tasks/story/start?language=en", headers=h).json()["task_id"]
    s1, s2 = _stories(db, tid)
    _step(client, h, tid, "story1_listen")
    _step(client, h, tid, "story1_recall", transcript=s1.text)
    # out of order
    r = _step(client, h, tid, "fluency_60s", transcript="dog")
    assert r.status_code == 409 and r.json()["current_step"] == "story2_listen"
    _step(client, h, tid, "story2_listen")
    _step(client, h, tid, "story2_recall", transcript=s2.text)
    st = _step(client, h, tid, "fluency_60s", transcript="dog cat").json()
    assert st["current_step"] == "delay" and 0 < st["delay_remaining_s"] <= 180
    r = _step(client, h, tid, "delay")
    assert r.status_code == 409 and r.json()["delay_remaining_s"] > 0
    r = _step(client, h, tid, "delay", force_delay="true")
    assert r.status_code == 200 and r.json()["current_step"] == "story1_delayed_recall"
    assert "first story" in r.json()["instruction"]


def test_story_ids_not_reused_and_audio_cached(client, db, user_headers, overrides):
    _, h = user_headers
    seen = []
    for _ in range(3):
        r = client.post("/api/tasks/story/start?language=en", headers=h)
        assert r.status_code == 201
        seen += [s.id for s in _stories(db, r.json()["task_id"])]
    assert len(seen) == len(set(seen)) == 6
    assert client.post("/api/tasks/story/start?language=en", headers=h).status_code == 409  # bank exhausted
    tid = r.json()["task_id"]
    a1 = client.get(f"/api/tasks/story/{tid}/audio/story1_listen", headers=h)
    a2 = client.get(f"/api/tasks/story/{tid}/audio/story1_listen", headers=h)
    assert a1.status_code == 200 and a1.headers["content-type"] == "audio/wav" and a1.content == a2.content
    assert overrides.calls == 1
    assert client.get(f"/api/tasks/story/{tid}/audio/story1_recall", headers=h).status_code == 400


def test_story_auth_and_ownership(client, db, user_headers, overrides, auth_headers):
    _, h = user_headers
    tid = client.post("/api/tasks/story/start?language=ja", headers=h).json()["task_id"]
    assert client.get(f"/api/tasks/story/{tid}").status_code == 401
    assert client.get(f"/api/tasks/story/{tid}", headers=auth_headers).status_code == 404
    st = client.get(f"/api/tasks/story/{tid}", headers=h).json()
    assert st["language"] == "ja" and "お話" in st["instruction"]
