"""The e-mails the booking agent sends to the USER (clinic e-mails live in booking/common.py).

    content = approval_email(user, appt)            # "Please approve" — Approve / Not now buttons
    content = requested_email(user, appt)           # "Request sent" — what happens next
    content = confirmed_email(user, appt, calendar) # "Confirmed" — add-to-calendar + .ics attached
    notify(db, user.id, KIND, title, body, email=True, email_content=content)

Every button that changes something is a signed link (booking/common.py `user_action_url`): the
page it opens shows what will happen and only its POST acts, so mail scanners that prefetch links
never approve or decline anything. The body is built from structured data only — never from the
planner's free text (spec §0 minimal disclosure also applies to what we tell the user about the clinic).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.integrations import Slot
from app.providers.email import Attachment, email_shell
from app.services.booking.common import DEFAULT_TZ, _env as _jinja, app_url, ics_url, slot_label, user_action_url
from app.services.notify import EmailContent

SPECIALIST = {
    "cardiology": "a cardiologist", "neurology": "a neurologist", "memory_clinic": "a memory clinic",
    "geriatrics": "a geriatrician", "family_medicine": "a family doctor", "walk_in": "a walk-in clinic",
    "hospital": "a hospital clinic",
}
SPECIALTY_LABEL = {
    "cardiology": "Cardiology", "neurology": "Neurology", "memory_clinic": "Memory clinic", "geriatrics": "Geriatrics",
    "family_medicine": "Family medicine", "walk_in": "Walk-in clinic", "hospital": "Hospital",
}
CHANNEL_TEXT = {"web": "its online booking form", "email": "e-mail", "phone": "phone (an AI assistant calls the front desk)"}
SHARED_LABELS = [("name", "Name"), ("date_of_birth", "Date of birth"), ("phone", "Phone"), ("email", "E-mail"),
                 ("postal_code", "Postal code"), ("reason_category", "Reason for the visit")]
NEVER_SHARED = "Never shared: your measurements, diagnoses, medications or health-card number."
PROFILE_FIELD_NAMES = {"first_name": "legal first name", "last_name": "legal last name", "date_of_birth": "date of birth",
                       "sex": "sex (as on your health card)", "phone": "phone number", "email": "e-mail",
                       "street": "street address", "city": "city", "province": "province", "postal_code": "postal code"}
BRING = ["Your health card", "A list of your current medications", "Any questions you want to ask"]


def _tz(user: Any) -> str:
    p = getattr(user, "profile", None)
    return (getattr(p, "timezone", None) if p else None) or DEFAULT_TZ


def _first_name(user: Any) -> str | None:
    p = getattr(user, "profile", None)
    name = (getattr(p, "first_name", None) or getattr(p, "display_name", None)) if p else None
    return str(name).split(" ")[0] if name else None


def _greeting(user: Any) -> str:
    name = _first_name(user)
    return f"Hi {name}," if name else "Hello,"


def long_slot(slot: Slot, tz: str) -> tuple[str, str]:
    """('Tuesday, October 21, 2026', '10:00–10:30 AM EDT')."""
    zone = ZoneInfo(tz or DEFAULT_TZ)
    s, e = slot.start.astimezone(zone), slot.end.astimezone(zone)
    hm = lambda d: f"{d:%I:%M %p}".lstrip("0")  # noqa: E731
    return f"{s:%A, %B} {s.day}, {s:%Y}", f"{hm(s)}–{hm(e)} {s.tzname()}"


def _clinic(appt: Any) -> dict[str, Any]:
    return dict((appt.approval or {}).get("clinic") or appt.clinic or {})


def _distance(c: dict[str, Any]) -> str | None:
    d = c.get("distance_km")
    return f"{d:.1f} km from you" if isinstance(d, (int, float)) and d < 500 else None


def _maps_url(c: dict[str, Any]) -> str | None:
    q = ", ".join(x for x in (c.get("name"), c.get("address")) if x)
    return f"https://www.google.com/maps/search/?api=1&query={quote(q)}" if q else None


def _clinic_rows(c: dict[str, Any], *, with_contact: bool = True) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if c.get("specialty"):
        rows.append(("Specialty", SPECIALTY_LABEL.get(c["specialty"], str(c["specialty"]).replace("_", " ").title())))
    if c.get("address"):
        rows.append(("Address", c["address"] + (f" · {_distance(c)}" if _distance(c) else "")))
    if with_contact and c.get("phone"):
        rows.append(("Phone", c["phone"]))
    if with_contact and c.get("website"):
        rows.append(("Website", c["website"]))
    if c.get("rating"):
        rows.append(("Google rating", f"{c['rating']} / 5"))
    return rows


def _plan_sentence(plan: list[dict[str, Any]]) -> str:
    usable = [p["channel"] for p in plan or [] if p.get("available")]
    if not usable:
        return "No contact channel is available for this clinic."
    parts = [CHANNEL_TEXT.get(c, c) for c in usable]
    if len(parts) == 1:
        return f"By {parts[0]}."
    return f"First by {parts[0]}; if that does not work, by " + ", then by ".join(parts[1:]) + "."


def _sandbox_banner(appt: Any) -> dict[str, str] | None:
    if (appt.approval or {}).get("target_mode", settings.booking_target_mode) != "sandbox":
        return None
    return {"bg": "#fdf1dc", "ink": "#8a4b00",
            "text": "Demo mode: the request goes to a test inbox / test number controlled by the team, "
                    "never to the real clinic."}


def _render(user: Any, appt: Any, *, subject: str, title: str, eyebrow: str | None, lead: str | None,
            sections: list[dict[str, Any]], buttons: list[tuple[str, str, bool]] | None, closing: str | None = None,
            hero: dict[str, str] | None = None, banner: dict[str, str] | None = None, footer_note: str | None = None,
            preheader: str | None = None, attachments: list[Attachment] | None = None) -> EmailContent:
    body = _jinja().get_template("user_email.html.j2").render(lead=lead, sections=sections, closing=closing, hero=hero,
                                                              banner=banner)
    html = email_shell(title, body, eyebrow=eyebrow, buttons=buttons, footer_note=footer_note, preheader=preheader)
    return EmailContent(subject, html, _text(title, lead, hero, sections, buttons, closing, banner, footer_note),
                        attachments)


def _text(title, lead, hero, sections, buttons, closing, banner, footer_note) -> str:
    lines = [title, "=" * min(len(title), 60), ""]
    if banner:
        lines += [f"[{banner['text']}]", ""]
    if lead:
        lines += [lead, ""]
    if hero:
        lines += [f"{hero['label']}: {hero['value']}" + (f" ({hero['sub']})" if hero.get("sub") else ""), ""]
    for sec in sections:
        lines.append(sec["title"])
        if sec.get("text"):
            lines.append(sec["text"])
        for label, value in sec.get("rows") or []:
            lines.append(f"  {label}: {value}")
        for item in sec.get("items") or []:
            lines.append(f"  - {item}")
        for url, label in sec.get("links") or []:
            lines.append(f"  {label}: {url}")
        if sec.get("note"):
            lines.append(f"  ({sec['note']})")
        lines.append("")
    for url, label, _ in buttons or []:
        lines.append(f"{label}: {url}")
    if buttons:
        lines.append("")
    if closing:
        lines += [closing, ""]
    if footer_note:
        lines += [footer_note, ""]
    from app.providers.email import DISCLAIMER_EN

    lines.append(DISCLAIMER_EN)
    return "\n".join(lines)


# ================================================================ A. approval needed

def approval_email(user: Any, appt: Any) -> EmailContent:
    """'Please approve': the finding, the clinic, the 3 times, how it will be contacted, what it
    receives, and Approve / Not now buttons that work from the inbox (7-day links)."""
    tz = _tz(user)
    approval = appt.approval or {}
    clinic = _clinic(appt)
    finding = approval.get("finding") or {}
    reapproval = bool(approval.get("reapproval_reason"))
    specialist = SPECIALIST.get(clinic.get("specialty") or "", "a clinic")
    name = clinic.get("name") or "a clinic near you"

    if reapproval:
        subject = f"Please approve new times: {name}"
        title = f"New times for {name}"
        lead = (f"{_greeting(user)} the times you approved for {name} are no longer available. "
                "We prepared three new ones. Nothing is sent until you approve.")
    else:
        subject = f"Please approve: appointment request with {name}"
        title = finding.get("headline") or "A check-up is worth arranging"
        area = finding.get("pathway_name")
        lead = (f"{_greeting(user)} based on your recent {area.lower() if area else 'health'} data, "
                f"{settings.product_name} suggests seeing {specialist}. We found a clinic near you and prepared "
                "an appointment request. Nothing is sent until you approve.")

    sections: list[dict[str, Any]] = []
    if finding.get("points") and not reapproval:
        sections.append({"title": "What we noticed", "items": finding["points"],
                         "links": [(app_url(None, "/"), "See the full explanation")],
                         "note": "The priority score reflects which follow-up rules fired, not the chance that "
                                 "you have a condition."})
    sections.append({"title": "The clinic", "rows": [("Clinic", name)] + _clinic_rows(clinic)})
    sections.append({"title": "Proposed times (your time zone)",
                     "items": [s.get("label_user") or s.get("label") for s in approval.get("slots") or []],
                     "note": "The clinic picks one of these. If none works for them, they can suggest another "
                             "time and we will ask you first."})
    sections.append({"title": "How we will contact them", "text": _plan_sentence(approval.get("channel_plan") or [])})
    shared = approval.get("shared_fields") or {}
    rows = [(label, str(shared[k])) for k, label in SHARED_LABELS if shared.get(k)]
    if shared.get("report_url"):
        rows.append(("Report link", "a secure link to your referral report"))
    sections.append({"title": "What the clinic will receive", "rows": rows, "note": NEVER_SHARED})
    missing = approval.get("missing_profile_fields") or []
    if missing:
        sections.append({"title": "Your profile is missing something",
                         "text": "The clinic may ask for: " + ", ".join(PROFILE_FIELD_NAMES.get(m, m) for m in missing)
                                 + ". We will say you can provide it; adding it to your profile avoids the back-and-forth.",
                         "links": [(app_url(None, "/"), "Complete your profile")]})
    backups = [c.get("summary", {}).get("name") or c.get("clinic", {}).get("name")
               for c in (approval.get("candidates") or []) if c.get("rank", 1) > 1]
    backups = [b for b in backups if b]
    if backups:
        sections.append({"title": "If this clinic cannot book you",
                         "text": "We will try " + " and then ".join(backups) + " with the same details and the "
                                 "times shown in the app, without asking you again."})
    if approval.get("referral_note"):
        sections.append({"title": "About referrals", "text": approval["referral_note"]})

    buttons = [(user_action_url(appt, "approve"), "Approve request", True),
               (user_action_url(appt, "decline"), "Not now", False)]
    days = settings.user_action_link_days
    footer = (f"These buttons work for {days} days without signing in; after that, or any time, you can decide in "
              f"{settings.product_name} → Appointments.")
    return _render(user, appt, subject=subject, title=title, eyebrow="Your approval is needed", lead=lead,
                   sections=sections + [{"title": "Prefer to decide in the app?",
                                         "links": [(app_url(appt), f"Review in {settings.product_name}")]}],
                   buttons=buttons, banner=_sandbox_banner(appt), footer_note=footer,
                   preheader=f"We found {name}. Nothing is sent until you approve.")


# ================================================================ requested

def requested_email(user: Any, appt: Any, channel: str) -> EmailContent:
    tz = _tz(user)
    clinic = _clinic(appt)
    name = clinic.get("name") or "the clinic"
    slots = [slot_label(Slot.from_any(s), tz) for s in appt.proposed_slots or []]
    via = {"web": "through its online booking form", "email": "by e-mail", "phone": "by phone"}.get(channel, channel)
    sections = [
        {"title": "Times we proposed (your time zone)", "items": slots},
        {"title": "What happens next", "items": [
            f"The clinic picks a time or suggests another one. We e-mail you as soon as it replies.",
            "If the clinic asks a question we cannot answer, we ask you first.",
            "No reply after 3 days: we follow up. After 10 days we tell you and suggest the next clinic.",
        ]},
        {"title": "Changed your mind?", "links": [(app_url(appt), "Cancel the request in " + settings.product_name)]},
    ]
    return _render(user, appt, subject=f"Request sent to {name}", title=f"We asked {name} for an appointment",
                   eyebrow="Request sent", lead=f"{_greeting(user)} your request went to {name} {via}.",
                   sections=sections, buttons=None, banner=_sandbox_banner(appt),
                   preheader=f"Sent {via}. We will tell you when the clinic replies.")


# ================================================================ B. confirmed + calendar

def appointment_ics(appt: Any, *, tz: str | None = None) -> bytes:
    """RFC 5545 invitation for the confirmed appointment (UTC times, 1-hour reminder)."""
    slot = Slot.from_any(appt.chosen_slot)
    clinic = _clinic(appt)
    stamp = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    def esc(value: Any) -> str:
        return (str(value).replace(chr(92), chr(92) * 2).replace(chr(10), chr(92) + 'n').replace(',', chr(92) + ',')
                .replace(';', chr(92) + ';'))
    where = ", ".join(x for x in (clinic.get("name"), clinic.get("address")) if x)
    desc = f"Booked via {settings.product_name}. Bring: " + "; ".join(b.lower() for b in BRING) + "."
    if clinic.get("phone"):
        desc += f" Clinic phone: {clinic['phone']}."
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{settings.product_name}//Appointments//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:{appt.id}@{settings.product_name.lower()}",
        f"DTSTAMP:{stamp(datetime.now(timezone.utc))}", f"DTSTART:{stamp(slot.start)}", f"DTEND:{stamp(slot.end)}",
        f"SUMMARY:{esc('Appointment: ' + (clinic.get('name') or 'clinic'))}",
    ]
    if where:
        lines.append(f"LOCATION:{esc(where)}")
    lines += [f"DESCRIPTION:{esc(desc)}", "STATUS:CONFIRMED", "BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY",
              f"DESCRIPTION:{esc('Appointment at ' + (clinic.get('name') or 'the clinic') + ' in 1 hour')}",
              "END:VALARM", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines).encode("utf-8") + b"\r\n"


def _fold(line: str) -> str:
    """RFC 5545 §3.1: lines longer than 75 octets are folded with CRLF + space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, chunk = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(chunk) + len(b) > (75 if not out else 74):
            out.append(chunk.decode("utf-8"))
            chunk = b
        else:
            chunk += b
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def google_calendar_url(appt: Any) -> str:
    """A Google Calendar 'new event' link pre-filled with the appointment (no OAuth: the user saves it)."""
    slot = Slot.from_any(appt.chosen_slot)
    clinic = _clinic(appt)
    stamp = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
    params = {"action": "TEMPLATE", "text": f"Appointment: {clinic.get('name') or 'clinic'}",
              "dates": f"{stamp(slot.start)}/{stamp(slot.end)}",
              "details": f"Booked via {settings.product_name}. Bring your health card and a list of your medications.",
              "location": ", ".join(x for x in (clinic.get("name"), clinic.get("address")) if x)}
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def calendar_options(appt: Any, *, google_available: bool) -> dict[str, Any]:
    """What the confirmation e-mail / app can offer. ``google_available`` = Google Calendar connected
    AND calendar_access consent (then the primary button writes the event through the API)."""
    if not appt.chosen_slot:
        return {"mode": "none"}
    return {"mode": "google" if google_available else "manual", "add_url": user_action_url(appt, "calendar"),
            "google_url": google_calendar_url(appt), "ics_url": ics_url(appt)}


def confirmed_email(user: Any, appt: Any, calendar: dict[str, Any]) -> EmailContent:
    """'Confirmed': the time, the place, what to bring and the add-to-calendar step. The .ics is attached
    whenever there is a time. ``calendar`` = `approval["calendar"]` | `calendar_options(...)`."""
    tz = _tz(user)
    clinic = _clinic(appt)
    name = clinic.get("name") or "The clinic"
    slot = Slot.from_any(appt.chosen_slot) if appt.chosen_slot else None
    day, hours = long_slot(slot, tz) if slot else ("A time to be confirmed", "")
    hero = {"label": "When", "value": day, "sub": hours} if slot else None
    where_rows = _clinic_rows(clinic)
    maps = _maps_url(clinic)
    sections: list[dict[str, Any]] = [
        {"title": "Where", "rows": [("Clinic", name)] + where_rows, "links": [(maps, "Get directions")] if maps else []},
        {"title": "Bring", "items": BRING + ["Arrive about 10 minutes early"]},
    ]
    report_url = (appt.approval or {}).get("report_url")
    if report_url:
        sections[-1]["links"] = [(report_url, "Your report (show it to the clinic if asked)")]

    buttons: list[tuple[str, str, bool]] = []
    attachments: list[Attachment] = []
    status = calendar.get("status")
    if slot:
        attachments.append(Attachment(f"appointment-{appt.id[:8]}.ics", appointment_ics(appt, tz=tz),
                                      "text/calendar; charset=utf-8; method=PUBLISH"))
        if status == "added":
            sections.append({"title": "Your calendar", "text": "This appointment is in your Google Calendar."})
        elif calendar.get("mode") == "google":
            sections.append({"title": "Add it to your calendar?",
                             "text": f"{settings.product_name} never changes your calendar without your OK. Tap the button "
                                     "and we add this appointment to your Google Calendar. The .ics invitation is attached "
                                     "for Apple Calendar and Outlook.",
                             "links": [(calendar["ics_url"], "Download the invitation (.ics)")]})
            buttons.append((calendar["add_url"], "Add to Google Calendar", True))
        elif calendar.get("mode") == "manual":
            sections.append({"title": "Add it to your calendar",
                             "text": "Open the attached invitation (.ics) in Apple Calendar or Outlook, or use the button "
                                     "to add it to Google Calendar. Nothing is added until you save it there."})
            buttons.append((calendar["google_url"], "Add to Google Calendar", True))
            buttons.append((calendar["ics_url"], "Download .ics", False))
    # "Need to change it?" goes BEFORE the calendar section so the calendar button (rendered after
    # the sections) sits directly under the calendar text.
    change = {"title": "Need to change it?",
              "text": f"Call the clinic{(' at ' + clinic['phone']) if clinic.get('phone') else ''} to move the time, "
                      f"or cancel in {settings.product_name} → Appointments.",
              "links": [(app_url(appt), "Open the appointment")]}
    if slot and sections[-1]["title"].startswith("Add it to your calendar") or (slot and sections[-1]["title"] == "Your calendar"):
        sections.insert(len(sections) - 1, change)
    else:
        sections.append(change)
    subject = f"Confirmed: {name}" + (f" · {slot_label(slot, tz)}" if slot else "")
    return _render(user, appt, subject=subject, title=f"{name} confirmed your appointment", eyebrow="Appointment confirmed",
                   lead=f"{_greeting(user)} good news — the clinic confirmed a time.", hero=hero, sections=sections,
                   buttons=buttons or None, attachments=attachments, banner=_sandbox_banner(appt),
                   preheader=f"{day}{(' · ' + hours) if hours else ''}")


__all__ = ["approval_email", "requested_email", "confirmed_email", "appointment_ics", "google_calendar_url",
           "calendar_options", "long_slot"]
