"""Rate limiting on chat / tasks (disabled suite-wide in conftest; enabled here)."""
import pytest

from app.core.config import settings
from app.core.ratelimit import limiter


@pytest.fixture()
def limited(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_default", "3/minute")
    limiter.reset()
    yield
    limiter.reset()


def test_tasks_limited_per_user(client, auth_headers, limited):
    codes = [client.get("/api/tasks/history", headers=auth_headers).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    r = client.get("/api/tasks/history", headers=auth_headers)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    # other endpoints are not limited
    assert client.get("/api/health").status_code == 200


def test_disabled_by_default_in_tests(client, auth_headers):
    assert not settings.rate_limit_enabled
    assert all(client.get("/api/tasks/history", headers=auth_headers).status_code == 200 for _ in range(5))
