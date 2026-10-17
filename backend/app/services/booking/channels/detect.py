"""Website channel detection (booking-v2 spec §1.4).

    page = analyze_html(html, url)                  # pure: PageAnalysis
    result = detect_channels(website, fetch=None)   # crawls ≤5 same-domain pages: DetectionResult
    result.web       # "web_form" | "web_blocked" | "none"
    result.reason    # why blocked / why none
    result.form_url  # page holding the bookable form (web_form)
    result.emails    # addresses found (mailto: links and text)

Classification of one page (`analyze_html`):
  web_blocked  - a password field or "log in / sign in / create an account to book" wording
                 (login), a CAPTCHA widget (reCAPTCHA / hCaptcha / Turnstile / FriendlyCaptcha
                 scripts, iframes or `data-sitekey`), a *required* health-card / OHIP / PHN /
                 government-ID field, payment-card fields, or an external booking portal that needs
                 a patient account (Jane App, Ocean, Cortico, …). The agent never tries to get past
                 any of these.
  web_form     - an HTML form with appointment-booking semantics (`is_booking_form`): a name and an
                 e-mail / phone field AND a date / time choice for the visit (not the date of
                 birth) or an explicit "Book / Request an appointment" submit / booking action.
                 Search, contact-us, newsletter and donation forms are not booking forms.
  none         - neither.
A site is `web_form` when any crawled page has a clean form; otherwise `web_blocked` when any
booking page is blocked; otherwise `none`.

Crawl limits: http(s) only, same host (www. ignored), ≤ 5 pages, 10 s overall budget (per-request
deadline ≤ 10 s), text/html only, body ≤ 1 MB (streamed), ≤ 3 manual same-site redirects, every
hop's host resolved and refused unless all its addresses are public (no private / loopback /
link-local / reserved / multicast / unspecified IPv4 / IPv6 — SSRF guard), robots.txt respected
(user agent `HealthAdvocateBookingBot`; an unreachable robots.txt counts as allow-all, a 401/403 as
disallow-all). GET only. Only the built-in mock clinic (`BASE_URL/mock-clinic/…`) is fetched
in-process. The fetcher is injectable for tests.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from app.core.logging import get_logger

log = get_logger(__name__)

USER_AGENT = "HealthAdvocateBookingBot/1.0 (+appointment request assistant)"
ROBOTS_AGENT = "HealthAdvocateBookingBot"
MAX_PAGES = 5
BUDGET_S = 10.0

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
CAPTCHA_SRC = re.compile(r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare\.com|friendlycaptcha|captcha", re.I)
CAPTCHA_CLASS = re.compile(r"g-recaptcha|h-captcha|cf-turnstile|frc-captcha|captcha", re.I)
LOGIN_TEXT = re.compile(
    r"(log ?in|sign ?in|create an account|register for an account|patient portal account)\s+(to|before you)\s+"
    r"(book|request|schedule|continue|make)|login required|must be (logged|signed) in|please (log|sign) ?in\b",
    re.I)
HEALTH_CARD = re.compile(r"health[\s_-]*card|health[\s_-]*(number|no\b)|ohip|\bhcn\b|\bphn\b|personal health number|"
                         r"version[\s_-]*code|carte[\s_-]*sant|ramq|\bsin\b|social insurance|passport|driver'?s licen", re.I)
PAYMENT = re.compile(r"cc-number|cc-csc|cc-exp|card[\s_-]*number|credit[\s_-]*card|\bcvv\b|\bcvc\b|security code", re.I)
EXTERNAL_PORTALS = {"janeapp.com": "Jane App", "ocean.cognisantmd.com": "Ocean", "cortico.ca": "Cortico",
                    "mindbodyonline.com": "Mindbody", "zocdoc.com": "Zocdoc", "medeohealth.com": "Medeo",
                    "telus.com/health": "TELUS Health"}
BOOKING_WORDS = re.compile(r"\bbook|appointment|request a visit|request an? (visit|consult)|schedul|referral|intake", re.I)
LINK_WORDS = re.compile(r"book|appointment|request|contact|referral|schedul|intake|new patient", re.I)


@dataclass
class FormField:
    tag: str
    type: str
    name: str
    label: str
    required: bool

    def text(self) -> str:
        return f"{self.name} {self.label}"


@dataclass
class FormInfo:
    action: str | None
    method: str
    fields: list[FormField] = field(default_factory=list)
    has_submit: bool = False
    text: str = ""
    submit_text: str = ""


@dataclass
class PageAnalysis:
    url: str
    kind: str                         # web_form | web_blocked | none
    reasons: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    form: dict[str, Any] | None = None
    links: list[str] = field(default_factory=list)   # same-host candidate booking links

    @property
    def reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None


@dataclass
class DetectionResult:
    website: str | None
    web: str                          # web_form | web_blocked | none
    reason: str | None = None
    form_url: str | None = None
    form: dict[str, Any] | None = None
    emails: list[str] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    robots_disallowed: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ================================================================ HTML parsing (stdlib)

class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[FormInfo] = []
        self._form: FormInfo | None = None
        self.labels_for: dict[str, str] = {}
        self._label_for: str | None = None
        self._label_buf: list[str] = []
        self._in_label = False
        self._pending_unlabeled: list[FormField] = []
        self.anchors: list[tuple[str, str]] = []
        self._a_href: str | None = None
        self._a_buf: list[str] = []
        self.srcs: list[str] = []
        self.classes: list[str] = []
        self.sitekeys = 0
        self.password = False
        self.text: list[str] = []
        self._skip = 0
        self._field_by_id: dict[str, FormField] = {}
        self._label_fields: list[FormField] = []
        self._in_button = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript"):
            if tag == "script" and a.get("src"):
                self.srcs.append(a["src"])
            self._skip += 1
            return
        if a.get("class"):
            self.classes.append(a["class"])
        if "data-sitekey" in a:
            self.sitekeys += 1
        if tag in ("iframe", "img", "embed") and a.get("src"):
            self.srcs.append(a["src"])
        if tag == "link" and a.get("href"):
            self.srcs.append(a["href"])
        if tag == "a":
            self._a_href, self._a_buf = a.get("href", ""), []
        elif tag == "form":
            self._form = FormInfo(action=a.get("action") or None, method=(a.get("method") or "get").lower())
            self.forms.append(self._form)
        elif tag == "label":
            self._in_label, self._label_for, self._label_buf = True, a.get("for"), []
            self._label_fields = []
        elif tag in ("input", "textarea", "select", "button"):
            typ = (a.get("type") or ("submit" if tag == "button" else tag if tag != "input" else "text")).lower()
            if typ == "password":
                self.password = True
            if self._form is None:
                return
            if typ in ("submit", "image") or tag == "button" and typ != "button":
                self._form.has_submit = True
                if tag == "button":
                    self._in_button = True
                else:
                    self._form.submit_text += " " + (a.get("value") or a.get("alt") or "")
                return
            if typ in ("hidden", "button", "reset"):
                return
            label = " ".join(x for x in (a.get("placeholder"), a.get("aria-label"), a.get("autocomplete"),
                                         a.get("id")) if x)
            f = FormField(tag=tag, type=typ, name=a.get("name") or a.get("id") or "", label=label,
                          required="required" in a or a.get("aria-required") == "true")
            self._form.fields.append(f)
            if a.get("id"):
                self._field_by_id[a["id"]] = f
            if self._in_label:
                self._label_fields.append(f)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a" and self._a_href is not None:
            self.anchors.append((self._a_href, " ".join(self._a_buf).strip()))
            self._a_href = None
        elif tag == "form":
            self._form = None
        elif tag == "button":
            self._in_button = False
        elif tag == "label" and self._in_label:
            text = " ".join(self._label_buf).strip()
            if self._label_for:
                self.labels_for[self._label_for] = text
            for f in self._label_fields:
                f.label = f"{text} {f.label}".strip()
                if "*" in text or "required" in text.lower():
                    f.required = True
            self._in_label = False

    def handle_data(self, data):
        if self._skip:
            return
        t = data.strip()
        if not t:
            return
        self.text.append(t)
        if self._a_href is not None:
            self._a_buf.append(t)
        if self._in_label:
            self._label_buf.append(t)
        if self._form is not None:
            self._form.text += " " + t
            if self._in_button:
                self._form.submit_text += " " + t

    def finish(self) -> None:
        for fid, text in self.labels_for.items():
            f = self._field_by_id.get(fid)
            if f is not None:
                f.label = f"{text} {f.label}".strip()
                if "*" in text or "required" in text.lower():
                    f.required = True


def _emails(text: str, anchors: list[tuple[str, str]]) -> list[str]:
    found: list[str] = []
    for href, _ in anchors:
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?")[0].strip()
            if EMAIL_RE.fullmatch(addr):
                found.append(addr.lower())
    for m in EMAIL_RE.finditer(text):
        e = m.group(0).rstrip(".").lower()
        if not e.endswith(_IMG_EXT):
            found.append(e)
    return list(dict.fromkeys(found))


def _is_search_form(form: FormInfo) -> bool:
    names = {f.name.lower() for f in form.fields}
    return (len(form.fields) <= 2 and (any(f.type == "search" for f in form.fields) or names & {"q", "s", "query",
                                                                                                 "search"}))


CONTACT_HINT = re.compile(r"e-?mail|phone|\btel\b|mobile|cell", re.I)
NAME_HINT = re.compile(r"name", re.I)
DOB_HINT = re.compile(r"birth|\bdob\b|bday", re.I)
SLOT_HINT = re.compile(r"date|time|slot|appointment|availab|preferred|when", re.I)
BOOK_SUBMIT = re.compile(r"\bbook|appointment|schedul|request (?:a |an )?(?:visit|consult|referral)|reserve", re.I)
BOOK_ACTION = re.compile(r"book|appointment|schedul|reserv|intake|referral", re.I)
NOT_BOOKING = re.compile(r"newsletter|subscri|sign ?up for (?:our|the) |donat|search|feedback|survey|careers?|"
                         r"job alert|e-?news", re.I)


def _has_slot_choice(form: FormInfo) -> bool:
    """A date / time selection for the appointment itself (a date-of-birth field does not count)."""
    for f in form.fields:
        if DOB_HINT.search(f.text()):
            continue
        if f.type in ("datetime-local", "time"):
            return True
        if f.type == "date" or (f.tag == "select" or f.type == "radio") and SLOT_HINT.search(f.text()):
            return True
    return False


def is_booking_form(form: FormInfo) -> bool:
    """Appointment-booking semantics, not a generic search / contact / newsletter form: patient
    contact fields (a name + an e-mail or phone field) AND either a date / time selection for the
    visit or an explicit "book / request an appointment" submit button / form action."""
    if _is_search_form(form):
        return False
    has_contact = any(f.type in ("email", "tel") or CONTACT_HINT.search(f.text()) for f in form.fields)
    has_name = any(NAME_HINT.search(f.text()) and f.type not in ("email", "tel") for f in form.fields)
    if not (has_contact and has_name):
        return False
    slot_choice = _has_slot_choice(form)
    explicit = bool(BOOK_SUBMIT.search(form.submit_text) or BOOK_ACTION.search(urlsplit(form.action or "").path))
    if NOT_BOOKING.search(f"{form.submit_text} {form.action or ''} {form.text}") and not slot_choice:
        return False
    return slot_choice or explicit


def _form_summary(form: FormInfo, base_url: str) -> dict[str, Any]:
    return {"action": urljoin(base_url, form.action) if form.action else base_url, "method": form.method,
            "fields": [{"name": f.name, "type": f.type, "label": f.label[:120], "required": f.required}
                       for f in form.fields]}


def analyze_html(html: str, url: str) -> PageAnalysis:
    """Classify one HTML page (pure; see module docstring)."""
    p = _Parser()
    try:
        p.feed(html or "")
        p.close()
    except Exception as exc:  # malformed markup: keep what was parsed
        log.info("html parse issue", extra={"url": url, "error": str(exc)})
    p.finish()
    text = " ".join(p.text)
    host = urlsplit(url).netloc.lower()
    reasons: list[str] = []

    if any(CAPTCHA_SRC.search(s) for s in p.srcs) or any(CAPTCHA_CLASS.search(c) for c in p.classes) or p.sitekeys:
        reasons.append("captcha")
    if p.password or LOGIN_TEXT.search(text):
        reasons.append("login required")
    all_fields = [f for form in p.forms for f in form.fields]
    if any(HEALTH_CARD.search(f.text()) and f.required for f in all_fields):
        reasons.append("health-card number required")
    if any(PAYMENT.search(f.text()) and not HEALTH_CARD.search(f.text()) for f in all_fields):
        reasons.append("payment required")
    targets = [href for href, _ in p.anchors] + p.srcs
    for t in targets:
        tl = t.lower()
        portal = next((name for dom, name in EXTERNAL_PORTALS.items() if dom in tl), None)
        if portal and BOOKING_WORDS.search(text + " " + tl):
            reasons.append(f"external booking portal ({portal}) needs a patient account")
            break

    links = []
    for href, label in p.anchors:
        absu = urljoin(url, href.split("#")[0])
        parts = urlsplit(absu)
        if parts.scheme in ("http", "https") and parts.netloc.lower() == host and absu != url \
                and (LINK_WORDS.search(label) or LINK_WORDS.search(parts.path)):
            links.append(absu)
    links = list(dict.fromkeys(links))

    form_dict = None
    bookable = None
    for form in p.forms:
        if is_booking_form(form):
            bookable = form
            break
    if bookable is not None:
        form_dict = _form_summary(bookable, url)
    if reasons:
        kind = "web_blocked"
    elif bookable is not None:
        kind = "web_form"
    else:
        kind = "none"
    return PageAnalysis(url=url, kind=kind, reasons=reasons, emails=_emails(text, p.anchors), form=form_dict,
                        links=links)


# ================================================================ fetching + crawl

@dataclass
class FetchResponse:
    status: int
    text: str
    url: str
    content_type: str = "text/html"


Fetcher = Callable[[str, float], FetchResponse]


def _self_fetch(url: str) -> FetchResponse:
    """URLs on this backend (BASE_URL, e.g. the built-in mock clinic) are served in-process via
    Starlette's TestClient: no socket, no self-request deadlock on a single worker, hermetic tests."""
    from fastapi.testclient import TestClient

    from app.main import app

    parts = urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    resp = TestClient(app, raise_server_exceptions=False).get(path or "/", headers={"User-Agent": USER_AGENT})
    return FetchResponse(status=resp.status_code, text=resp.text, url=url,
                         content_type=resp.headers.get("content-type", ""))


MAX_BODY_BYTES = 1_000_000
MAX_REDIRECTS = 3


class UnsafeURL(ValueError):
    """Refused before any request: non-http(s) scheme or a host resolving to a non-public address."""


def _ip_is_public(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified or not ip.is_global)


def check_public_url(url: str, resolver: Callable[..., Any] | None = None) -> None:
    """Raise UnsafeURL unless `url` is http(s) and EVERY address its host resolves to is public
    (no private / loopback / link-local / reserved / multicast / unspecified IPv4 or IPv6)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURL(f"unsupported URL scheme: {parts.scheme or '-'}")
    host = parts.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addrs = [literal]
    else:
        try:
            infos = (resolver or socket.getaddrinfo)(host, parts.port or (443 if parts.scheme == "https" else 80),
                                                     proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise UnsafeURL(f"cannot resolve {host}") from exc
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    if not addrs or not all(_ip_is_public(a) for a in addrs):
        raise UnsafeURL(f"{host} resolves to a non-public address")


def _same_site(a: str, b: str) -> bool:
    """Same host, ignoring a leading 'www.'."""
    def norm(h: str) -> str:
        h = (h or "").lower()
        return h[4:] if h.startswith("www.") else h
    return norm(urlsplit(a).hostname or "") == norm(urlsplit(b).hostname or "")


def _in_process(url: str) -> bool:
    """Only the built-in mock clinic (BASE_URL/mock-clinic/…) and BASE_URL/robots.txt are served
    in-process; every other BASE_URL path goes through the normal (public-only) fetch."""
    from app.core.config import settings

    base = settings.base_url.rstrip("/")
    if not base:
        return False
    mock = base + "/mock-clinic"
    path_url = url.split("?", 1)[0].split("#", 1)[0]
    return path_url == mock or path_url.startswith(mock + "/") or path_url == base + "/robots.txt"


def http_fetch(url: str, timeout: float = 10.0) -> FetchResponse:
    """Default fetcher (httpx, GET, our user agent). Redirects are followed manually (≤ 3, same
    site only); before every hop the host is resolved and must be public (`check_public_url`); the
    body is streamed and capped at MAX_BODY_BYTES; `timeout` is an overall deadline for this fetch.
    The mock clinic under BASE_URL is fetched in-process. Known gap: DNS can change between the
    check and httpx's own resolution (rebinding) — the check still blocks the common cases."""
    import httpx

    if _in_process(url):
        return _self_fetch(url)
    deadline = time.monotonic() + max(0.5, timeout)
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        check_public_url(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("fetch deadline exceeded")
        with httpx.stream("GET", current, timeout=remaining, follow_redirects=False,
                          headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}) as resp:
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                nxt = urljoin(current, resp.headers["location"])
                if not _same_site(nxt, url):
                    raise UnsafeURL(f"redirect off-site to {urlsplit(nxt).hostname}")
                current = nxt
                continue
            ctype = resp.headers.get("content-type", "")
            body = bytearray()
            if resp.status_code < 400 and ("html" in ctype.lower() or "text/plain" in ctype.lower() or not ctype):
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_BODY_BYTES:
                        del body[MAX_BODY_BYTES:]
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("fetch deadline exceeded")
            text = body.decode(resp.encoding or "utf-8", errors="replace")
            return FetchResponse(status=resp.status_code, text=text, url=current, content_type=ctype)
    raise UnsafeURL(f"more than {MAX_REDIRECTS} redirects")


def _robots(base: str, fetch: Fetcher, timeout: float) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        r = fetch(urljoin(base, "/robots.txt"), timeout)
    except Exception:
        rp.parse([])  # unreachable robots.txt -> allow all
        return rp
    if r.status in (401, 403):
        rp.disallow_all = True
    elif r.status >= 400:
        rp.parse([])
    else:
        rp.parse(r.text.splitlines())
    return rp


def detect_channels(website: str | None, fetch: Fetcher | None = None, max_pages: int = MAX_PAGES,
                    budget_s: float = BUDGET_S) -> DetectionResult:
    """Crawl `website` (see module docstring). Never raises; network errors -> web='none'."""
    if not website:
        return DetectionResult(website=None, web="none", reason="no website")
    parts = urlsplit(website)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return DetectionResult(website=website, web="none", reason="unsupported URL")
    fetch = fetch or http_fetch
    deadline = time.monotonic() + budget_s
    base = f"{parts.scheme}://{parts.netloc}"
    robots = _robots(base, fetch, min(10.0, budget_s))
    queue, seen = [website], set()
    pages: list[PageAnalysis] = []
    disallowed: list[str] = []
    error = None
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if not robots.can_fetch(ROBOTS_AGENT, url):
            disallowed.append(url)
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            error = "time budget exhausted"
            break
        try:
            r = fetch(url, min(10.0, remaining))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:200]
            continue
        if r.status >= 400 or "html" not in (r.content_type or "text/html").lower():
            continue
        if not _same_site(r.url, website):
            continue  # redirected off-site
        page = analyze_html(r.text, r.url)
        pages.append(page)
        queue.extend(link for link in page.links if link not in seen)

    emails = list(dict.fromkeys(e for p in pages for e in p.emails))
    page_log = [{"url": p.url, "kind": p.kind, "reasons": p.reasons} for p in pages]
    clean = next((p for p in pages if p.kind == "web_form"), None)
    if clean is not None:
        return DetectionResult(website=website, web="web_form", form_url=clean.url, form=clean.form, emails=emails,
                               pages=page_log, robots_disallowed=disallowed, error=error)
    blocked = next((p for p in pages if p.kind == "web_blocked"), None)
    if blocked is not None:
        return DetectionResult(website=website, web="web_blocked", reason=blocked.reason, emails=emails,
                               pages=page_log, robots_disallowed=disallowed, error=error)
    reason = ("robots.txt disallows crawling" if disallowed and not pages else
              "website unreachable" if error and not pages else "no booking form found")
    return DetectionResult(website=website, web="none", reason=reason, emails=emails, pages=page_log,
                           robots_disallowed=disallowed, error=error)
