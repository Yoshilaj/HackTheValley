"""POST /api/admin/seed-demo gate + provider misconfiguration → clean 503."""
import uuid

from app.api.routes import chat as chat_routes
from app.core.config import settings
from app.providers.llm import AnthropicLlm


def test_seed_demo_gate(client, db, monkeypatch):
    # review fix 17: plant the trend on a fresh user, not the shared demo@example.com, so later
    # tests that use demo_user are unaffected
    import scripts.seed as seed_mod
    from app.db.models import Profile, User

    fresh = User(email=f"admin-seed-{uuid.uuid4().hex[:8]}@example.com")
    fresh.profile = Profile(**seed_mod.DEMO_PROFILE)
    db.add(fresh)
    db.commit()
    monkeypatch.setattr(seed_mod, "seed_demo_user", lambda _db: _db.get(User, fresh.id))
    monkeypatch.setattr(settings, "dev_mode", False)
    assert client.post("/api/admin/seed-demo").status_code == 403
    r = client.post("/api/admin/seed-demo", headers={"X-Admin-Secret": settings.admin_secret})
    assert r.status_code == 200 and r.json()["email"] == "demo@example.com"
    assert r.json()["user_id"] == fresh.id
    again = client.post("/api/admin/seed-demo", headers={"X-Admin-Secret": settings.admin_secret}).json()
    assert again["seed"] == {"skipped": True}


def test_missing_key_is_503_json(client, app, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setattr(settings, "voyage_api_key", "")
    r = client.post("/api/chat", headers=auth_headers, json={"message": "How do I measure BP?", "language": "en"})
    assert r.status_code == 503 and "VOYAGE_API_KEY" in r.json()["detail"]
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    app.dependency_overrides[chat_routes.get_llm] = lambda: AnthropicLlm()
    try:
        r = client.post("/api/chat", headers=auth_headers, json={"message": "How do I measure BP?", "language": "en"})
    finally:
        app.dependency_overrides.pop(chat_routes.get_llm, None)
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert r.json()["error"] == "provider_not_configured"


def test_confirm_links_dev_only(client, monkeypatch):
    assert client.get("/api/admin/appointments/nope/confirm-links").status_code == 404
    monkeypatch.setattr(settings, "dev_mode", False)
    assert client.get("/api/admin/appointments/nope/confirm-links").status_code == 403
