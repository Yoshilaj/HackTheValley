"""Website channel detection on static HTML fixtures (no network)."""

from pathlib import Path

import pytest

from app.services.booking.channels.detect import FetchResponse, analyze_html, detect_channels

FIX = Path(__file__).parent / "fixtures" / "booking"
BASE = "https://clinic.example.com"


def page(name: str) -> str:
    return (FIX / name).read_text()


def test_bookable_form_with_optional_health_card_field():
    a = analyze_html(page("form.html"), f"{BASE}/mock-clinic/book")
    assert a.kind == "web_form" and a.reasons == []
    names = {f["name"]: f for f in a.form["fields"]}
    assert {"full_name", "dob", "email", "phone", "health_card", "slot", "reason"} <= set(names)
    assert "csrf" not in names  # hidden inputs are not fields
    assert names["full_name"]["required"] and not names["health_card"]["required"]
    assert a.form["method"] == "post" and a.form["action"] == f"{BASE}/mock-clinic/book"


@pytest.mark.parametrize("fixture,reason", [
    ("login.html", "login required"),
    ("captcha.html", "captcha"),
    ("captcha_iframe.html", "captcha"),
    ("health_card.html", "health-card number required"),
    ("payment.html", "payment required"),
])
def test_blocked_pages(fixture, reason):
    a = analyze_html(page(fixture), f"{BASE}/x")
    assert a.kind == "web_blocked"
    assert reason in a.reasons


def test_external_portal_is_blocked():
    a = analyze_html(page("jane.html"), BASE)
    assert a.kind == "web_blocked" and "Jane App" in a.reason


def test_mailto_and_text_emails_extracted():
    a = analyze_html(page("email_only.html"), BASE)
    assert a.kind == "none"
    assert a.emails == ["referrals@queenwestcardio.example.com", "reception@queenwestcardio.example.com"]
    login = analyze_html(page("login.html"), BASE)
    assert login.emails == ["intake@midtownmemory.example.com"]


def test_home_page_links_and_search_form_ignored():
    a = analyze_html(page("home.html"), f"{BASE}/mock-clinic")
    assert a.kind == "none"
    assert a.links == [f"{BASE}/mock-clinic/book", f"{BASE}/mock-clinic/contact"]  # same host, booking-ish only


def _fetcher(pages: dict[str, tuple[int, str]], calls: list[str]):
    def fetch(url: str, timeout: float) -> FetchResponse:
        calls.append(url)
        status, body = pages.get(url, (404, ""))
        return FetchResponse(status=status, text=body, url=url, content_type="text/html" if status < 400 else "")
    return fetch


def test_crawl_finds_form_on_linked_page():
    calls: list[str] = []
    fetch = _fetcher({f"{BASE}/mock-clinic": (200, page("home.html")),
                      f"{BASE}/mock-clinic/book": (200, page("form.html")),
                      f"{BASE}/mock-clinic/contact": (200, page("email_only.html"))}, calls)
    r = detect_channels(f"{BASE}/mock-clinic", fetch=fetch)
    assert r.web == "web_form" and r.form_url == f"{BASE}/mock-clinic/book"
    assert "referrals@queenwestcardio.example.com" in r.emails
    assert calls[0] == f"{BASE}/robots.txt" and len(calls) <= 6


def test_crawl_blocked_and_robots_respected():
    calls: list[str] = []
    fetch = _fetcher({f"{BASE}/robots.txt": (200, "User-agent: *\nDisallow: /private\n"),
                      f"{BASE}/": (200, '<a href="/private/book">Book</a><a href="/book">Book now</a>'),
                      f"{BASE}/book": (200, page("login.html"))}, calls)
    r = detect_channels(f"{BASE}/", fetch=fetch)
    assert r.web == "web_blocked" and r.reason == "login required"
    assert r.robots_disallowed == [f"{BASE}/private/book"] and f"{BASE}/private/book" not in calls


def test_crawl_page_limit_and_errors():
    links = "".join(f'<a href="/book{i}">Book {i}</a>' for i in range(10))
    calls: list[str] = []
    pages = {f"{BASE}/": (200, links)} | {f"{BASE}/book{i}": (200, links) for i in range(10)}
    r = detect_channels(f"{BASE}/", fetch=_fetcher(pages, calls), max_pages=5)
    assert len(r.pages) == 5 and r.web == "none"

    def boom(url, timeout):
        raise OSError("connection refused")
    r = detect_channels(f"{BASE}/", fetch=boom)
    assert r.web == "none" and r.reason == "website unreachable"
    assert detect_channels(None).reason == "no website"
    assert detect_channels("ftp://x").reason == "unsupported URL"


def test_hospital_info_page_with_search_and_newsletter_is_not_a_booking_form():
    """Regression: https://www.uhn.ca/PMCC (hospital information page) was labelled web_form."""
    a = analyze_html(page("hospital_info.html"), "https://www.uhn.ca/PMCC")
    assert a.kind == "none" and a.form is None


@pytest.mark.parametrize("html,expected", [
    # name + e-mail + a time choice for the visit
    ('<form><input name="name"><input type="email" name="email"><input type="datetime-local" name="when">'
     '<button>Send</button></form>', "web_form"),
    # explicit "Request an appointment" submit
    ('<form action="/contact"><input name="full_name"><input type="tel" name="phone">'
     '<button type="submit">Request an appointment</button></form>', "web_form"),
    # only a date of birth: not a time choice; generic contact submit
    ('<form><input name="name"><input type="email" name="email"><input type="date" name="dob">'
     '<textarea name="message"></textarea><button>Send</button></form>', "none"),
    # no contact field
    ('<form><input name="name"><select name="appointment_time"><option>x</option></select>'
     '<button>Book</button></form>', "none"),
])
def test_booking_form_semantics(html, expected):
    assert analyze_html(f"<p>Book an appointment</p>{html}", BASE).kind == expected


# ---------------------------------------------------------------- SSRF guard / limits (no network)

import contextlib  # noqa: E402
import socket  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.booking.channels import detect  # noqa: E402

PUBLIC_IP = "93.184.216.34"


def _resolver(table):
    def getaddrinfo(host, port, *a, **k):
        if host not in table:
            raise socket.gaierror("unknown host")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in table[host]]
    return getaddrinfo


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://10.0.0.5/admin", "http://169.254.169.254/latest/meta-data", "http://[::1]:8000/",
    "http://[fe80::1]/", "http://0.0.0.0/", "http://224.0.0.1/", "http://[::ffff:127.0.0.1]/", "http://192.168.1.1/",
    "ftp://clinic.example.com/", "file:///etc/passwd", "http://internal.example.com/",
])
def test_check_public_url_rejects_non_public(url, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver({"internal.example.com": ["10.1.2.3"]}))
    with pytest.raises(detect.UnsafeURL):
        detect.check_public_url(url)


def test_check_public_url_any_private_answer_rejects(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver({"clinic.example.com": [PUBLIC_IP],
                                                          "mixed.example.com": [PUBLIC_IP, "127.0.0.1"]}))
    detect.check_public_url("https://clinic.example.com/book")
    with pytest.raises(detect.UnsafeURL):
        detect.check_public_url("https://mixed.example.com/")


class _FakeResp:
    def __init__(self, status=200, headers=None, chunks=(b"<html></html>",)):
        self.status_code, self.headers, self._chunks, self.encoding = status, headers or {}, chunks, "utf-8"

    def iter_bytes(self):
        yield from self._chunks


def _fake_stream(routes, seen):
    @contextlib.contextmanager
    def stream(method, url, **kw):
        assert kw.get("follow_redirects") is False
        seen.append(url)
        yield routes[url]
    return stream


def test_http_fetch_redirects_checked_each_hop(monkeypatch):
    import httpx

    monkeypatch.setattr(socket, "getaddrinfo", _resolver({"clinic.example.com": [PUBLIC_IP],
                                                          "www.clinic.example.com": [PUBLIC_IP]}))
    seen: list[str] = []
    routes = {"https://clinic.example.com/": _FakeResp(301, {"location": "https://www.clinic.example.com/"}),
              "https://www.clinic.example.com/": _FakeResp(302, {"location": "http://169.254.169.254/x"})}
    monkeypatch.setattr(httpx, "stream", _fake_stream(routes, seen))
    with pytest.raises(detect.UnsafeURL):  # same-site hop OK, then off-site/metadata hop refused
        detect.http_fetch("https://clinic.example.com/")
    assert seen == ["https://clinic.example.com/", "https://www.clinic.example.com/"]

    loop = {f"https://clinic.example.com/{i}": _FakeResp(302, {"location": f"/{i + 1}"}) for i in range(10)}
    monkeypatch.setattr(httpx, "stream", _fake_stream(loop, []))
    with pytest.raises(detect.UnsafeURL, match="redirects"):
        detect.http_fetch("https://clinic.example.com/0")


def test_http_fetch_caps_body_and_reports_final_url(monkeypatch):
    import httpx

    monkeypatch.setattr(socket, "getaddrinfo", _resolver({"clinic.example.com": [PUBLIC_IP]}))
    big = _FakeResp(200, {"content-type": "text/html"}, chunks=[b"a" * 400_000] * 10)  # 4 MB
    monkeypatch.setattr(httpx, "stream", _fake_stream({"https://clinic.example.com/": big}, []))
    r = detect.http_fetch("https://clinic.example.com/")
    assert len(r.text) == detect.MAX_BODY_BYTES and r.url == "https://clinic.example.com/"


def test_base_url_shortcut_only_for_the_mock_clinic(monkeypatch):
    import httpx

    base = settings.base_url.rstrip("/")
    monkeypatch.setattr(socket, "getaddrinfo", _resolver({}))  # any real resolution fails
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("network used"))
    r = detect.http_fetch(f"{base}/mock-clinic")
    assert r.status == 200 and "Queen West" in r.text
    with pytest.raises(detect.UnsafeURL):
        detect.http_fetch(f"{base}/api/admin/appointments/x/confirm-links")
