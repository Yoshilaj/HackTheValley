"""Website channel: fills the clinic's online booking form with headless Chromium (spec §1.6 "web").

    ch = WebChannel()
    ch.configured()               # WEB_BOOKING_ENABLED and Playwright + a Chromium build installed
    ch.available(clinic, ctx)     # honours ctx.detection (web_blocked / none), then config, then target
    ch.execute(appt, ctx)         # one booking attempt -> ChannelResult

`execute` (one attempt, hard timeout HARD_TIMEOUT_S, run in its own thread with Playwright's sync API):
  1. target = sandbox.resolve_target("web", clinic): in sandbox the mock clinic
     (SANDBOX_CLINIC_WEBSITE or BASE_URL/mock-clinic; the seed clinics keep their /mock-clinic/...
     variant). Start at ctx.detection["form_url"] when it lies under the target, else target.value.
     In sandbox every request to another host is aborted, so a real clinic site is never loaded;
     live: no off-site pages; sub-resources only from public hosts. Pages are fetched with
     redirects off so each redirect hop is checked (`request_allowed`); a redirected request that
     leaves the allowed hosts aborts the run before submit.
  2. Every loaded page goes through detect.analyze_html: login wall / CAPTCHA / required health
     card / payment ⇒ skipped(<reason>) immediately — never bypassed; the orchestrator falls through
     to e-mail / phone. On a page without a booking form the "Book online" link is followed (≤ 2 hops).
  3. Form fields (label, name, id, type, required, options) → `map_fields`: the LLM
     (`llm.extract`, strict enum schema; it sees field descriptions only, never patient values) maps
     each field to one of MAP_TARGETS; keyword rules (`keyword_target`) are used for FakeLlm / no LLM /
     LLM errors and always win for password / health-card / government-ID / payment fields.
  4. `build_fill_plan` aborts with skipped(reason) when a required field is unknown, has no shared
     value, or is a blocked kind; when a required time select has no option near the approved slots
     (`choose_slot`: exact match, else the closest option on the same day within MAX_SLOT_DRIFT).
     Only values from `PatientInfo.shared_fields()` are typed (minimal disclosure; address parts
     only when required or when there is no postal code); optional unknown or blocked fields stay
     empty. Identity fields from onboarding (legal first / last name, sex, family doctor, has a
     referral, preferred language) are filled when the form asks for them.
  5. Fill → screenshot (storage provider, kind "booking_web") → submit → screenshot → the
     confirmation page is read (`read_confirmation`: LLM, else regex / <time datetime>) →
     confirmed (slot + external_ref = reference number) | requested (pending) | failed (rejected).

Watching a run (local demos): WEB_BOOKING_HEADED=true opens a visible Chromium window (no low-memory
flags), WEB_BOOKING_SLOW_MO_MS delays every browser action, each field is outlined just before it is
filled, and the window stays open HEADED_HOLD_S on the result page. All safety checks stay on; the hard
timeout grows with slow-mo (`hard_timeout_for`). Sandbox runs carry `ngrok-skip-browser-warning: 1`
(`context_options`) so a BASE_URL on ngrok-free shows the clinic, not ngrok's browser interstitial; in
sandbox only the target's origin is ever contacted, and live clinic runs never send it.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations import Clinic, Slot
from app.services.booking import sandbox
from app.services.booking.channels.base import BookingContext, ChannelMessage, ChannelResult, PatientInfo
from app.services.booking.channels.detect import HEALTH_CARD, PAYMENT, analyze_html
from app.services.booking.slots import zone_or_default

log = get_logger(__name__)

HARD_TIMEOUT_S = 60.0
NAV_TIMEOUT_MS = 15_000
ACTION_TIMEOUT_MS = 5_000
MAX_HOPS = 2
MAX_SLOT_DRIFT = timedelta(hours=2)
SLOT_MINUTES = 30
SCREENSHOT_KIND = "booking_web"
CHROMIUM_ARGS = ["--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions", "--no-first-run",
                 "--mute-audio", "--disable-background-networking", "--renderer-process-limit=1",
                 "--js-flags=--max-old-space-size=96"]
#: One browser process instead of browser + renderer + GPU + network (~480 → ~340 MB peak measured for
#: a mock-clinic booking incl. the Playwright node driver). Opt out with WEB_BOOKING_LOW_MEMORY=false
#: (settings.web_booking_low_memory).
LOW_MEMORY_ARGS = ["--single-process", "--no-zygote"]
#: Headed / slow-mo runs: the hard timeout becomes base + fields x slow_mo x SLOW_MO_FACTOR (each field
#: costs a few slowed actions: scroll + highlight + fill), plus the hold on the result page, ≤ this cap.
MAX_HARD_TIMEOUT_S = 300.0
SLOW_MO_FACTOR = 4
ESTIMATED_FIELDS = 12          # until the form has been read
HEADED_HOLD_S = 5.0            # the window stays open this long on the confirmation page
HIGHLIGHT_MS = 1500
NGROK_SKIP_HEADER = "ngrok-skip-browser-warning"


@dataclass(frozen=True)
class BrowserOptions:
    headed: bool = False
    slow_mo_ms: int = 0

    @classmethod
    def from_settings(cls, headed: bool | None = None, slow_mo_ms: int | None = None) -> "BrowserOptions":
        h = settings.web_booking_headed if headed is None else headed
        sm = settings.web_booking_slow_mo_ms if slow_mo_ms is None else slow_mo_ms
        return cls(headed=bool(h), slow_mo_ms=max(0, int(sm or 0)))

    @property
    def watched(self) -> bool:
        return self.headed or self.slow_mo_ms > 0

    def action_timeout_ms(self) -> int:
        return ACTION_TIMEOUT_MS + 2 * self.slow_mo_ms

    def nav_timeout_ms(self) -> int:
        return NAV_TIMEOUT_MS + 2 * self.slow_mo_ms


def chromium_args(headed: bool = False) -> list[str]:
    """Launch flags; the single-process low-memory flags are never used headed (they can break it)."""
    low = settings.web_booking_low_memory and not headed
    return CHROMIUM_ARGS + (LOW_MEMORY_ARGS if low else [])


def launch_options(opts: BrowserOptions) -> dict[str, Any]:
    """Keyword arguments for `pw.chromium.launch` (pure)."""
    out: dict[str, Any] = {"headless": not opts.headed, "args": chromium_args(opts.headed)}
    if opts.slow_mo_ms > 0:
        out["slow_mo"] = opts.slow_mo_ms
    return out


def hard_timeout_for(base_s: float, n_fields: int, opts: BrowserOptions) -> float:
    """The watchdog limit: `base_s` unchanged for normal runs; headed / slow-mo runs get at least
    60 s + fields x slow_mo x 4 (+ the result-page hold when headed), capped at MAX_HARD_TIMEOUT_S."""
    if not opts.watched:
        return base_s
    t = max(base_s, HARD_TIMEOUT_S) + n_fields * opts.slow_mo_ms * SLOW_MO_FACTOR / 1000
    if opts.headed:
        t += HEADED_HOLD_S
    return min(MAX_HARD_TIMEOUT_S, t)


def target_extra_headers(url: str, target_url: str, sandboxed: bool) -> dict[str, str]:
    """Extra request headers for `url`: only requests to the SANDBOX target's own origin get
    `ngrok-skip-browser-warning` (BASE_URL on ngrok-free shows an interstitial to browsers otherwise).
    Live clinic sites and every other host get nothing."""
    if sandboxed and url.startswith(("http://", "https://")) and _same_origin(url, target_url):
        return {NGROK_SKIP_HEADER: "1"}
    return {}


def context_options(target_url: str, sandboxed: bool, tz: str | None) -> dict[str, Any]:
    """Keyword arguments for `browser.new_context` (pure).

    The ngrok header is set on the CONTEXT (not per routed request) because redirect follow-ups
    (POST /book -> 303 -> /confirmation/…) bypass route handlers, and ngrok would show its
    interstitial on the confirmation page. That is still "the target only": it is set only in
    sandbox mode, where the guard aborts every request to another origin before it leaves; the one
    exception, a redirect follow-up off the target, is flagged by `on_request` and stops the run
    before submit. Live clinic runs never send it."""
    opts: dict[str, Any] = {"viewport": {"width": 1100, "height": 900}, "locale": "en-CA",
                            "timezone_id": tz or "America/Toronto",
                            "user_agent": "Mozilla/5.0 HealthAdvocateBookingBot/1.0"}
    extra = target_extra_headers(target_url, target_url, sandboxed)
    if extra:
        opts["extra_http_headers"] = extra
    return opts

#: What a form field may be mapped to. Data keys come from PatientInfo.shared_fields() (first/last name
#: are split from `name`); "consent" = a contact / privacy agreement checkbox; the last three are
#: blocked kinds that abort the attempt when required.
DATA_TARGETS = ("name", "first_name", "last_name", "date_of_birth", "sex", "phone", "email", "postal_code", "address",
                "street", "city", "province", "family_doctor_name", "family_doctor_clinic", "family_doctor_phone",
                "has_referral", "preferred_language", "reason_category", "preferred_slots", "report_url")
#: Address parts are typed only when the form REQUIRES them or there is no postal code (minimal disclosure).
ADDRESS_TARGETS = ("address", "street", "city", "province")
BLOCKED_TARGETS = ("password", "government_id", "payment")
MAP_TARGETS = DATA_TARGETS + ("consent", "unknown") + BLOCKED_TARGETS
BLOCKED_REASONS = {"password": "login required", "government_id": "health-card number required",
                   "payment": "payment required"}

CONSENT_RE = re.compile(r"consent|agree|privacy|terms|acknowledge|may contact|contact me", re.I)
NOT_CONSENT_RE = re.compile(r"\bfee|charge|\bpay|card|cancell?ation|marketing|newsletter|promot|offers|share .*third", re.I)
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("first_name", re.compile(r"first[\s_-]*name|given[\s_-]*name|\bfname\b|given-name", re.I)),
    ("last_name", re.compile(r"last[\s_-]*name|surname|family[\s_-]*name|\blname\b|family-name", re.I)),
    ("date_of_birth", re.compile(r"birth|\bdob\b|bday|birthday", re.I)),
    ("email", re.compile(r"e-?mail", re.I)),
    ("phone", re.compile(r"phone|\btel\b|mobile|\bcell", re.I)),
    ("postal_code", re.compile(r"postal|post[\s_-]*code|\bzip\b", re.I)),
    ("sex", re.compile(r"\bsex\b|gender", re.I)),
    ("preferred_language", re.compile(r"language", re.I)),
    ("province", re.compile(r"province|territor|\bstate\b|address-level1|\bregion\b", re.I)),
    ("city", re.compile(r"\bcity\b|\btown\b|municipality|address-level2", re.I)),
    ("street", re.compile(r"street|address[\s_-]*(line[\s_-]*)?1\b|address1|street-address|address-line1", re.I)),
    ("preferred_slots", re.compile(r"appointment[\s_-]*(time|date|slot)|preferred[\s_-]*(date|time|slot)|time[\s_-]*slot|"
                                   r"\bslot\b|availability|date and time", re.I)),
    ("reason_category", re.compile(r"reason|visit[\s_-]*type|type of (visit|appointment)|service|purpose|concern", re.I)),
    ("address", re.compile(r"address", re.I)),
    ("name", re.compile(r"(full|patient'?s?|your)[\s_-]*name|^\s*name\b|\bname\s*\*?$|\bname\b", re.I)),
    ("report_url", re.compile(r"message|notes?\b|comments?|additional|anything else|details|referral", re.I)),
]
_REF_RE = re.compile(r"(?:reference|confirmation|booking|appointment)\s*(?:number|no\.?|#|code|id)?\s*[:#]?\s*"
                     r"([A-Z0-9][A-Z0-9-]{3,}[A-Z0-9])", re.I)
_TIME_ATTR_RE = re.compile(r"<time[^>]*\bdatetime=\"([^\"]+)\"", re.I)
_PENDING_RE = re.compile(r"pending|request (has been |was )?received|we will (call|contact|e-?mail|be in touch)|"
                         r"will be in touch|not yet confirmed|awaiting confirmation|will confirm", re.I)
_CONFIRMED_RE = re.compile(r"\bconfirmed\b|is booked|appointment booked|booking (is )?complete|you'?re booked", re.I)
_ERROR_RE = re.compile(r"is required|invalid|failed|no longer available|try again|please (correct|choose|enter)|error", re.I)
_BOOK_LINK_RE = re.compile(r"book (online|now|an appointment|appointment)|request an? (appointment|visit)|"
                           r"online booking|\bbook\b|appointments?", re.I)
FAMILY_DOCTOR_RE = re.compile(r"family[\s_-]*(doctor|physician|md)|primary[\s_-]*care|\bgp\b|general practitioner|"
                              r"family_doctor|familydoctor", re.I)
HAS_REFERRAL_RE = re.compile(r"(do you|already) have (a |an )?referral|have you (been referred|got a referral)|"
                             r"referral (on file|received|from (your|a) (family )?doctor)\??$|\bhas[\s_-]*referral|"
                             r"\breferred\?", re.I)
NOT_PATIENT_RE = re.compile(r"doctor|physician|referr(ing|er|al source)|emergency contact|next of kin|insur|"
                            r"pharmac|guardian|caregiver|employer", re.I)
_STOP = {"and", "or", "the", "a", "an", "of", "for", "with", "check", "up", "check-up", "consultation", "visit"}


# ================================================================ field model

@dataclass
class WebField:
    index: int
    tag: str
    type: str
    name: str = ""
    id: str = ""
    label: str = ""
    placeholder: str = ""
    autocomplete: str = ""
    required: bool = False
    options: list[dict[str, str]] = field(default_factory=list)   # select / radio: [{value, text, group}]
    value: str = ""                                               # checkbox value attribute

    def text(self) -> str:
        return " ".join(x for x in (self.label, self.name, self.id, self.placeholder, self.autocomplete) if x)

    def display(self) -> str:
        return (self.label or self.placeholder or self.name or self.id or f"field {self.index}")[:80]

    def describe(self) -> dict[str, Any]:
        d = {"index": self.index, "tag": self.tag, "type": self.type, "name": self.name, "id": self.id,
             "label": self.label[:160], "placeholder": self.placeholder, "autocomplete": self.autocomplete,
             "required": self.required}
        if self.options:
            d["options"] = [o.get("text", "") for o in self.options[:8]] + (["…"] if len(self.options) > 8 else [])
        return d


@dataclass
class FillAction:
    field: WebField
    target: str
    value: str                    # text / option value / "check"

    def summary(self) -> str:
        shown = "checked" if self.target == "consent" else self.value
        return f"{self.field.display()}: {shown}"


@dataclass
class FillPlan:
    actions: list[FillAction] = field(default_factory=list)
    mapping: dict[int, str] = field(default_factory=dict)
    slot: Slot | None = None
    abort: str | None = None
    left_empty: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return "\n".join(a.summary() for a in self.actions)


def fields_from_dicts(raw: list[dict[str, Any]]) -> list[WebField]:
    """Browser-extracted element dicts -> WebFields; radio buttons of one name become one field."""
    out: list[WebField] = []
    radios: dict[str, WebField] = {}
    for d in raw:
        typ = (d.get("type") or "text").lower()
        if typ in ("hidden", "submit", "button", "reset", "image", "file"):
            continue
        if not d.get("visible", True) and not d.get("required"):
            continue  # honeypot / collapsed optional field
        if typ == "radio" and d.get("name"):
            group = radios.get(d["name"])
            if group is None:
                group = WebField(index=len(out), tag="input", type="radio", name=d["name"], id=d.get("id", ""),
                                 label=d.get("group_label") or d.get("label", ""), required=bool(d.get("required")))
                radios[d["name"]] = group
                out.append(group)
            group.options.append({"value": d.get("value", ""), "text": d.get("label", ""), "group": ""})
            group.required = group.required or bool(d.get("required"))
            continue
        out.append(WebField(index=len(out), tag=d.get("tag", "input"), type=typ, name=d.get("name", ""),
                            id=d.get("id", ""), label=d.get("label", ""), placeholder=d.get("placeholder", ""),
                            autocomplete=d.get("autocomplete", ""), required=bool(d.get("required")),
                            options=list(d.get("options") or []), value=d.get("value", "")))
    return out


# ================================================================ mapping (pure)

def blocked_kind(f: WebField) -> str | None:
    """password / government_id / payment by rules (cannot be overridden by the LLM)."""
    t = f.text()
    if f.type == "password" or re.search(r"password|passcode|\bpin\b", t, re.I):
        return "password"
    if HEALTH_CARD.search(t):
        return "government_id"
    if PAYMENT.search(t) or f.autocomplete.startswith("cc-"):
        return "payment"
    return None


def keyword_target(f: WebField) -> str:
    """Deterministic mapping by label / name / autocomplete keywords (FakeLlm and fallback)."""
    blocked = blocked_kind(f)
    if blocked:
        return blocked
    t = f.text()
    if f.type != "checkbox" and FAMILY_DOCTOR_RE.search(t):
        if f.type == "tel" or re.search(r"phone|\btel\b|fax|number", t, re.I):
            return "family_doctor_phone" if not re.search(r"fax", t, re.I) else "unknown"
        if f.type == "email" or re.search(r"e-?mail|address", t, re.I):
            return "unknown"  # we do not hold the doctor's e-mail / address
        if re.search(r"clinic|practice|office|centre|center|team", t, re.I):
            return "family_doctor_clinic"
        return "family_doctor_name"
    if f.type != "checkbox" and (f.tag == "select" or f.type == "radio") and HAS_REFERRAL_RE.search(t):
        return "has_referral"
    if f.type != "checkbox" and NOT_PATIENT_RE.search(t):
        return "unknown"  # someone else's details (doctor, emergency contact, insurer, pharmacy)
    if f.type == "checkbox":
        return "consent" if CONSENT_RE.search(t) and not NOT_CONSENT_RE.search(t) else "unknown"
    if f.type == "email":
        return "email"
    if f.type == "tel":
        return "phone"
    if f.type in ("datetime-local", "time"):
        return "preferred_slots"
    for target, rx in _RULES:
        if rx.search(t):
            if target == "name" and re.search(r"user\s*name|username|clinic name|doctor'?s? name|physician", t, re.I):
                continue
            if target == "report_url" and f.tag not in ("textarea",) and f.type not in ("text",):
                continue
            if target == "date_of_birth" or f.type != "date" or target == "preferred_slots":
                return target
    if f.type == "date":
        return "preferred_slots"
    if f.tag == "select" and _looks_like_slots(f.options):
        return "preferred_slots"
    return "unknown"


def _looks_like_slots(options: list[dict[str, str]]) -> bool:
    return sum(1 for o in options if _parse_option_start(o, None) is not None) >= 2


def llm_usable(llm: Any) -> bool:
    if llm is None or not hasattr(llm, "extract"):
        return False
    from app.providers.llm import FakeLlm

    return not isinstance(llm, FakeLlm) or bool(getattr(llm, "extract_queue", None))


FIELD_MAP_SCHEMA = {
    "type": "object",
    "properties": {"fields": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, "maps_to": {"type": "string", "enum": list(MAP_TARGETS)}},
        "required": ["index", "maps_to"]}}},
    "required": ["fields"],
}
FIELD_MAP_SYSTEM = (
    "You map the fields of a clinic's online appointment form to patient data keys. For each field "
    "return its index and maps_to, exactly one of: " + ", ".join(MAP_TARGETS) + ". "
    "name = full name; first_name / last_name = split name; sex = the PATIENT's sex / gender; street / city / "
    "province / postal_code / address (whole address in one field) = the PATIENT's home address; "
    "family_doctor_name / family_doctor_clinic / family_doctor_phone = the patient's family doctor / primary care "
    "provider; has_referral = a yes/no question whether the patient already has a referral; preferred_language "
    "= preferred language; reason_category = reason for visit / type of "
    "appointment; preferred_slots = appointment date/time choice; report_url = free-text message or notes "
    "(a referral report link goes there); consent = a checkbox agreeing to be contacted or to the privacy "
    "notice. password = any password / account field; government_id = health card, OHIP, SIN, passport, "
    "driver's licence or similar; payment = card / payment fields. Anything else (insurance, medical "
    "history, symptoms, medications, emergency contact, pharmacy, marketing opt-ins) is unknown. Never guess.")


def map_fields(fields: list[WebField], llm: Any = None) -> dict[int, str]:
    """{field.index: target}. LLM first when usable; rules fill gaps and override blocked kinds."""
    rules = {f.index: keyword_target(f) for f in fields}
    llm_map: dict[int, str] = {}
    if llm_usable(llm) and fields:
        import json

        try:
            out = llm.extract(FIELD_MAP_SYSTEM, json.dumps([f.describe() for f in fields]), FIELD_MAP_SCHEMA,
                              tool_name="map_form_fields", purpose="booking_web_fields")
            for item in (out or {}).get("fields") or []:
                idx, target = item.get("index"), item.get("maps_to")
                if isinstance(idx, int) and target in MAP_TARGETS:
                    llm_map[idx] = target
        except Exception as exc:  # LLM down / malformed -> rules only
            log.info("web field mapping via LLM failed", extra={"error": str(exc)[:200]})
    final: dict[int, str] = {}
    by_index = {f.index: f for f in fields}
    for idx, rule in rules.items():
        f = by_index[idx]
        if rule in BLOCKED_TARGETS:
            final[idx] = rule
            continue
        target = llm_map.get(idx, "unknown")
        if target == "unknown":
            target = rule  # an LLM "blocked" verdict is kept (more cautious than the rules)
        if target == "consent" and (f.type != "checkbox" or rule != "consent"):
            target = "unknown"  # only clear contact / privacy checkboxes are ever ticked
        if f.type == "checkbox" and target != "consent" and target not in BLOCKED_TARGETS:
            target = "unknown"
        if f.type == "email" and target not in ("email", *BLOCKED_TARGETS):
            target = "email"
        final[idx] = target
    return final


# ================================================================ slots + options (pure)

def _parse_option_start(opt: dict[str, str], tz: str | None) -> datetime | None:
    zone = zone_or_default(tz)
    value = (opt.get("value") or "").strip()
    if value:
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=zone)
        except ValueError:
            pass
    text = " ".join(x for x in (opt.get("group"), opt.get("text")) if x).strip()
    if not text or not re.search(r"\d{1,2}(:\d{2})?\s*(am|pm)|\d{1,2}:\d{2}", text, re.I) \
            or not re.search(r"\d{4}-\d{2}-\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", text, re.I):
        return None
    try:
        from dateutil import parser as dparser

        now = datetime.now(zone)
        dt = dparser.parse(text, fuzzy=True, default=now.replace(hour=0, minute=0, second=0, microsecond=0))
    except (ValueError, OverflowError, ImportError):
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=zone)
    if dt < datetime.now(zone) - timedelta(days=1):  # "Jan 5" seen in December means next year
        dt = dt.replace(year=dt.year + 1)
    return dt


def choose_slot(options: list[dict[str, str]], approved: list[Slot], tz: str | None,
                max_drift: timedelta = MAX_SLOT_DRIFT) -> tuple[str, Slot] | None:
    """The option closest to the approved slots: an exact start match (in approval order) wins, else
    the smallest |Δ| on the same local day within `max_drift`. None when nothing is close enough."""
    zone = zone_or_default(tz)
    cands = [(o.get("value") or o.get("text") or "", s) for o in options
             if (o.get("value") or "").strip() and (s := _parse_option_start(o, tz)) is not None]
    if not cands or not approved:
        return None
    for a in approved:
        for value, start in cands:
            if start == a.start:
                return value, Slot(start, a.end)
    best: tuple[timedelta, int, str, datetime] | None = None
    for i, a in enumerate(approved):
        for value, start in cands:
            if start.astimezone(zone).date() != a.start.astimezone(zone).date():
                continue
            delta = abs(start - a.start)
            if delta <= max_drift and (best is None or (delta, i) < (best[0], best[1])):
                best = (delta, i, value, start)
    if best is None:
        return None
    return best[2], Slot(best[3], best[3] + timedelta(minutes=SLOT_MINUTES))


def choose_reason_option(options: list[dict[str, str]], reason: str) -> str | None:
    """Option value best matching the generic reason category (word overlap), else a general one."""
    def words(s: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in _STOP and len(w) > 2}

    real = [o for o in options if (o.get("value") or "").strip()]
    if not real:
        return None
    for o in real:
        if o.get("text", "").strip().lower() == reason.strip().lower():
            return o["value"]
    target = words(reason)
    scored = sorted(((len(words(o.get("text", "")) & target), -i, o["value"]) for i, o in enumerate(real)), reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][2]
    general = next((o["value"] for o in real if re.search(r"general|check-?up|consult|\bother\b", o.get("text", ""), re.I)),
                   None)
    return general


SEX_CHOICES = {"male": [r"^m$", r"\bmale\b", r"\bman\b"], "female": [r"^f$", r"\bfemale\b", r"\bwoman\b"],
               "other": [r"\bother\b", r"non-?binary", r"^x$", r"another"],
               "prefer_not": [r"prefer not", r"decline", r"not to (say|answer|disclose)", r"rather not"]}
SEX_TEXT = {"male": "Male", "female": "Female", "other": "Other"}
YES_NO_CHOICES = {"yes": [r"^y(es)?\b", r"\btrue\b"], "no": [r"^no?\b", r"\bfalse\b"]}


def _province_choices() -> dict[str, list[str]]:
    from app.schemas.profile import PROVINCES

    return {code: [rf"^{code}$", rf"^{re.escape(name)}$", rf"\b{re.escape(name)}\b"] for code, name in PROVINCES.items()}


PROVINCE_CHOICES = _province_choices()


def _choice_value(f: WebField, key: Any, choices: dict[str, list[str]]) -> str | None:
    """Select / radio: the option matching `key` (value or text); text inputs get a plain value."""
    if not key:
        return None
    key = str(key)
    if f.tag == "select" or f.type == "radio":
        pats = choices.get(key, [rf"^{re.escape(key)}$"])
        real = [o for o in f.options if (o.get("value") or "").strip()]
        for o in real:
            for cand in ((o.get("value") or "").strip(), (o.get("text") or "").strip()):
                if any(re.search(p, cand, re.I) for p in pats):
                    return o["value"]
        return None
    if choices is SEX_CHOICES:
        return SEX_TEXT.get(key)  # "prefer_not" is never typed into a text box
    if choices is YES_NO_CHOICES:
        return key.capitalize()
    return key


def _value_for(target: str, f: WebField, shared: dict[str, Any], patient: PatientInfo, slots: list[Slot],
               tz: str) -> tuple[str | None, Slot | None]:
    zone = zone_or_default(tz)
    name = str(shared.get("name") or "").strip()
    if target == "first_name":
        return (shared.get("first_name") or (name.split()[0] if name else None)), None
    if target == "last_name":
        if shared.get("last_name"):
            return shared["last_name"], None
        parts = name.split()
        return (" ".join(parts[1:]) if len(parts) > 1 else (parts[0] if parts else None)), None
    if target in ADDRESS_TARGETS and shared.get("postal_code") and not f.required:
        return None, None  # the postal code is enough unless the form insists on the address
    if target == "sex":
        return _choice_value(f, shared.get("sex"), SEX_CHOICES), None
    if target == "province":
        return _choice_value(f, shared.get("province"), PROVINCE_CHOICES), None
    if target == "has_referral":
        v = shared.get("has_referral")
        return (None if v is None else _choice_value(f, "yes" if v else "no", YES_NO_CHOICES)), None
    if target == "preferred_language":
        lang = shared.get("preferred_language")
        return (_choice_value(f, lang, {lang: [re.escape(lang)]}) if lang else None), None
    if target == "reason_category":
        reason = str(shared.get("reason_category") or "")
        if f.tag == "select" or f.type == "radio":
            return (choose_reason_option(f.options, reason) if reason else None), None
        return reason or None, None
    if target == "preferred_slots":
        if f.tag == "select" or f.type == "radio":
            picked = choose_slot(f.options, slots, tz)
            return (picked[0], picked[1]) if picked else (None, None)
        if not slots:
            return None, None
        first = slots[0].start.astimezone(zone)
        if f.type == "date":
            return first.date().isoformat(), slots[0]
        if f.type == "datetime-local":
            return first.strftime("%Y-%m-%dT%H:%M"), slots[0]
        if f.type == "time":
            return first.strftime("%H:%M"), slots[0]
        labels = shared.get("preferred_slots") or []
        return ("; ".join(labels) or None), None
    if target == "report_url":
        url = shared.get("report_url")
        return (f"Referral report (secure link): {url}" if url else None), None
    if target == "consent":
        return "check", None
    if target in DATA_TARGETS:
        v = shared.get(target)
        return (str(v) if v else None), None
    return None, None


def build_fill_plan(fields: list[WebField], mapping: dict[int, str], patient: PatientInfo, slots: list[Slot],
                    tz: str) -> FillPlan:
    """What to type where — or why to abort (see module docstring, step 4). Pure."""
    plan = _plan_or_abort(fields, mapping, patient, slots, tz)
    if plan.abort:
        plan.actions, plan.slot = [], None
    return plan


def _plan_or_abort(fields: list[WebField], mapping: dict[int, str], patient: PatientInfo, slots: list[Slot],
                   tz: str) -> FillPlan:
    plan = FillPlan(mapping=dict(mapping))
    shared = patient.shared_fields()
    for f in fields:  # walls first: any password / payment field, required government ID
        target = mapping.get(f.index, "unknown")
        if target in ("password", "payment") or target == "government_id" and f.required:
            plan.abort = BLOCKED_REASONS[target]
            return plan
    for f in fields:
        target = mapping.get(f.index, "unknown")
        if target in BLOCKED_TARGETS or target == "unknown":
            if f.required:
                plan.abort = f"required field outside the allow-list: {f.display()}"
                return plan
            plan.left_empty.append(f.display())
            continue
        value, slot = _value_for(target, f, shared, patient, slots, tz)
        if value is None:
            if f.required:
                plan.abort = ("no open slot near the approved times" if target == "preferred_slots" else
                              f"required field has no shared value: {f.display()}")
                return plan
            plan.left_empty.append(f.display())
            continue
        if slot is not None and plan.slot is None:
            plan.slot = slot
        plan.actions.append(FillAction(f, target, value))
    return plan


# ================================================================ confirmation (pure)

CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["confirmed", "pending", "error", "unknown"]},
        "reference": {"type": ["string", "null"]},
        "appointment_start": {"type": ["string", "null"], "description": "ISO 8601 local date-time, if shown"},
        "message": {"type": ["string", "null"]},
    },
    "required": ["status", "reference", "appointment_start", "message"],
}
CONFIRM_SYSTEM = ("You read the page a clinic website showed after an appointment form was submitted. status: "
                  "confirmed (a specific appointment is booked), pending (request received, clinic will follow "
                  "up), error (the form was rejected or shows errors), unknown. reference: the booking reference "
                  "or confirmation number exactly as shown, else null. appointment_start: the booked date-time "
                  "as ISO 8601 if shown, else null. message: one short sentence quoting any error.")


def read_confirmation(text: str, html: str, tz: str, llm: Any = None, http_status: int = 200) -> dict[str, Any]:
    """{status, reference, start (aware datetime | None), message} from the result page."""
    det: dict[str, Any] = {"status": "unknown", "reference": None, "start": None, "message": None}
    for m in _REF_RE.finditer(text or ""):
        if re.search(r"[\d-]", m.group(1)):  # "QW-ABCDEF", "123456" — not a plain word
            det["reference"] = m.group(1)
            break
    tm = _TIME_ATTR_RE.search(html or "")
    if tm:
        det["start"] = _aware(tm.group(1), tz)
    if http_status >= 400:
        det["status"] = "error"
    elif _CONFIRMED_RE.search(text or "") and not _PENDING_RE.search(text or ""):
        det["status"] = "confirmed"
    elif _PENDING_RE.search(text or ""):
        det["status"] = "pending"
    elif _ERROR_RE.search(text or ""):
        det["status"] = "error"
    if det["status"] == "error":
        em = _ERROR_RE.search(text or "")
        if em:
            line = next((ln.strip() for ln in (text or "").splitlines() if em.group(0) in ln), em.group(0))
            det["message"] = line[:200]
    if llm_usable(llm):
        try:
            out = llm.extract(CONFIRM_SYSTEM, (text or "")[:4000], CONFIRM_SCHEMA, tool_name="read_confirmation",
                              purpose="booking_web_confirmation") or {}
            if out.get("status") in ("confirmed", "pending", "error") and http_status < 400:
                det["status"] = out["status"]
            ref = (out.get("reference") or "").strip()
            if ref and ref in (text or ""):  # never trust a reference the page does not show
                det["reference"] = ref
            if out.get("appointment_start") and det["start"] is None:
                det["start"] = _aware(out["appointment_start"], tz)
            if out.get("message") and det["status"] == "error":
                det["message"] = str(out["message"])[:200]
        except Exception as exc:
            log.info("web confirmation via LLM failed", extra={"error": str(exc)[:200]})
    return det


def _aware(value: str, tz: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=zone_or_default(tz))


# ================================================================ browser

_FORMS_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const labelOf = (el) => {
    const parts = [];
    if (el.id) {
      for (const l of document.querySelectorAll('label')) { if (l.htmlFor === el.id) parts.push(l.textContent); }
    }
    const wrap = el.closest('label');
    if (wrap) { const c = wrap.cloneNode(true); c.querySelectorAll('select,textarea,input,option').forEach(n => n.remove()); parts.push(c.textContent); }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\s+/).forEach(i => { const n = document.getElementById(i); if (n) parts.push(n.textContent); });
    if (el.getAttribute('aria-label')) parts.push(el.getAttribute('aria-label'));
    return clean(parts.join(' ')).slice(0, 200);
  };
  return Array.from(document.forms).map((f, fi) => {
    const fields = Array.from(f.elements).filter(el => ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)).map(el => {
      const type = (el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text') : el.tagName).toLowerCase();
      const fs = el.closest('fieldset'); const legend = fs && fs.querySelector('legend');
      return {
        tag: el.tagName.toLowerCase(), type, name: el.name || '', id: el.id || '', label: labelOf(el),
        group_label: legend ? clean(legend.textContent) : '',
        placeholder: el.getAttribute('placeholder') || '', autocomplete: el.getAttribute('autocomplete') || '',
        required: !!el.required || el.getAttribute('aria-required') === 'true',
        value: (type === 'checkbox' || type === 'radio') ? (el.value || '') : '',
        visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
        options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => ({value: o.value, text: clean(o.text),
          group: o.parentElement && o.parentElement.tagName === 'OPTGROUP' ? o.parentElement.label : ''})) : [],
      };
    });
    return {index: fi, action: f.action || '', method: (f.method || 'get').toLowerCase(), fields};
  });
}
"""


#: Headed runs: outline + tint the field about to be filled (inline style only; restored afterwards).
HIGHLIGHT_JS = r"""
(el, ms) => {
  const t = (el.type === 'checkbox' || el.type === 'radio') && el.closest('label') ? el.closest('label') : el;
  const prev = [t.style.outline, t.style.outlineOffset, t.style.backgroundColor, t.style.transition];
  t.style.transition = 'background-color 0.2s, outline-color 0.2s';
  t.style.outline = '3px solid #f59e0b';
  t.style.outlineOffset = '2px';
  t.style.backgroundColor = '#fff4cc';
  setTimeout(() => { [t.style.outline, t.style.outlineOffset, t.style.backgroundColor, t.style.transition] = prev; }, ms);
}
"""


class WebAbort(Exception):
    """Stop the attempt without submitting anything (→ skipped)."""


@lru_cache(maxsize=2)
def chromium_installed(headed: bool = False) -> bool:
    """A Playwright Chromium (or headless shell) build exists in the browsers path (cheap; no launch).
    headed=True: the full Chromium build (the headless shell cannot open a window)."""
    if importlib.util.find_spec("playwright") is None:
        return False
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env == "0":
        spec = importlib.util.find_spec("playwright")
        roots = [Path(spec.origin).parent / "driver" / "package" / ".local-browsers"] if spec and spec.origin else []
    elif env:
        roots = [Path(env)]
    elif sys.platform == "darwin":
        roots = [Path.home() / "Library" / "Caches" / "ms-playwright"]
    elif sys.platform.startswith("win"):
        roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"]
    else:
        roots = [Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"]
    prefixes = ("chromium-",) if headed else ("chromium_headless_shell-", "chromium-")
    return any(p.name.startswith(prefixes) for r in roots if r.is_dir() for p in r.iterdir())


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.scheme, pa.netloc.lower()) == (pb.scheme, pb.netloc.lower())


def start_url_for(target_url: str, detection: dict[str, Any] | None) -> str:
    """detection.form_url when it lies under the target (same origin + path prefix), else the target."""
    form_url = (detection or {}).get("form_url")
    if form_url and _same_origin(form_url, target_url):
        base = urlsplit(target_url).path.rstrip("/")
        if urlsplit(form_url).path.startswith(base):
            return form_url
    return target_url


@lru_cache(maxsize=256)
def _host_is_public(host: str) -> bool:
    """True when every address of `host` is public (cached per process; unresolvable = not public)."""
    from app.services.booking.channels.detect import UnsafeURL, check_public_url

    try:
        check_public_url(f"http://{host}/")
        return True
    except UnsafeURL:
        return False


def request_allowed(url: str, target_url: str, sandboxed: bool, is_document: bool) -> bool:
    """May the browser load `url`? The target's own origin (even a private sandbox host such as the
    local mock clinic) is allowed; any other host never in sandbox, never as a page (document) in
    live mode, and as a sub-resource only when its host is public."""
    if url.startswith(("data:", "blob:", "about:")):
        return True
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if _same_origin(url, target_url):
        return True
    if sandboxed or is_document:
        return False
    return _host_is_public(parts.hostname)


@dataclass
class _Run:
    """Mutable state of one browser run (shared with the watchdog in `execute`). `lock` guards
    `submitted` / `cancelled` so a timed-out run can never submit after `execute` returned."""
    cancelled: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    submitted: bool = False
    blocked: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    screenshots: dict[str, str] = field(default_factory=dict)
    result: ChannelResult | None = None
    opts: BrowserOptions = field(default_factory=BrowserOptions)
    t0: float = field(default_factory=time.monotonic)
    deadline: float = 0.0          # monotonic; the watchdog gives up after it (may grow, see extend)

    def remaining(self) -> float:
        with self.lock:
            return self.deadline - time.monotonic()

    def extend(self, timeout_s: float) -> None:
        """Move the deadline to t0 + timeout_s when that is later (never shortens it)."""
        with self.lock:
            if not self.cancelled.is_set():
                self.deadline = max(self.deadline, self.t0 + timeout_s)


def make_guard(origin: str, sandboxed: bool, run: "_Run") -> Any:
    """The Playwright route handler for one run: enforces `request_allowed` (also per redirect hop)."""

    def guard(route: Any) -> None:
        """Every request the browser starts: other hosts / private IPs are aborted (sandbox:
        nothing but the target; live: no off-site pages). Pages are fetched with redirects
        OFF so each redirect hop is checked here too (route handlers do not see redirects)."""
        req = route.request
        is_doc = req.resource_type == "document"
        if not request_allowed(req.url, origin, sandboxed, is_doc):
            if is_doc:
                run.blocked.append(req.url)
            route.abort()
            return
        if not is_doc:
            route.continue_()
            return
        resp = route.fetch(max_redirects=0)
        location = resp.headers.get("location")
        if 300 <= resp.status < 400 and location:
            nxt = urljoin(req.url, location)
            if not request_allowed(nxt, origin, sandboxed, True):
                run.blocked.append(nxt)
                route.abort()
                return
        route.fulfill(response=resp)

    return guard


class WebChannel:
    name = "web"

    def __init__(self, storage: Any = None, hard_timeout_s: float = HARD_TIMEOUT_S, headless: bool | None = None,
                 slow_mo_ms: int | None = None) -> None:
        """`headless` / `slow_mo_ms` None = WEB_BOOKING_HEADED / WEB_BOOKING_SLOW_MO_MS at run time."""
        self._storage = storage
        self.hard_timeout_s = hard_timeout_s
        self._headless = headless
        self._slow_mo_ms = slow_mo_ms

    def browser_options(self) -> BrowserOptions:
        return BrowserOptions.from_settings(None if self._headless is None else not self._headless, self._slow_mo_ms)

    def configured(self) -> bool:
        return bool(settings.web_booking_enabled) and chromium_installed()

    def available(self, clinic: Clinic, ctx: BookingContext) -> str | None:
        det = ctx.detection or {}
        if det.get("web") == "web_blocked":
            return det.get("reason") or "website blocked"
        if det and det.get("web") != "web_form":
            return det.get("reason") or "no booking form found"
        if not self.configured():
            return "not configured"
        if not det:
            return "website not checked"
        if sandbox.resolve_target("web", clinic) is None:
            return sandbox.missing_reason("web", clinic)
        return None

    # ------------------------------------------------------------ execute

    def execute(self, appt: Any, ctx: BookingContext) -> ChannelResult:
        if not self.configured():
            return ChannelResult.skipped("not configured")
        target = sandbox.resolve_target("web", ctx.clinic)
        if target is None:
            return ChannelResult.skipped(sandbox.missing_reason("web", ctx.clinic))
        start = start_url_for(target.value, ctx.detection)
        opts = self.browser_options()
        run = _Run(opts=opts)
        t0 = run.t0
        run.deadline = t0 + hard_timeout_for(self.hard_timeout_s, ESTIMATED_FIELDS, opts)

        def work() -> None:
            try:
                run.result = self._drive(appt, ctx, target, start, run)
            except WebAbort as exc:
                run.result = ChannelResult.skipped(str(exc))
            except Exception as exc:  # browser crash, navigation error, …
                log.warning("web booking failed", extra={"error": f"{type(exc).__name__}: {exc}"[:300]})
                msg = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
                if run.blocked and not run.submitted:
                    msg = f"blocked a request outside the clinic website ({urlsplit(run.blocked[0]).hostname})"
                    run.result = ChannelResult.skipped(msg)
                    return
                run.result = (ChannelResult.failed(f"website error after submit: {msg}") if run.submitted else
                              ChannelResult.skipped(f"website automation error: {msg}"))

        th = threading.Thread(target=work, name="web-booking", daemon=True)
        th.start()
        while th.is_alive():
            left = run.remaining()  # the worker may extend the deadline once it knows the form (slow-mo)
            if left <= 0:
                break
            th.join(min(left, 0.25))
        if th.is_alive():
            with run.lock:  # after this the worker can no longer start the submit
                run.cancelled.set()
                submitted = run.submitted
                limit = run.deadline - t0
            res = (ChannelResult.failed("website booking timed out after submit") if submitted else
                   ChannelResult.skipped(f"website booking timed out ({max(1, round(limit))} s)"))
        else:
            res = run.result or ChannelResult.failed("website booking produced no result")
        res.target = target.as_dict()
        res.details = {**res.details, "pages": run.pages, "screenshots": run.screenshots,
                       "duration_s": round(time.monotonic() - t0, 2), "start_url": start}
        return res

    def _storage_obj(self) -> Any:
        if self._storage is None:
            from app.providers.storage import get_storage

            self._storage = get_storage()
        return self._storage

    def _shot(self, page: Any, ctx: BookingContext, appt: Any, label: str, run: _Run, timeout_ms: int = ACTION_TIMEOUT_MS) -> None:
        try:
            data = page.screenshot(full_page=True, type="png", timeout=timeout_ms)
            user_id = str(getattr(ctx.user, "id", None) or getattr(appt, "user_id", "anonymous"))
            run.screenshots[label] = self._storage_obj().save(user_id, SCREENSHOT_KIND,
                                                              f"{getattr(appt, 'id', 'appt')}_{label}.png", data)
        except Exception as exc:
            log.info("web booking screenshot failed", extra={"error": str(exc)[:200]})

    def _check_walls(self, page: Any, run: _Run) -> Any:
        if run.blocked:
            raise WebAbort(f"blocked a request outside the clinic website ({urlsplit(run.blocked[0]).hostname})")
        html = page.content()
        if page.url not in run.pages:
            run.pages.append(page.url)
        analysis = analyze_html(html, page.url)
        if analysis.kind == "web_blocked":
            raise WebAbort(analysis.reason or "website blocked")
        return analysis

    def _drive(self, appt: Any, ctx: BookingContext, target: Any, start: str, run: _Run) -> ChannelResult:
        from playwright.sync_api import sync_playwright

        opts = run.opts
        sandboxed = target.mode != "live"
        origin = start

        guard = make_guard(origin, sandboxed, run)

        def on_request(req: Any) -> None:
            """Belt and braces: a redirected request (any type) that leaves the allowed hosts."""
            if req.redirected_from is not None and not request_allowed(
                    req.url, origin, sandboxed, req.resource_type == "document"):
                run.blocked.append(req.url)

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(**launch_options(opts))
            except Exception as exc:
                raise WebAbort(f"browser unavailable: {str(exc).splitlines()[0][:150]}") from exc
            try:
                context = browser.new_context(**context_options(origin, sandboxed, ctx.clinic_tz))
                context.route("**/*", guard)
                context.on("request", on_request)
                page = context.new_page()
                page.set_default_timeout(opts.action_timeout_ms())
                page.set_default_navigation_timeout(opts.nav_timeout_ms())
                page.goto(start, wait_until="domcontentloaded")
                form_index, fields = self._find_form(page, run)
                run.extend(hard_timeout_for(self.hard_timeout_s, len(fields) + 4, opts))  # +4: walls, shots, submit
                mapping = map_fields(fields, ctx.llm)
                plan = build_fill_plan(fields, mapping, ctx.patient, ctx.slots, ctx.clinic_tz)
                if plan.abort:
                    raise WebAbort(plan.abort)
                form = page.locator("form").nth(form_index)
                for action in plan.actions:
                    if run.cancelled.is_set():
                        raise WebAbort("website booking timed out")
                    self._fill(form, action, highlight=opts.headed)
                self._check_walls(page, run)  # a widget injected while filling still stops us
                self._shot(page, ctx, appt, "before_submit", run, opts.action_timeout_ms())
                form_url = page.url
                self._begin_submit(run)
                status = self._submit(page, form, opts.nav_timeout_ms())
                text = page.inner_text("body")
                html = page.content()
                run.pages.append(page.url)
                self._shot(page, ctx, appt, "result", run, opts.action_timeout_ms())
                if analyze_html(html, page.url).kind == "web_blocked" and status < 400:
                    # e.g. a CAPTCHA challenge shown only after submit: treat as not sent
                    return ChannelResult.failed("website asked for a login / CAPTCHA after submit",
                                                details={"result_url": page.url})
                conf = read_confirmation(text, html, ctx.clinic_tz, ctx.llm, status)
                result = self._result(ctx, target, form_url, plan, conf, page.url)
                if opts.headed:
                    self._linger(page, run)
                return result
            finally:
                browser.close()

    @staticmethod
    def _linger(page: Any, run: _Run) -> None:
        """Headed: keep the result page on screen for viewers (only while the watchdog allows it)."""
        hold = min(HEADED_HOLD_S, run.remaining() - 1.0)
        if hold > 0 and not run.cancelled.is_set():
            try:
                page.wait_for_timeout(int(hold * 1000))
            except Exception:  # the viewer closed the window: the result already stands
                pass

    @staticmethod
    def _begin_submit(run: _Run) -> None:
        """Mark the run as submitted — under the lock, and never once the watchdog in `execute`
        gave up (cancelled) or a request left the allowed hosts."""
        with run.lock:
            if run.cancelled.is_set():
                raise WebAbort("website booking timed out")
            if run.blocked:
                raise WebAbort(f"blocked a request outside the clinic website ({urlsplit(run.blocked[0]).hostname})")
            run.submitted = True

    def _find_form(self, page: Any, run: _Run) -> tuple[int, list[WebField]]:
        """The booking form on this page, following "Book online" links (≤ MAX_HOPS)."""
        for hop in range(MAX_HOPS + 1):
            analysis = self._check_walls(page, run)
            forms = page.evaluate(_FORMS_JS)
            best: tuple[int, int, list[WebField]] | None = None
            for f in forms:
                fields = fields_from_dicts(f["fields"])
                score = sum(1 for x in fields if keyword_target(x) in DATA_TARGETS)
                if score >= 2 and (best is None or score > best[0]):
                    best = (score, f["index"], fields)
            if best is not None:
                return best[1], best[2]
            if hop == MAX_HOPS:
                break
            link = self._booking_link(page, analysis)
            if not link:
                break
            page.goto(link, wait_until="domcontentloaded")
        raise WebAbort("no booking form found")

    @staticmethod
    def _booking_link(page: Any, analysis: Any) -> str | None:
        anchors = page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => "
                                "[a.getAttribute('href'), (a.textContent || '').trim()])")
        cands = []
        for href, label in anchors:
            url = urljoin(page.url, (href or "").split("#")[0])
            if not url.startswith(("http://", "https://")) or not _same_origin(url, page.url) or url == page.url:
                continue
            if _BOOK_LINK_RE.search(label or ""):
                strong = bool(re.search(r"book online|book now|book an appointment|online booking", label, re.I))
                cands.append((0 if strong else 1, url))
        cands.sort(key=lambda c: c[0])
        return cands[0][1] if cands else (analysis.links[0] if analysis.links else None)

    @staticmethod
    def _locator(form: Any, f: WebField, option_value: str | None = None) -> Any:
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        if f.type == "radio" and option_value is not None:
            return form.locator(f'input[type="radio"][name="{esc(f.name)}"][value="{esc(option_value)}"]').first
        if f.name:
            return form.locator(f'[name="{esc(f.name)}"]').first
        return form.locator(f'[id="{esc(f.id)}"]').first

    def _fill(self, form: Any, a: FillAction, highlight: bool = False) -> None:
        f = a.field
        if highlight:
            self._highlight(self._locator(form, f, a.value if f.type == "radio" else None))
        if a.target == "consent":
            self._locator(form, f).check()
        elif f.tag == "select":
            self._locator(form, f).select_option(value=a.value)
        elif f.type == "radio":
            self._locator(form, f, a.value).check()
        else:
            self._locator(form, f).fill(a.value)

    @staticmethod
    def _highlight(loc: Any) -> None:
        """Headed runs: outline the field about to be filled (style only, restored after HIGHLIGHT_MS)."""
        try:
            loc.scroll_into_view_if_needed()
            loc.evaluate(HIGHLIGHT_JS, HIGHLIGHT_MS)
        except Exception:  # cosmetic only, never blocks the booking
            pass

    @staticmethod
    def _submit(page: Any, form: Any, nav_timeout_ms: int = NAV_TIMEOUT_MS) -> int:
        btn = form.locator('button[type="submit"], input[type="submit"], button:not([type])').first
        if btn.count() == 0:
            raise WebAbort("booking form has no submit button")
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=nav_timeout_ms) as nav:
                btn.click()
            resp = nav.value
        except Exception as exc:
            if "Timeout" in type(exc).__name__ or "timeout" in str(exc).lower():
                raise RuntimeError("the form did not submit (client-side validation or script error)") from exc
            raise
        return resp.status if resp is not None else 200

    @staticmethod
    def _result(ctx: BookingContext, target: Any, form_url: str, plan: FillPlan, conf: dict[str, Any],
                result_url: str) -> ChannelResult:
        ref = conf.get("reference")
        msg = ChannelMessage(direction="out", channel="web", recipient=form_url,
                             subject=f"Online booking form: {ctx.clinic.name}", body=plan.summary(),
                             external_id=f"web:{ref or uuid.uuid4().hex}")
        start = conf.get("start") or (plan.slot.start if plan.slot else None)
        slot = {"start": start.isoformat(), "end": (start + timedelta(minutes=SLOT_MINUTES)).isoformat()} if start else None
        request = {"channel": "web", "to": form_url, "real_to": target.real, "redirected": target.redirected,
                   "form_url": form_url, "result_url": result_url, "reference": ref,
                   "fields": [a.target for a in plan.actions], "left_empty": plan.left_empty,
                   "chosen_slot": slot, "sent_at": datetime.now().astimezone().isoformat()}
        details = {"request": request, "confirmation": {k: (v.isoformat() if isinstance(v, datetime) else v)
                                                        for k, v in conf.items()}}
        status = conf.get("status")
        if status == "error":
            return ChannelResult.failed(f"form submission rejected: {conf.get('message') or 'error page'}",
                                        messages=[msg], details=details)
        if status == "confirmed" and slot:
            return ChannelResult(status="confirmed", reason=target.note or "booked via the clinic's website",
                                 external_ref=ref, messages=[msg], slot=slot, details=details)
        return ChannelResult(status="requested", reason=target.note or "request submitted via the clinic's website",
                             external_ref=ref, messages=[msg], details=details)


__all__ = ["WebChannel", "WebField", "FillPlan", "BrowserOptions", "launch_options", "hard_timeout_for",
           "target_extra_headers", "context_options", "chromium_args", "map_fields", "keyword_target", "build_fill_plan", "choose_slot",
           "choose_reason_option", "read_confirmation", "fields_from_dicts", "start_url_for", "chromium_installed"]
