from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok" and body["version"] and body["db"] == "sqlite"
    assert body["providers"] == {"llm": "fake", "embeddings": "fake", "stt": "fake", "email": "fake",
                                 "calendar": body["providers"]["calendar"]}
    assert body["providers"]["calendar"] in ("google", "fake") and body["scheduler"] == "disabled"


def test_ingest_then_chat(auth_headers):
    # dev mode: /api/ingest needs no admin secret; chat needs a user (Bearer token)
    res = client.post("/api/ingest", json={"text": "hello world " * 100, "source": "t"})
    assert res.status_code == 200, res.text
    assert res.json()["chunks_added"] > 0
    res = client.post("/api/chat", json={"message": "hello world", "language": "en"}, headers=auth_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert "t" in [s["id"] for s in body["sources"]]
    assert body["red_flags"] == [] and body["answer"]
