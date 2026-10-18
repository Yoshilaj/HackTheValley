"""Appointment endpoints (thin; logic in app/services/booking/, API in app/services/booking_agent.py)."""

import html as _html
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_admin
from app.core.config import settings
from app.db.models import RiskAssessment, User
from app.db.session import get_db
from app.schemas.appointments import AppointmentDetailOut, AppointmentMessageOut, AppointmentOut, UserInfoIn
from app.services import booking_agent as svc

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.get("", response_model=list[AppointmentOut])
def list_appointments(status_: str | None = Query(None, alias="status"), limit: int = Query(50, ge=1, le=200),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The current user's appointments, newest first (optional ?status=)."""
    return svc.list_appointments(db, user.id, status_, limit)


@router.post("/run/{assessment_id}", response_model=AppointmentDetailOut, dependencies=[Depends(require_admin)])
def run_agent(assessment_id: str, db: Session = Depends(get_db)):
    """Admin/demo: generate the report (if needed) and run the booking agent for an assessment."""
    from app.providers.llm import get_llm_provider
    from app.services import llm_audit_hook

    assessment = db.get(RiskAssessment, assessment_id)
    if assessment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
    user = db.get(User, assessment.user_id)
    try:
        appt = svc.run_booking_agent(db, user, assessment,
                                     get_llm_provider(audit_hook=llm_audit_hook(db, user.id, "booking")))
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if appt is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "share_with_clinic consent missing; nothing was sent")
    return appt


def _act(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found") from exc
    except svc.AppointmentStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class AutoBookIn(BaseModel):
    pathway: Literal["cardio", "cognitive", "parkinsons", "general"]


@router.post("/auto", response_model=AppointmentDetailOut)
def auto_book(body: AutoBookIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The app's high-risk pop-up: the active request for this pathway, or a new one planned by the
    booking agent (awaiting_approval, with `approval.channel_plan` saying which of web / e-mail /
    phone can reach the clinic). Nothing is sent until approve. 404 without an assessment for the
    pathway, 403 without share_with_clinic consent, 409 for an emergency assessment."""
    from app.services import followup

    try:
        appt = followup.start_for_pathway(db, user, body.pathway)
    except followup.NoAssessment as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No risk assessment for this pathway yet") from exc
    except followup.ConsentMissing as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "share_with_clinic consent is not active") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return appt


class ApproveIn(BaseModel):
    channel: Literal["web", "email", "phone"] | None = None


@router.post("/{appointment_id}/approve", response_model=AppointmentDetailOut)
def approve(appointment_id: str, body: ApproveIn | None = None, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    """Approve the plan on the approval card (status awaiting_approval) → the agent contacts the
    clinic (web → email → phone, or only `channel` when given; 422 if the card doesn't list it as
    available). 409 in any other status or without share_with_clinic consent."""
    return _act(svc.approve_appointment, db, user, appointment_id, channel=body.channel if body else None)


@router.post("/{appointment_id}/decline", response_model=AppointmentDetailOut)
def decline(appointment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Decline the plan (status awaiting_approval) → cancelled; nothing is sent."""
    return _act(svc.decline_appointment, db, user, appointment_id)


@router.post("/{appointment_id}/user-info", response_model=AppointmentDetailOut)
def user_info(appointment_id: str, body: UserInfoIn, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Answer the clinic's question or counter-offer (status needs_user_info) → the agent replies
    to the clinic → requested (or confirmed when a counter-offer is accepted)."""
    return _act(svc.submit_user_info, db, user, appointment_id, answer=body.answer, accept=body.accept)


@router.get("/{appointment_id}/messages", response_model=list[AppointmentMessageOut])
def messages(appointment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every message exchanged with the clinic for this appointment (out + in), oldest first."""
    if svc.get_appointment(db, user.id, appointment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    return svc.list_messages(db, user.id, appointment_id)


class CalendarDecisionIn(BaseModel):
    """`add` = write the confirmed time to the user's Google Calendar (needs Google connected +
    calendar_access consent; otherwise 409 and the app offers the .ics); `skip` = stop asking."""

    decision: Literal["add", "skip"] = "add"


@router.get("/{appointment_id}/calendar-sync")
def calendar_sync(appointment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """What the app shows under "Add to calendar" for a confirmed appointment:
    `{ status: pending|added|skipped|none, mode: google|manual, event_id?, add_url?, google_url?, ics_url? }`."""
    return _act(svc.calendar_status_for, db, user, appointment_id)


@router.post("/{appointment_id}/calendar-sync")
def calendar_decision(appointment_id: str, body: CalendarDecisionIn, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    """The user's answer to "Add it to your calendar?" (notification B). Returns the new sync status."""
    if body.decision == "add":
        _act(svc.add_to_calendar, db, user, appointment_id)
    else:
        _act(svc.skip_calendar, db, user, appointment_id)
    return svc.calendar_status_for(db, user, appointment_id)


@router.get("/{appointment_id}", response_model=AppointmentDetailOut)
def get_appointment(appointment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """One appointment including `agent_log` (the UI's "what the agent did")."""
    row = svc.get_appointment(db, user.id, appointment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    return row


@router.post("/{appointment_id}/cancel", response_model=AppointmentDetailOut)
def cancel(appointment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return svc.cancel_appointment(db, user, appointment_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found") from exc


def _page(title: str, body: str, code: int = 200, extra_html: str = "") -> HTMLResponse:
    page = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
            f"initial-scale=1'><meta name='robots' content='noindex'><title>{_html.escape(title)}</title></head>"
            "<body style=\"font-family:-apple-system,'Segoe UI',sans-serif;max-width:520px;margin:48px auto;"
            f"padding:0 16px;color:#1b1f24\"><h1 style='font-size:20px'>{_html.escape(title)}</h1>"
            f"<p>{_html.escape(body)}</p>{extra_html}</body></html>")
    return HTMLResponse(page, status_code=code, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                                         "X-Frame-Options": "DENY"})


def _confirm_errors(fn, *args):
    try:
        return fn(*args), None
    except LookupError:
        return None, _page("Not found", "This appointment request does not exist.", 404)
    except PermissionError:
        return None, _page("Invalid link", "This confirmation link is not valid (it may be for an earlier request "
                                           "or have expired).", 403)
    except svc.AppointmentStateError as exc:
        return None, _page("Cannot confirm", str(exc), 409)
    except ValueError as exc:
        return None, _page("Invalid time", str(exc), 400)


@router.get("/{appointment_id}/confirm", response_class=HTMLResponse, operation_id="confirm_appointment_link")
def confirm_page(appointment_id: str, slot: int = Query(..., ge=0), token: str = Query(...),
                 db: Session = Depends(get_db)):
    """Public clinic confirm link (no auth; HMAC token bound to appointment + clinic + slot).
    GET never changes anything (mail scanners prefetch links): it shows the time and a
    "Confirm this time" button that POSTs to the same URL."""
    info, err = _confirm_errors(svc.confirm_preview, db, appointment_id, slot, token)
    if err is not None:
        return err
    label = _html.escape(info["label"])
    if info["state"] == "already_confirmed":
        return _page("Already confirmed", f"The appointment at {info['label']} is already confirmed. Thank you.")
    if info["state"] != "confirmable":
        return _page("Cannot confirm", info["message"], 409)
    action = _html.escape(f"/api/appointments/{appointment_id}/confirm?slot={slot}&token={token}", quote=True)
    form = (f"<p style='font-size:18px'><strong>{label}</strong></p>"
            f"<form method='post' action='{action}'><button type='submit' style='padding:10px 18px;font-size:15px;"
            "background:#1f5f8b;color:#fff;border:0;border-radius:4px;cursor:pointer'>Confirm this time</button>"
            "</form>")
    return _page(f"Confirm appointment: {info['clinic']}",
                 "Please confirm the appointment time below for the patient's request.", extra_html=form)


@router.post("/{appointment_id}/confirm", response_class=HTMLResponse, operation_id="confirm_appointment_post")
def confirm(appointment_id: str, slot: int = Query(..., ge=0), token: str = Query(...), db: Session = Depends(get_db)):
    """Confirm the slot (the button on the GET page). Returns a tiny HTML page."""
    appt, err = _confirm_errors(svc.confirm_appointment, db, appointment_id, slot, token)
    if err is not None:
        return err
    when = (appt.chosen_slot or {}).get("start", "")
    return _page("Appointment confirmed",
                 f"Thank you. The appointment at {when} is confirmed and the patient has been notified.")


# ---------------------------------------------------------------- e-mail buttons (public, signed links)

_PAGE_CSS = ("font-family:-apple-system,'Segoe UI',sans-serif;max-width:560px;margin:40px auto;padding:0 16px;color:#1d1d1f;"
             "line-height:1.5")
_BTN = ("padding:12px 20px;font-size:15px;font-weight:600;border:0;border-radius:8px;cursor:pointer;")


def _shell(title: str, inner: str, code: int = 200) -> HTMLResponse:
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
            f"initial-scale=1'><meta name='robots' content='noindex'><title>{_html.escape(title)}</title></head>"
            f"<body style=\"{_PAGE_CSS}\"><p style='margin:0 0 20px;font-size:15px;font-weight:700;color:#155bd0'>"
            f"{_html.escape(settings.product_name)}</p><h1 style='font-size:22px;margin:0 0 12px'>{_html.escape(title)}</h1>"
            f"{inner}</body></html>")
    return HTMLResponse(page, status_code=code, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                                         "X-Frame-Options": "DENY"})


def _open_app(url: str, label: str = "Open the app") -> str:
    return (f"<p style='margin-top:24px'><a href='{_html.escape(url, quote=True)}' style='color:#155bd0;font-weight:600'>"
            f"{_html.escape(label)} →</a></p>")


def _link_errors(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), None
    except LookupError:
        return None, _shell("Not found", "<p>This appointment does not exist.</p>", 404)
    except PermissionError:
        return None, _shell("This link has expired",
                            "<p>For your safety the buttons in our e-mails stop working after a while. "
                            "You can still decide in the app.</p>" + _open_app(settings.frontend_origin), 403)
    except svc.AppointmentStateError as exc:
        return None, _shell("Nothing to do", f"<p>{_html.escape(str(exc))}</p>" + _open_app(settings.frontend_origin), 409)


def _rows(rows: list[tuple[str, str]]) -> str:
    return ("<table style='border-collapse:collapse;font-size:15px'>" + "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#6e6e73;vertical-align:top'>{_html.escape(k)}</td>"
        f"<td style='padding:4px 0'>{_html.escape(v)}</td></tr>" for k, v in rows) + "</table>")


def _form(action_url: str, label: str, color: str = "#155bd0") -> str:
    return (f"<form method='post' action='{_html.escape(action_url, quote=True)}' style='margin:20px 0'>"
            f"<button type='submit' style=\"{_BTN}background:{color};color:#fff\">{_html.escape(label)}</button></form>")


@router.get("/{appointment_id}/action", response_class=HTMLResponse, operation_id="approval_link_page")
def approval_link_page(appointment_id: str, do: Literal["approve", "decline"] = Query(..., alias="do"),
                       token: str = Query(...), db: Session = Depends(get_db)):
    """The Approve / Not now buttons of the approval e-mail land here. GET never changes anything
    (mail scanners prefetch links): it shows the request and a button that POSTs to the same URL."""
    info, err = _link_errors(svc.link_preview, db, appointment_id, do, token)
    if err is not None:
        return err
    if info["state"] != "ready":
        return _shell("Already handled", f"<p>{_html.escape(info['state_text'])}</p>" + _open_app(info["app_url"]))
    here = f"/api/appointments/{appointment_id}/action?do={do}&token={token}"
    clinic = _html.escape(info["clinic"])
    if do == "approve":
        inner = (f"<p>We will contact <strong>{clinic}</strong> and propose these times:</p><ul>"
                 + "".join(f"<li>{_html.escape(x)}</li>" for x in info["slots"]) + "</ul>"
                 f"<p>{_html.escape(info['plan'])}</p><p style='font-weight:600;margin-bottom:6px'>The clinic receives</p>"
                 + _rows(info["shared"]) + "<p style='font-size:13px;color:#6e6e73'>Never your measurements, diagnoses, "
                 "medications or health-card number.</p>" + _form(here, "Approve and contact the clinic")
                 + _open_app(info["app_url"], "Review the details in the app instead"))
        return _shell(f"Approve the request to {info['clinic']}?", inner)
    inner = (f"<p>Nothing will be sent to <strong>{clinic}</strong>. You can ask {_html.escape(settings.product_name)} "
             "to book a visit any time from the Appointments page.</p>" + _form(here, "Decline this request", "#6e6e73")
             + _open_app(info["app_url"], "Review the details in the app instead"))
    return _shell("Decline this request?", inner)


@router.post("/{appointment_id}/action", response_class=HTMLResponse, operation_id="approval_link_post")
def approval_link_post(appointment_id: str, do: Literal["approve", "decline"] = Query(..., alias="do"),
                       token: str = Query(...), db: Session = Depends(get_db)):
    """Runs the approval (channels web → e-mail → phone) or declines, exactly like the in-app buttons."""
    appt, err = _link_errors(svc.act_by_link, db, appointment_id, do, token)
    if err is not None:
        return err
    app_url = f"{settings.frontend_origin.rstrip('/')}/appointments?appointment={appointment_id}"
    clinic = _html.escape((appt.clinic or {}).get("name") or "the clinic")
    if do == "decline":
        return _shell("Request declined", f"<p>Nothing was sent to {clinic}.</p>" + _open_app(app_url))
    text = {"requested": f"We are contacting {clinic} now and will e-mail you as soon as it replies.",
            "confirmed": f"{clinic} confirmed a time straight away. Check your inbox for the details.",
            "needs_user_info": f"{clinic} has a question for you. Please answer it in the app.",
            "awaiting_approval": "The first clinics could not be reached; we prepared a new request for you to approve "
                                 "in the app.",
            "failed": "We could not reach any of the clinics. Please see the app for what to do next."}
    return _shell("Thank you — request approved", f"<p>{_html.escape(text.get(appt.status, 'Done.'))}</p>" + _open_app(app_url))


@router.get("/{appointment_id}/calendar", response_class=HTMLResponse, operation_id="calendar_link_page")
def calendar_link_page(appointment_id: str, token: str = Query(...), db: Session = Depends(get_db)):
    """"Add to Google Calendar" from the confirmation e-mail. GET shows the time and a button; POST writes
    the event. Without a connected Google Calendar the page offers the .ics and the Google template link."""
    info, err = _link_errors(svc.link_preview, db, appointment_id, "calendar", token)
    if err is not None:
        return err
    cal = info["calendar"]
    when = _html.escape(info.get("when") or "")
    if info["state"] != "ready":
        return _shell("Nothing to add" if info["state"] == "closed" else "Already in your calendar",
                      f"<p>{_html.escape(info['state_text'])}</p>" + _open_app(info["app_url"]))
    head = f"<p><strong>{_html.escape(info['clinic'])}</strong><br>{when}"
    if info.get("address"):
        head += f"<br><span style='color:#6e6e73'>{_html.escape(info['address'])}</span>"
    head += "</p>"
    if cal.get("mode") == "google":
        inner = (head + "<p>We add this appointment to your Google Calendar as a confirmed event with a one-hour reminder.</p>"
                 + _form(f"/api/appointments/{appointment_id}/calendar?token={token}", "Add to Google Calendar")
                 + f"<p style='font-size:14px'>Prefer another calendar? <a href='{_html.escape(cal['ics_url'], quote=True)}'>"
                   "Download the invitation (.ics)</a></p>" + _open_app(info["app_url"]))
        return _shell("Add it to your calendar?", inner)
    inner = (head + "<p>Your Google Calendar is not connected, so nothing is added automatically. Pick one:</p>"
             f"<p><a href='{_html.escape(cal['google_url'], quote=True)}' style=\"{_BTN}display:inline-block;background:#155bd0;"
             "color:#fff;text-decoration:none\">Open in Google Calendar</a></p>"
             f"<p><a href='{_html.escape(cal['ics_url'], quote=True)}' style=\"{_BTN}display:inline-block;background:#eef1f5;"
             "color:#1d1d1f;text-decoration:none\">Download the invitation (.ics)</a></p>" + _open_app(info["app_url"]))
    return _shell("Add it to your calendar", inner)


@router.post("/{appointment_id}/calendar", response_class=HTMLResponse, operation_id="calendar_link_post")
def calendar_link_post(appointment_id: str, token: str = Query(...), db: Session = Depends(get_db)):
    appt, err = _link_errors(svc.act_by_link, db, appointment_id, "calendar", token)
    if err is not None:
        return err
    app_url = f"{settings.frontend_origin.rstrip('/')}/appointments?appointment={appointment_id}"
    return _shell("Added to your Google Calendar",
                  f"<p>{_html.escape((appt.clinic or {}).get('name') or 'The appointment')} is now in your calendar "
                  "with a reminder one hour before.</p>"
                  "<p><a href='https://calendar.google.com/' style='color:#155bd0;font-weight:600'>Open Google Calendar →</a></p>"
                  + _open_app(app_url))


@router.get("/{appointment_id}/calendar.ics", operation_id="calendar_ics")
def calendar_ics(appointment_id: str, token: str = Query(...), db: Session = Depends(get_db)):
    """The .ics invitation (signed link from the confirmation e-mail / app)."""
    out, err = _link_errors(svc.ics_by_link, db, appointment_id, token)
    if err is not None:
        return err
    data, filename = out
    return Response(data, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})
