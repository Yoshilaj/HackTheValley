import pytest

from app.core.config import settings
from app.providers import email as email_mod
from app.providers.email import FakeEmail, ResendEmail, get_email_provider, render_email


def test_fake_email_outbox():
    fake = FakeEmail()
    res = fake.send("a@example.com", "Hi", "<p>x</p>", text="x", tags={"kind": "test"})
    assert res.accepted and res.provider == "fake" and res.id
    assert len(fake.outbox) == 1
    assert fake.outbox[0].to == ["a@example.com"] and fake.outbox[0].tags == {"kind": "test"}


def test_factory_returns_fake_without_key(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "")
    assert isinstance(get_email_provider(), FakeEmail)


def test_factory_returns_resend_with_key(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(settings, "resend_from_email", "noreply@example.com")
    assert isinstance(get_email_provider(), ResendEmail)


def test_resend_raises_clearly_without_key(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", "")
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        ResendEmail()


def test_resend_send_reports_errors_without_network(monkeypatch):
    import resend

    def boom(params):
        assert params["from"] == "noreply@example.com" and params["to"] == ["a@example.com"]
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(resend.Emails, "send", staticmethod(boom))
    res = ResendEmail(api_key="re_test", from_email="noreply@example.com").send("a@example.com", "s", "<p>h</p>")
    assert not res.accepted and "no network" in res.error


def test_resend_send_success_mocked(monkeypatch):
    import resend

    monkeypatch.setattr(resend.Emails, "send", staticmethod(lambda params: {"id": "em_123"}))
    res = ResendEmail(api_key="re_test", from_email="noreply@example.com").send("a@example.com", "s", "<p>h</p>")
    assert res.accepted and res.id == "em_123" and res.provider == "resend"


def test_render_email_has_disclaimers_and_escapes():
    html = render_email("<Title>", "<p>body</p>", cta_url="https://x.test/a?b=1&c=2")
    assert "&lt;Title&gt;" in html and "<p>body</p>" in html
    assert "This is not a diagnosis" in html and "これは診断ではありません" in html
    assert "https://x.test/a?b=1&amp;c=2" in html
    assert email_mod.DISCLAIMER_EN.startswith("This is not a diagnosis")
