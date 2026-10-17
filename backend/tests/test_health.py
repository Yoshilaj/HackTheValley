from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ingest_then_chat():
    res = client.post("/api/ingest", json={"text": "hello world " * 100, "source": "t"})
    assert res.json()["chunks_added"] > 0
    res = client.post("/api/chat", json={"message": "hello"})
    assert res.status_code == 200
    assert "t" in res.json()["sources"]
