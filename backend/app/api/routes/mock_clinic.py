"""Mock clinic website for the booking agent's web channel (booking-v2 spec §3).

A fictional small Toronto clinic ("Queen West Family & Memory Clinic") served by the backend so
the web channel (app/services/booking/channels/web.py) can be exercised end to end without ever
touching a real clinic website. Sandbox mode sends every website booking here
(`sandbox.resolve_target("web", …)` → `BASE_URL/mock-clinic`).

Variants (each one only links inside its own prefix, so website detection classifies it alone):

    GET  /mock-clinic                       home: services, hours, phone, "Book online"
    GET  /mock-clinic/book                  booking form (slots = open 30-min slots, next 14 days)
    POST /mock-clinic/book                  validate → store → 303 to the confirmation page
    GET  /mock-clinic/confirmation/{ref}    "Your appointment is booked", reference number
    GET  /mock-clinic/email-only            no form; appointment requests by e-mail (mailto)
    GET  /mock-clinic/login                 booking needs a patient account (sign-in / register wall)
    GET  /mock-clinic/captcha               form behind a CAPTCHA widget (static placeholder iframe)
    GET  /mock-clinic/health-card           form that requires an Ontario health card number
    GET  /mock-clinic/admin/bookings        JSON list of submissions (dev mode or X-Admin-Secret)
    GET  /mock-clinic/admin                 live "front desk" page: bookings newest first, refreshes
                                            every 3 s (dev mode, X-Admin-Secret, or ?key=ADMIN_SECRET)
    POST /mock-clinic/admin/clear           delete all demo bookings (same auth + X-Front-Desk: 1)

Submissions live in memory and are mirrored to `<STORAGE_DIR>/mock_clinic/bookings.json` (no DB
table). A health card number is never stored — only that one was provided.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.auth import require_admin_or_dev
from app.core.config import settings
from app.services.booking.slots import candidate_slots, zone_or_default

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "mock_clinic"
_HEADERS = {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"}

CLINIC: dict[str, Any] = {
    "name": "Queen West Family & Memory Clinic",
    "tagline": "Family medicine, memory care and heart health in the heart of Queen West.",
    "address": "1088 Queen St W, Toronto, ON M6J 1H8",
    "phone": "416-555-0142",
    "fax": "416-555-0143",
    "email": "frontdesk@queenwestclinic.example.com",
    "timezone": "America/Toronto",
    "hours": {"mon": [["08:00", "12:00"], ["13:00", "19:00"]], "tue": [["08:00", "12:00"], ["13:00", "19:00"]],
              "wed": [["08:00", "12:00"], ["13:00", "19:00"]], "thu": [["08:00", "12:00"], ["13:00", "19:00"]],
              "fri": [["08:00", "12:00"], ["13:00", "17:00"]], "sat": [["09:00", "13:00"]]},
    "services": [
        ("Family medicine", "Check-ups, prescription renewals and everyday care for the whole family."),
        ("Memory clinic", "Memory and thinking assessments with our geriatric and family physicians."),
        ("Heart & blood pressure", "Blood-pressure reviews, ECGs and heart-health consultations."),
        ("Neurology consultations", "Visiting neurologist for movement and nerve concerns (referral helpful)."),
    ],
}
REASONS = ["General check-up", "Memory consultation", "Heart and blood-pressure check-up",
           "Neurology consultation", "Prescription renewal", "Other"]
DAY_NAMES = [("mon", "Monday"), ("tue", "Tuesday"), ("wed", "Wednesday"), ("thu", "Thursday"), ("fri", "Friday"),
             ("sat", "Saturday"), ("sun", "Sunday")]
POSTAL_RE = re.compile(r"^[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HEALTH_CARD_RE = re.compile(r"^\d{4}[ -]?\d{3}[ -]?\d{3}(?:[ -]?[A-Za-z]{2})?$")

#: variant key -> settings. "" is the standard site at /mock-clinic.
VARIANTS: dict[str, dict[str, Any]] = {
    "": {"label": "standard", "booking": "form"},
    "email-only": {"label": "email-only", "booking": "email"},
    "login": {"label": "login", "booking": "login"},
    "captcha": {"label": "captcha", "booking": "form", "captcha": True},
    "health-card": {"label": "health-card", "booking": "form", "health_card_required": True},
}

router = APIRouter(prefix="/mock-clinic", tags=["mock-clinic"])
_env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]),
                   trim_blocks=True, lstrip_blocks=True)


# ================================================================ submissions store

class BookingStore:
    """In-memory list mirrored to a JSON file (thread-safe; the demo has a single worker)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] | None = None

    @property
    def path(self) -> Path:
        return Path(settings.storage_dir) / "mock_clinic" / "bookings.json"

    def _load(self) -> list[dict[str, Any]]:
        if self._items is None:
            try:
                self._items = list(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                self._items = []
        return self._items

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(x) for x in self._load()]

    def add(self, item: dict[str, Any]) -> None:
        with self._lock:
            items = self._load()
            items.append(item)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(items, indent=1), encoding="utf-8")
            except OSError:
                pass  # in-memory copy still serves the demo

    def get(self, ref: str) -> dict[str, Any] | None:
        return next((x for x in self.all() if x["reference"] == ref), None)

    def taken(self) -> set[str]:
        return {x["slot_start"] for x in self.all() if x.get("slot_start")}

    def reset(self) -> int:
        with self._lock:
            n = len(self._load())
            self._items = []
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            return n


store = BookingStore()


# ================================================================ helpers

def _prefix(variant: str) -> str:
    return "/mock-clinic" + (f"/{variant}" if variant else "")


def _hours_rows() -> list[tuple[str, str]]:
    rows = []
    for key, label in DAY_NAMES:
        spans = CLINIC["hours"].get(key) or []
        rows.append((label, ", ".join(f"{_ampm(a)} – {_ampm(b)}" for a, b in spans) if spans else "Closed"))
    return rows


def _ampm(hm: str) -> str:
    h, m = (int(x) for x in hm.split(":"))
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _pretaken(start: datetime) -> bool:
    """~15 % of slots are already booked by other patients (deterministic per slot)."""
    return hashlib.sha256(start.isoformat().encode()).digest()[0] < 38


def open_slots(now: datetime | None = None) -> list[dict[str, str]]:
    """Open 30-minute slots for the next 14 days: [{value (ISO, clinic tz), label, day}]."""
    zone = zone_or_default(CLINIC["timezone"])
    taken = store.taken()
    out = []
    for s in candidate_slots(CLINIC["hours"], CLINIC["timezone"], now=now, days=14, lead=timedelta(hours=2)):
        start = s.start.astimezone(zone)
        iso = start.isoformat()
        if iso in taken or _pretaken(start):
            continue
        out.append({"value": iso, "label": f"{start:%a %b} {start.day}, {_ampm(f'{start:%H:%M}')}",
                    "day": f"{start:%A, %B} {start.day}"})
    return out


def _grouped(slots: list[dict[str, str]]) -> list[tuple[str, list[dict[str, str]]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for s in slots:
        groups.setdefault(s["day"], []).append(s)
    return list(groups.items())


def _render(name: str, status_code: int = 200, **ctx: Any) -> HTMLResponse:
    html = _env.get_template(name).render(clinic=CLINIC, hours=_hours_rows(), **ctx)
    return HTMLResponse(html, status_code=status_code, headers=_HEADERS)


def _new_reference() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        ref = "QW-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if store.get(ref) is None:
            return ref


def validate_booking(form: dict[str, str], variant: str, slots: list[dict[str, str]]) -> list[str]:
    """Server-side validation of a booking submission → list of error messages."""
    v = VARIANTS[variant]
    errors = []
    for key, label in (("first_name", "First name"), ("last_name", "Last name"), ("date_of_birth", "Date of birth"),
                       ("phone", "Phone"), ("email", "E-mail"), ("postal_code", "Postal code"),
                       ("reason", "Reason for visit"), ("appointment_time", "Appointment time")):
        if not form.get(key, "").strip():
            errors.append(f"{label} is required.")
    if form.get("email") and not EMAIL_RE.match(form["email"].strip()):
        errors.append("Please enter a valid e-mail address.")
    if form.get("postal_code") and not POSTAL_RE.match(form["postal_code"].strip()):
        errors.append("Please enter a valid Canadian postal code (e.g. M6J 1H8).")
    if form.get("date_of_birth"):
        try:
            if date.fromisoformat(form["date_of_birth"]) >= date.today():
                errors.append("Date of birth must be in the past.")
        except ValueError:
            errors.append("Please enter the date of birth as YYYY-MM-DD.")
    if form.get("reason") and form["reason"] not in REASONS:
        errors.append("Please choose a reason for the visit.")
    if form.get("appointment_time") and form["appointment_time"] not in {s["value"] for s in slots}:
        errors.append("That appointment time is no longer available. Please choose another time.")
    if form.get("consent") != "yes":
        errors.append("Please confirm that we may contact you about this appointment.")
    if v.get("health_card_required"):
        hc = form.get("health_card", "").strip()
        if not hc or not HEALTH_CARD_RE.match(hc):
            errors.append("A valid Ontario health card number is required.")
    if v.get("captcha"):
        errors.append("CAPTCHA verification failed. Please complete the CAPTCHA and try again.")
    return errors


# ================================================================ pages

def _home(variant: str):
    def view() -> HTMLResponse:
        return _render("home.html.j2", p=_prefix(variant), variant=VARIANTS[variant])
    return view


def _book_page(variant: str):
    def view() -> HTMLResponse:
        v = VARIANTS[variant]
        if v["booking"] == "login":
            return _render("login.html.j2", p=_prefix(variant), variant=v)
        if v["booking"] == "email":
            return _render("email_only.html.j2", p=_prefix(variant), variant=v)
        return _render("book.html.j2", p=_prefix(variant), variant=v, slot_groups=_grouped(open_slots()),
                       reasons=REASONS, form={}, errors=[])
    return view


def _book_submit(variant: str):
    async def view(request: Request):
        v = VARIANTS[variant]
        raw = await request.form()
        form = {k: str(raw.get(k) or "") for k in raw.keys()}
        if v["booking"] == "login":
            return _render("login.html.j2", status_code=401, p=_prefix(variant), variant=v,
                           errors=["The e-mail or password is incorrect."])
        if v["booking"] != "form":
            raise HTTPException(405, "This clinic does not take online bookings.")
        slots = open_slots()
        errors = validate_booking(form, variant, slots)
        if errors:
            return _render("book.html.j2", status_code=422, p=_prefix(variant), variant=v,
                           slot_groups=_grouped(slots), reasons=REASONS, form=form, errors=errors)
        ref = _new_reference()
        start = datetime.fromisoformat(form["appointment_time"])
        store.add({
            "reference": ref, "variant": v["label"], "status": "confirmed",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "first_name": form["first_name"].strip(), "last_name": form["last_name"].strip(),
            "date_of_birth": form["date_of_birth"], "phone": form["phone"].strip(), "email": form["email"].strip(),
            "postal_code": form["postal_code"].strip().upper(), "reason": form["reason"],
            "slot_start": start.isoformat(), "slot_end": (start + timedelta(minutes=30)).isoformat(),
            "notes": form.get("notes", "").strip()[:1000] or None,
            "health_card_provided": bool(form.get("health_card", "").strip()),
            "consent": form.get("consent") == "yes",
            "user_agent": request.headers.get("user-agent", "")[:200],
        })
        return RedirectResponse(f"/mock-clinic/confirmation/{ref}", status_code=303)
    return view


@router.get("/confirmation/{ref}", response_class=HTMLResponse, include_in_schema=False)
def confirmation(ref: str) -> HTMLResponse:
    item = store.get(ref)
    if item is None:
        raise HTTPException(404, "Booking not found")
    start = datetime.fromisoformat(item["slot_start"])
    variant = next((k for k, v in VARIANTS.items() if v["label"] == item["variant"]), "")
    return _render("confirmation.html.j2", p=_prefix(variant), variant=VARIANTS[variant], booking=item,
                   when=f"{start:%A, %B} {start.day}, {start.year} at {_ampm(f'{start:%H:%M}')}")


@router.get("/widget/captcha", response_class=HTMLResponse, include_in_schema=False)
def captcha_widget() -> HTMLResponse:
    """Static placeholder for the CAPTCHA iframe (no real CAPTCHA service, nothing to solve)."""
    return _render("captcha_widget.html.j2")


@router.get("/login/register", response_class=HTMLResponse, include_in_schema=False)
def login_register() -> HTMLResponse:
    return _render("login.html.j2", p=_prefix("login"), variant=VARIANTS["login"], register=True)


def _newest_first() -> list[dict[str, Any]]:
    return sorted(store.all(), key=lambda x: x["submitted_at"], reverse=True)


@router.get("/admin/bookings")
def admin_bookings(_: None = Depends(require_admin_or_dev)) -> dict[str, Any]:
    """Submissions to the mock clinic, newest first → {count, bookings: [...]} (dev mode or X-Admin-Secret)."""
    items = _newest_first()
    return {"count": len(items), "bookings": items}


def require_front_desk(x_admin_secret: str | None = Header(default=None),
                       key: str | None = Query(default=None, max_length=256)) -> None:
    """Dev mode, or ADMIN_SECRET as the X-Admin-Secret header or `?key=` (constant-time compare)."""
    if settings.dev_mode:
        return
    for cand in (x_admin_secret, key):
        if cand and hmac.compare_digest(cand.encode(), settings.admin_secret.encode()):
            return
    raise HTTPException(403, "Admin secret required")


def _when(iso: str | None, fmt: str = "slot") -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso).astimezone(zone_or_default(CLINIC["timezone"]))
    except ValueError:
        return iso
    if fmt == "time":
        return f"{dt:%b} {dt.day}, {(dt.hour - 1) % 12 + 1}:{dt:%M:%S} {'AM' if dt.hour < 12 else 'PM'}"
    return f"{dt:%a %b} {dt.day}, {_ampm(f'{dt:%H:%M}')}"


def front_desk_rows(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What the front-desk page shows per booking (no health card number, only provided / not)."""
    return [{
        "reference": x.get("reference", ""), "received": _when(x.get("submitted_at"), "time"),
        "name": f"{x.get('first_name', '')} {x.get('last_name', '')}".strip(), "dob": x.get("date_of_birth", ""),
        "phone": x.get("phone", ""), "email": x.get("email", ""), "postal_code": x.get("postal_code", ""),
        "reason": x.get("reason", ""), "slot": _when(x.get("slot_start")),
        "consent": "yes" if x.get("consent", True) else "no",  # every accepted booking needed consent
        "health_card": "provided" if x.get("health_card_provided") else "not provided",
        "variant": x.get("variant", ""),
    } for x in items]


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def front_desk(_: None = Depends(require_front_desk)) -> HTMLResponse:
    """Live "front desk" view for watching bookings arrive during a demo (auto-refresh 3 s)."""
    return _render("admin.html.j2", p=_prefix(""), variant=VARIANTS[""], rows=front_desk_rows(_newest_first()))


@router.get("/admin/feed", include_in_schema=False)
def front_desk_feed(_: None = Depends(require_front_desk)) -> dict[str, Any]:
    """The front-desk rows as JSON (what the page polls every 3 s)."""
    rows = front_desk_rows(_newest_first())
    return {"count": len(rows), "rows": rows}


@router.post("/admin/clear", include_in_schema=False)
def front_desk_clear(_: None = Depends(require_front_desk),
                     x_front_desk: str | None = Header(default=None)) -> dict[str, int]:
    """Delete all demo bookings. Needs the X-Front-Desk: 1 header (set by the page's fetch), so a
    plain cross-site form post cannot clear them even in dev mode."""
    if x_front_desk != "1":
        raise HTTPException(400, "X-Front-Desk header required")
    return {"cleared": store.reset()}


for _variant in VARIANTS:
    _p = f"/{_variant}" if _variant else ""
    router.add_api_route(_p or "", _home(_variant), methods=["GET"], response_class=HTMLResponse,
                         include_in_schema=False)
    router.add_api_route(f"{_p}/", _home(_variant), methods=["GET"], response_class=HTMLResponse,
                         include_in_schema=False)
    router.add_api_route(f"{_p}/book", _book_page(_variant), methods=["GET"], response_class=HTMLResponse,
                         include_in_schema=False)
    router.add_api_route(f"{_p}/book", _book_submit(_variant), methods=["POST"], include_in_schema=False)
