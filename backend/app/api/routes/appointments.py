"""Appointment endpoints (thin; logic in app/services/booking/, API in app/services/booking_agent.py)."""

import html as _html

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_admin
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


@router.post("/{appointment_id}/approve", response_model=AppointmentDetailOut)
def approve(appointment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Approve the plan on the approval card (status awaiting_approval) → the agent contacts the
    clinic (web → email → phone). 409 in any other status or without share_with_clinic consent."""
    return _act(svc.approve_appointment, db, user, appointment_id)


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
