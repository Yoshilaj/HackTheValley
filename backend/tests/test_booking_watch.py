"""Watching the web channel live (headed / slow-mo), the ngrok interstitial header, the mock clinic's
front-desk page, and scripts.test_booking argument handling (booking-v2 §3 "Live demo")."""

from __future__ import annotations

import socket
import threading
import time
from types import SimpleNamespace

import pytest

from app.api.routes import mock_clinic
from app.core.config import settings
from app.integrations import Clinic
from app.services.booking.channels import web as webmod
from app.services.booking.channels.web import (
    HARD_TIMEOUT_S, LOW_MEMORY_ARGS, MAX_HARD_TIMEOUT_S, NGROK_SKIP_HEADER, BrowserOptions, WebChannel,
    context_options, hard_timeout_for, launch_options, make_guard, target_extra_headers,
)
from tests.test_booking_web import _ctx, _open_slots_near, chromium_launchable
from tests.test_mock_clinic import valid_form


@pytest.fixture(autouse=True)
def fresh_store():
    mock_clinic.store.reset()
    yield
    mock_clinic.store.reset()


# ================================================================ headed / slow-mo plumbing

def test_default_is_headless_low_memory_no_slow_mo(monkeypatch):
    monkeypatch.setattr(settings, "web_booking_low_memory", True)
    opts = WebChannel().browser_options()
    assert opts == BrowserOptions(headed=False, slow_mo_ms=0) and not opts.watched
    kw = launch_options(opts)
    assert kw["headless"] is True and "slow_mo" not in kw
    assert all(a in kw["args"] for a in LOW_MEMORY_ARGS)
    assert hard_timeout_for(HARD_TIMEOUT_S, 30, opts) == HARD_TIMEOUT_S
    assert hard_timeout_for(0.2, 30, opts) == 0.2  # tests' tiny timeouts are kept as they are
    assert opts.action_timeout_ms() == webmod.ACTION_TIMEOUT_MS and opts.nav_timeout_ms() == webmod.NAV_TIMEOUT_MS


def test_settings_switch_headed_and_slow_mo(monkeypatch):
    monkeypatch.setattr(settings, "web_booking_low_memory", True)
    monkeypatch.setattr(settings, "web_booking_headed", True)
    monkeypatch.setattr(settings, "web_booking_slow_mo_ms", 400)
    opts = WebChannel().browser_options()
    assert opts == BrowserOptions(headed=True, slow_mo_ms=400)
    kw = launch_options(opts)
    assert kw["headless"] is False and kw["slow_mo"] == 400
    assert not any(a in kw["args"] for a in LOW_MEMORY_ARGS)  # single-process breaks headed Chromium
    assert "--disable-gpu" in kw["args"]
    assert opts.action_timeout_ms() == webmod.ACTION_TIMEOUT_MS + 800
    # constructor arguments win over the settings
    assert WebChannel(headless=True, slow_mo_ms=0).browser_options() == BrowserOptions(False, 0)
    monkeypatch.setattr(settings, "web_booking_slow_mo_ms", -5)
    assert BrowserOptions.from_settings().slow_mo_ms == 0


def test_hard_timeout_grows_with_slow_mo_and_is_capped():
    assert hard_timeout_for(60, 10, BrowserOptions(False, 400)) == pytest.approx(60 + 10 * 0.4 * 4)
    assert hard_timeout_for(60, 10, BrowserOptions(True, 400)) == pytest.approx(60 + 16 + webmod.HEADED_HOLD_S)
    assert hard_timeout_for(60, 10, BrowserOptions(True, 0)) == pytest.approx(60 + webmod.HEADED_HOLD_S)
    assert hard_timeout_for(0.2, 10, BrowserOptions(True, 400)) >= 60  # never below 60 s when watched
    assert hard_timeout_for(60, 200, BrowserOptions(True, 2000)) == MAX_HARD_TIMEOUT_S


def test_watchdog_uses_slow_mo_deadline_and_honours_extension(monkeypatch):
    monkeypatch.setattr(webmod, "chromium_installed", lambda headed=False: True)
    c = Clinic(id="c", name="C", specialty="cardiology", website="https://real-clinic.ca")
    seen = {}

    ch = WebChannel(hard_timeout_s=0.2, headless=True, slow_mo_ms=0)

    def drive(appt, ctx, target, start, run):
        seen["opts"] = run.opts
        run.extend(1.5)       # the form is known: the worker grants itself more time
        time.sleep(0.5)       # longer than the base 0.2 s
        return webmod.ChannelResult(status="requested", reason="ok")

    monkeypatch.setattr(ch, "_drive", drive)
    res = ch.execute(SimpleNamespace(id="a"), _ctx(c, {"web": "web_form"}))
    assert res.status == "requested" and seen["opts"] == BrowserOptions(False, 0)

    slow = WebChannel(hard_timeout_s=0.2, headless=True, slow_mo_ms=300)
    monkeypatch.setattr(slow, "_drive", lambda *a: time.sleep(0.4) or webmod.ChannelResult(status="requested"))
    assert slow.execute(SimpleNamespace(id="a"), _ctx(c, {"web": "web_form"})).status == "requested"

    stuck = WebChannel(hard_timeout_s=0.2)
    monkeypatch.setattr(stuck, "_drive", lambda *a: time.sleep(1))
    res = stuck.execute(SimpleNamespace(id="a"), _ctx(c, {"web": "web_form"}))
    assert res.status == "skipped" and "timed out" in res.reason


# ================================================================ ngrok interstitial header

NGROK = "https://tidy-otter-demo.ngrok-free.dev/mock-clinic"


def test_ngrok_header_only_for_the_sandbox_target_origin():
    assert target_extra_headers(NGROK + "/book", NGROK, sandboxed=True) == {NGROK_SKIP_HEADER: "1"}
    assert target_extra_headers("https://tidy-otter-demo.ngrok-free.dev/static/x.css", NGROK, True) == {NGROK_SKIP_HEADER: "1"}
    assert target_extra_headers("https://fonts.example.com/a.woff", NGROK, True) == {}
    assert target_extra_headers("http://tidy-otter-demo.ngrok-free.dev/mock-clinic", NGROK, True) == {}  # other scheme
    assert target_extra_headers("https://tidy-otter-demo.ngrok-free.dev:8443/x", NGROK, True) == {}       # other port
    assert target_extra_headers("https://evil.ngrok-free.dev/x", NGROK, True) == {}
    assert target_extra_headers(NGROK + "/book", NGROK, sandboxed=False) == {}  # never to a live clinic
    assert target_extra_headers("data:image/png;base64,xx", NGROK, True) == {}


def test_context_header_only_in_sandbox():
    sb = context_options(NGROK, sandboxed=True, tz="America/Toronto")
    assert sb["extra_http_headers"] == {NGROK_SKIP_HEADER: "1"} and sb["timezone_id"] == "America/Toronto"
    live = context_options("https://real-clinic.ca/book", sandboxed=False, tz=None)
    assert "extra_http_headers" not in live and live["timezone_id"] == "America/Toronto"


class _Route:
    def __init__(self, url, rtype, status=200, location=None):
        self.request = SimpleNamespace(url=url, resource_type=rtype, headers={"user-agent": "UA"})
        self.calls: list[tuple[str, dict]] = []
        self._resp = SimpleNamespace(status=status, headers={"location": location} if location else {})

    def abort(self):
        self.calls.append(("abort", {}))

    def continue_(self, **kw):
        self.calls.append(("continue", kw))

    def fetch(self, **kw):
        self.calls.append(("fetch", kw))
        return self._resp

    def fulfill(self, **kw):
        self.calls.append(("fulfill", {}))


def test_sandbox_guard_never_lets_another_host_be_contacted(monkeypatch):
    """So the context-level header can only ever reach the target in sandbox mode."""
    run = webmod._Run()
    guard = make_guard(NGROK, True, run)
    doc = _Route(NGROK + "/book", "document")
    guard(doc)
    assert doc.calls == [("fetch", {"max_redirects": 0}), ("fulfill", {})]
    for url, rtype in (("https://cdn.example.com/x.js", "script"), ("https://fonts.example.com/a.woff", "font"),
                       ("https://evil.ngrok-free.dev/", "document"), ("http://tidy-otter-demo.ngrok-free.dev/x", "image")):
        r = _Route(url, rtype)
        guard(r)
        assert r.calls == [("abort", {})], url
    redirect = _Route(NGROK + "/book", "document", status=303, location="https://evil.example/phish")
    guard(redirect)
    assert redirect.calls[-1] == ("abort", {}) and "https://evil.example/phish" in run.blocked
    same = _Route(NGROK + "/book", "document", status=303, location="/mock-clinic/confirmation/QW-AAAA22")
    guard(same)
    assert same.calls[-1] == ("fulfill", {})


# ================================================================ real Chromium: header + headed path

def _serve(asgi_app):
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(asgi_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    return server, th, f"http://127.0.0.1:{port}"


@pytest.fixture()
def recorded(app, monkeypatch):
    """The app behind a header-recording ASGI wrapper on a random port (like ngrok -> uvicorn)."""
    if not chromium_launchable():
        pytest.skip("Playwright Chromium is not available")
    seen: list[tuple[str, dict[bytes, bytes]]] = []

    async def recorder(scope, receive, send):
        if scope["type"] == "http":
            seen.append((scope["path"], dict(scope["headers"])))
        await app(scope, receive, send)

    server, th, base = _serve(recorder)
    monkeypatch.setattr(settings, "base_url", base)
    monkeypatch.setattr(settings, "web_booking_enabled", True)
    monkeypatch.setattr(settings, "sandbox_clinic_website", "")
    yield base, seen
    server.should_exit = True
    th.join(5)


def test_browser_sends_ngrok_header_to_the_target(recorded):
    base, seen = recorded
    c = Clinic(id="c", name="Bloor (demo)", specialty="cardiology", website=f"{base}/mock-clinic")
    res = WebChannel().execute(SimpleNamespace(id="appt-h", user_id="u-web"),
                               _ctx(c, {"web": "web_form", "form_url": f"{base}/mock-clinic/book"}, _open_slots_near()))
    assert res.status == "confirmed", res.reason
    paths = [p for p, _ in seen]
    assert "/mock-clinic/book" in paths and any(p.startswith("/mock-clinic/confirmation/") for p in paths)
    missing = [p for p, h in seen if h.get(NGROK_SKIP_HEADER.encode()) != b"1"]
    assert not missing, missing  # incl. the confirmation page reached via the 303 redirect


def test_headed_flow_highlights_and_lingers_without_breaking_safety(recorded, monkeypatch):
    """The headed code path (highlight per field, slow-mo, hold on the result page), run headless."""
    base, _ = recorded
    real = launch_options
    monkeypatch.setattr(webmod, "launch_options", lambda o: real(o) | {"headless": True})
    monkeypatch.setattr(webmod, "HEADED_HOLD_S", 0.3)
    calls: list[str] = []
    orig = WebChannel._highlight
    monkeypatch.setattr(WebChannel, "_highlight", staticmethod(lambda loc: (calls.append("hl"), orig(loc))))
    c = Clinic(id="c", name="Bloor (demo)", specialty="cardiology", website=f"{base}/mock-clinic")
    ch = WebChannel(headless=False, slow_mo_ms=10)
    res = ch.execute(SimpleNamespace(id="appt-w", user_id="u-web"),
                     _ctx(c, {"web": "web_form", "form_url": f"{base}/mock-clinic/book"}, _open_slots_near()))
    assert res.status == "confirmed", res.reason
    assert len(calls) >= 7  # every filled field was highlighted first
    assert res.details["duration_s"] >= 0.3
    assert set(res.details["screenshots"]) == {"before_submit", "result"}

    login = Clinic(id="l", name="Midtown (demo)", specialty="memory_clinic", website=f"{base}/mock-clinic/login")
    res = ch.execute(SimpleNamespace(id="appt-l", user_id="u-web"),
                     _ctx(login, {"web": "web_form", "form_url": f"{base}/mock-clinic/login/book"}, _open_slots_near()))
    assert res.status == "skipped" and res.reason == "login required"  # walls still stop a headed run


# ================================================================ front-desk page

def _book(client, **kw):
    slot = mock_clinic.open_slots()[2]["value"]
    r = client.post("/mock-clinic/book", data=valid_form(slot, **kw), follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[-1]


def test_front_desk_lists_bookings_newest_first(client):
    first = _book(client)
    time.sleep(0.01)
    second = _book(client, first_name="Sam", last_name="Lee", health_card="1234-567-890")
    r = client.get("/mock-clinic/admin")
    assert r.status_code == 200 and r.headers["x-robots-tag"].startswith("noindex")
    html = r.text
    assert "Front desk" in html and "Clear demo bookings" in html and "setInterval(refresh, 3000)" in html
    assert html.index(second) < html.index(first)  # newest first
    assert "Sam Lee" in html and "Alex Tremblay" in html and "1958-01-15" in html and "M5S 1X5" in html
    assert "General check-up" in html and "alex@example.com" in html and "+1 416-555-0199" in html
    assert "1234-567-890" not in html and "provided" in html and "not provided" in html
    feed = client.get("/mock-clinic/admin/feed").json()
    assert feed["count"] == 2 and [x["reference"] for x in feed["rows"]] == [second, first]
    row = feed["rows"][1]
    assert row["consent"] == "yes" and row["health_card"] == "not provided" and row["name"] == "Alex Tremblay"
    assert row["slot"] and row["received"] and "notes" not in row and "user_agent" not in row


def test_front_desk_escapes_values(client):
    _book(client, first_name="<script>alert(1)</script>")
    html = client.get("/mock-clinic/admin").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_front_desk_auth_outside_dev_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", False)
    for path in ("/mock-clinic/admin", "/mock-clinic/admin/feed"):
        assert client.get(path).status_code == 403
        assert client.get(path, params={"key": "wrong"}).status_code == 403
        assert client.get(path, params={"key": settings.admin_secret}).status_code == 200
        assert client.get(path, headers={"X-Admin-Secret": settings.admin_secret}).status_code == 200
    assert client.post("/mock-clinic/admin/clear", headers={"X-Front-Desk": "1"}).status_code == 403
    # the JSON list keeps its original rule (header only)
    assert client.get("/mock-clinic/admin/bookings", params={"key": settings.admin_secret}).status_code == 403


def test_clear_button_deletes_bookings(client, monkeypatch):
    _book(client)
    _book(client)
    assert client.post("/mock-clinic/admin/clear").status_code == 400  # no X-Front-Desk: not from the page
    assert len(mock_clinic.store.all()) == 2
    r = client.post("/mock-clinic/admin/clear", headers={"X-Front-Desk": "1"})
    assert r.status_code == 200 and r.json() == {"cleared": 2}
    assert client.get("/mock-clinic/admin/bookings").json()["count"] == 0
    assert not mock_clinic.store.path.exists()
    monkeypatch.setattr(settings, "dev_mode", False)
    _book(client)
    r = client.post("/mock-clinic/admin/clear", params={"key": settings.admin_secret}, headers={"X-Front-Desk": "1"})
    assert r.json() == {"cleared": 1}


# ================================================================ scripts.test_booking

def test_booking_script_arguments(capsys):
    from scripts import test_booking as tb

    a = tb.parse_args([])
    assert (a.channel, a.headed, a.slow_mo) == ("phone", False, None)
    a = tb.parse_args(["--channel", "web", "--headed", "--slow-mo", "400"])
    assert (a.channel, a.headed, a.slow_mo) == ("web", True, 400)
    assert tb.parse_args(["--channel", "email"]).channel == "email"
    for bad in (["--channel", "fax"], ["--headed"], ["--channel", "email", "--slow-mo", "100"],
                ["--channel", "web", "--slow-mo", "-1"], ["--channel", "web", "--slow-mo", "99999"]):
        with pytest.raises(SystemExit):
            tb.parse_args(bad)


def test_booking_script_overrides_and_channels(monkeypatch):
    from scripts import test_booking as tb

    monkeypatch.setattr(settings, "sandbox_clinic_website", "https://some-other-site.example/")
    monkeypatch.setattr(settings, "web_booking_headed", False)
    monkeypatch.setattr(settings, "web_booking_slow_mo_ms", 0)
    monkeypatch.setattr(settings, "web_booking_enabled", False)
    tb.apply_overrides(tb.parse_args(["--channel", "web", "--headed", "--slow-mo", "250"]))
    assert settings.web_booking_headed is True and settings.web_booking_slow_mo_ms == 250
    assert settings.web_booking_enabled is True
    assert settings.sandbox_clinic_website == settings.base_url.rstrip("/") + "/mock-clinic"  # forced mock clinic
    assert list(tb.build_channels("web")) == ["web"]
    assert list(tb.build_channels("email")) == ["email"]
    assert list(tb.build_channels("phone")) == ["phone"]
    assert WebChannel().browser_options() == BrowserOptions(True, 250)


def test_booking_script_preflight_guards(monkeypatch):
    from scripts import test_booking as tb

    monkeypatch.setattr(settings, "booking_target_mode", "live")
    assert any("sandbox" in p for p in tb.preflight("web"))
    monkeypatch.setattr(settings, "booking_target_mode", "sandbox")
    monkeypatch.setattr(settings, "sandbox_clinic_email", "")
    assert any("SANDBOX_CLINIC_EMAIL" in p for p in tb.preflight("email"))
    monkeypatch.setattr(settings, "sandbox_clinic_phone", "")
    assert any("SANDBOX_CLINIC_PHONE" in p for p in tb.preflight("phone"))
    monkeypatch.setattr(webmod, "chromium_installed", lambda headed=False: not headed)
    monkeypatch.setattr(settings, "web_booking_headed", True)
    assert any("full Chromium" in p for p in tb.preflight("web"))
    monkeypatch.setattr(settings, "web_booking_headed", False)
    assert tb.preflight("web") == []


def test_booking_script_refuses_live_mode_before_planning(monkeypatch, capsys):
    from scripts import test_booking as tb

    for k in ("sandbox_clinic_website", "web_booking_enabled", "web_booking_headed", "web_booking_slow_mo_ms"):
        monkeypatch.setattr(settings, k, getattr(settings, k))  # restored after apply_overrides
    monkeypatch.setattr(settings, "booking_target_mode", "live")
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not prompt"))
    assert tb.main(["--channel", "web"]) == 1
    assert "BOOKING_TARGET_MODE must be sandbox" in capsys.readouterr().out


def test_phone_wrapper_forces_phone(monkeypatch):
    from scripts import test_booking as tb
    from scripts import test_phone_call

    got = {}
    monkeypatch.setattr(tb, "run", lambda args: got.setdefault("channel", args.channel) and 0)
    monkeypatch.setattr(test_phone_call, "booking_main", lambda argv: tb.main(argv))
    test_phone_call.main()
    assert got["channel"] == "phone"
