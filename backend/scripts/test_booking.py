"""Run ONE sandbox booking through a single channel, on demand (phone, web or e-mail).

    cd backend && .venv/bin/python -m scripts.test_booking                      # phone (default)
    cd backend && .venv/bin/python -m scripts.test_booking --channel web --headed --slow-mo 400
    cd backend && .venv/bin/python -m scripts.test_booking --channel email

Against the database in DATABASE_URL (normally data/dev.db, shared with the running uvicorn):
  1. takes the demo user's latest `followup_recommended` assessment (runs the risk engine once,
     without follow-up hooks, if there is none; run scripts.seed_demo first);
  2. plans a booking with the fictional seed clinics only (Google Places is not used) and with ONLY
     the chosen channel, so the other two are never tried;
  3. shows the approval card and asks you to type `yes`;
  4. approves -> the channel runs against the SANDBOX target and the result is printed:
       phone  the AI agent calls SANDBOX_CLINIC_PHONE (you play the receptionist); waits for the
              post-call webhook (uvicorn + ngrok, BASE_URL = the ngrok URL), else polls ElevenLabs.
       web    Chromium fills the mock clinic form at BASE_URL/mock-clinic (forced, whatever
              SANDBOX_CLINIC_WEBSITE says). The browser loads BASE_URL, so the backend (uvicorn on
              port 8000, behind ngrok when BASE_URL is the ngrok URL) MUST be running. Watch the
              bookings arrive at BASE_URL/mock-clinic/admin. --headed / --slow-mo MS override
              WEB_BOOKING_HEADED / WEB_BOOKING_SLOW_MO_MS for this run.
       email  one booking request to SANDBOX_CLINIC_EMAIL; reply to it from that inbox (the reply
              goes to book+<token>@BOOKING_REPLY_DOMAIN -> Resend inbound webhook -> uvicorn); the
              script polls the appointment status for up to 15 min.

Safety: refuses to run unless BOOKING_TARGET_MODE=sandbox (and the channel's sandbox target is set);
seed clinics only; nothing is sent before you type `yes`. Uses the offline FakeLlm for planning
(no Gemini quota). Never prints keys.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402

DEMO_EMAIL = "demo@example.com"
CHANNELS = ("phone", "web", "email")
WAIT_SECONDS = {"phone": 420, "web": 0, "email": 900}


class _NoPlaces:
    """Forces discovery onto the fictional seed clinics."""

    is_fake = True

    def search_text(self, *args, **kwargs):
        return []


class ScriptError(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(1)
        self.msg = msg


def _fail(msg: str) -> None:
    raise ScriptError(msg)


# ================================================================ arguments + settings (pure-ish)

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m scripts.test_booking",
                                 description="Run one sandbox booking through a single channel.")
    ap.add_argument("--channel", choices=CHANNELS, default="phone", help="booking channel to run (default phone)")
    ap.add_argument("--headed", action="store_true", help="web: show the Chromium window (needs a display)")
    ap.add_argument("--slow-mo", type=int, default=None, metavar="MS",
                    help="web: delay every browser action by MS milliseconds (e.g. 400)")
    args = ap.parse_args(argv)
    if args.channel != "web" and (args.headed or args.slow_mo is not None):
        ap.error("--headed / --slow-mo only apply to --channel web")
    if args.slow_mo is not None and not 0 <= args.slow_mo <= 5000:
        ap.error("--slow-mo must be between 0 and 5000 ms")
    return args


def apply_overrides(args: argparse.Namespace) -> None:
    """Settings for this run only (override the environment / .env)."""
    if args.channel == "web":
        from app.services.booking import sandbox

        settings.web_booking_enabled = True
        settings.sandbox_clinic_website = sandbox.mock_clinic_base()  # always the built-in mock clinic
        if args.headed:
            settings.web_booking_headed = True
        if args.slow_mo is not None:
            settings.web_booking_slow_mo_ms = args.slow_mo


def preflight(channel: str) -> list[str]:
    """Problems that stop the run before anything is planned (empty = go)."""
    problems = []
    if settings.booking_target_mode != "sandbox":
        problems.append("BOOKING_TARGET_MODE must be sandbox for this test")
    if channel == "phone":
        from app.services.booking.channels.phone import PhoneChannel

        if not settings.sandbox_clinic_phone:
            problems.append("set SANDBOX_CLINIC_PHONE in backend/.env (the phone that plays the clinic)")
        elif not PhoneChannel().configured():
            problems.append("phone channel not configured: need ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID and "
                            "ELEVENLABS_AGENT_PHONE_NUMBER_ID in backend/.env")
    elif channel == "email":
        if not settings.sandbox_clinic_email:
            problems.append("set SANDBOX_CLINIC_EMAIL in backend/.env (the inbox that plays the clinic)")
    elif channel == "web":
        from app.services.booking.channels import web

        if not web.chromium_installed():
            problems.append("Chromium for Playwright missing: .venv/bin/python -m playwright install chromium")
        elif settings.web_booking_headed and not web.chromium_installed(headed=True):
            problems.append("--headed needs the full Chromium build (not only the headless shell): "
                            ".venv/bin/python -m playwright install chromium")
    return problems


def backend_reachable(base_url: str, timeout: float = 10.0, attempts: int = 3) -> str | None:
    """None when BASE_URL/mock-clinic answers (the browser will load it), else why not. Only the
    user's own backend (BASE_URL) is contacted, with the ngrok interstitial skipped."""
    import httpx

    from app.services.booking.channels.web import NGROK_SKIP_HEADER

    import time

    url = base_url.rstrip("/") + "/mock-clinic"
    for attempt in range(attempts):  # ngrok tunnels occasionally drop a connection; retry briefly
        try:
            r = httpx.get(url, timeout=timeout, headers={NGROK_SKIP_HEADER: "1"}, follow_redirects=False)
            break
        except httpx.HTTPError as exc:
            if attempt == attempts - 1:
                return f"{url} is not reachable ({type(exc).__name__})"
            time.sleep(2)
    if r.status_code != 200 or "Book online" not in r.text:
        return f"{url} answered HTTP {r.status_code} without the mock clinic page"
    return None


def build_channels(channel: str) -> dict[str, Any]:
    """ONLY the chosen channel (the others are never tried)."""
    if channel == "phone":
        from app.services.booking.channels.phone import PhoneChannel

        return {"phone": PhoneChannel()}
    if channel == "web":
        from app.services.booking.channels.web import WebChannel

        return {"web": WebChannel()}
    from app.services.booking.channels.email import EmailChannel

    return {"email": EmailChannel()}


def web_detector(url: str) -> Any:
    """Web test: detection reads the built-in mock clinic (the forced sandbox target) instead of the
    seed clinic's own variant, so e.g. a login-walled seed site still gets a form to fill."""
    from app.services.booking import sandbox
    from app.services.booking.channels.detect import detect_channels

    return detect_channels(sandbox.mock_clinic_base())


def confirm(prompt: str) -> bool:
    return input(prompt).strip().lower() == "yes"


# ================================================================ run

def _assessment(db: Any, user: Any) -> Any:
    from sqlalchemy import select

    from app.db.models import RiskAssessment

    def latest_followup():
        return db.scalar(select(RiskAssessment).where(
            RiskAssessment.user_id == user.id, RiskAssessment.level == "followup_recommended")
            .order_by(RiskAssessment.created_at.desc()))

    assessment = latest_followup()
    if assessment is None:
        # Run the risk engine once WITHOUT the follow-up hooks (they would start a normal booking).
        from app.services import risk_service

        saved = list(risk_service.FOLLOWUP_HOOKS)
        risk_service.FOLLOWUP_HOOKS.clear()
        try:
            risk_service.assess_user(db, user, trigger="manual")
            db.commit()
        finally:
            risk_service.FOLLOWUP_HOOKS.extend(saved)
        assessment = latest_followup()
    return assessment


def _print_card(appt: Any, channel: str) -> None:
    card = appt.approval or {}
    clinic = card.get("clinic") or {}
    plan = next((c for c in card.get("channel_plan") or [] if c.get("channel") == channel), {})
    print("\n=== Approval card ===")
    print(f"Clinic   : {clinic.get('name')}  (fictional)")
    if channel == "web":
        print(f"Channel  : web -> {plan.get('target') or '?'}  (sandbox: the built-in mock clinic)")
    else:
        real = clinic.get("phone") if channel == "phone" else clinic.get("email")
        print(f"Channel  : {channel} -> {plan.get('target') or '?'}  (sandbox; real {real or '-'} is NOT contacted)")
    for i, s in enumerate(card.get("slots") or [], 1):
        print(f"Slot {i}   : {s.get('label') or s.get('start')}")
    print(f"Shared   : {', '.join(card.get('shared_fields') or [])}")
    missing = card.get("missing_profile_fields") or []
    print(f"Missing  : {', '.join(missing) if missing else 'none'}")


def _web_details(appt: Any) -> dict[str, Any]:
    for e in reversed(appt.agent_log or []):
        if e.get("type") == "channel" and e.get("channel") == "web":
            return e.get("details") or {}
    return {}


def _print_result(db: Any, appt: Any, channel: str) -> None:
    from sqlalchemy import select

    from app.db.models import AppointmentMessage

    print("\n=== Result ===")
    print(f"Status   : {appt.status}")
    print(f"Reference: {appt.external_ref or '-'}")
    print(f"Slot     : {(appt.chosen_slot or {}).get('start') or '-'}")
    last = (appt.channel_attempts or [])[-1:] or [{}]
    if last[0].get("reason"):
        print(f"Note     : {last[0].get('reason')}")
    if channel == "web":
        det = _web_details(appt)
        for label, path in (det.get("screenshots") or {}).items():
            print(f"Screenshot {label}: {path}")
        if det.get("pages"):
            print(f"Pages    : {' -> '.join(det['pages'])}")
        if det.get("duration_s") is not None:
            print(f"Duration : {det['duration_s']} s")
    q = (appt.approval or {}).get("pending_question")
    if q:
        print(f"Question : {q.get('question') or q}")
    msg = db.scalar(select(AppointmentMessage).where(
        AppointmentMessage.appointment_id == appt.id, AppointmentMessage.direction == "in")
        .order_by(AppointmentMessage.created_at.desc()))
    if msg is not None:
        cls = msg.classification or {}
        print(f"Outcome  : {cls.get('outcome')}  {cls.get('summary') or ''}")
        print(("Transcript" if channel == "phone" else "Reply") + ":\n" + (msg.body or "")[:3000])
    print(f"\nAppointment id: {appt.id}")


def _wait(db: Any, appt_id: str, channel: str, llm: Any) -> Any:
    from app.db.models import Appointment

    deadline = time.time() + WAIT_SECONDS[channel]
    appt = db.get(Appointment, appt_id)
    while time.time() < deadline:
        time.sleep(5)
        db.expire_all()
        appt = db.get(Appointment, appt_id)
        if appt.status != "requested" or appt.channel != channel:
            return appt
    if channel == "phone":
        from app.services.booking.channels.phone import poll_conversation

        print("No webhook yet; polling ElevenLabs for the conversation result once.")
        poll_conversation(db, appt, llm=llm)
        db.commit()
        db.expire_all()
        appt = db.get(Appointment, appt_id)
    else:
        print("No reply matched within 15 min (is the Resend inbound webhook reaching uvicorn?).")
    return appt


def run(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.db import session as db_session
    from app.db.models import User
    from app.providers.llm import FakeLlm
    from app.services.booking import orchestrator

    channel = args.channel
    apply_overrides(args)
    problems = preflight(channel)
    if problems:
        _fail("; ".join(problems))
    if channel == "phone" and not settings.base_url.startswith("https://"):
        print("WARNING: BASE_URL is not HTTPS; the post-call webhook cannot reach this machine. "
              "The script will fall back to polling ElevenLabs.")
    if channel == "web":
        why = backend_reachable(settings.base_url)
        if why:
            _fail(f"{why}. The browser loads the mock clinic from BASE_URL: start the backend first "
                  "(.venv/bin/uvicorn app.main:app --port 8000, plus ngrok when BASE_URL is the ngrok URL)")
        mode = ("headed" if settings.web_booking_headed else "headless") + (
            f", slow-mo {settings.web_booking_slow_mo_ms} ms" if settings.web_booking_slow_mo_ms else "")
        print(f"Web channel: {mode}. Watch the bookings arrive at {settings.base_url.rstrip('/')}/mock-clinic/admin")
    if channel == "email":
        if not settings.booking_reply_domain:
            print("WARNING: BOOKING_REPLY_DOMAIN is not set; replies go to the demo user's address and are "
                  "NOT matched back to the appointment.")
        if not settings.resend_api_key:
            print("WARNING: RESEND_API_KEY is not set; the e-mail goes to the offline FakeEmail outbox only.")

    db_session.init_db(db_session.engine)
    db = db_session.SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            _fail("demo user missing: run `.venv/bin/python -m scripts.seed_demo` first")
        assessment = _assessment(db, user)
        if assessment is None:
            _fail("the demo user has no followup_recommended assessment; re-run scripts.seed_demo")

        channels = build_channels(channel)
        llm = FakeLlm()
        kw: dict[str, Any] = {"detector": web_detector} if channel == "web" else {}
        appt = orchestrator.run_booking_agent(db, user, assessment, llm, places=_NoPlaces(), channels=channels, **kw)
        if appt is None:
            _fail("no share_with_clinic consent for the demo user")
        db.commit()
        if appt.status != "awaiting_approval":
            _fail(f"planning ended in status {appt.status}; see agent_log of appointment {appt.id}")

        _print_card(appt, channel)
        action = {"phone": "place the call", "web": "fill and submit the form", "email": "send the e-mail"}[channel]
        if not confirm(f"\nType 'yes' to approve and {action}: "):
            orchestrator.decline_appointment(db, user, appt.id)
            print("Declined; nothing was sent.")
            return 0

        appt = orchestrator.approve_appointment(db, user, appt.id, llm=llm, channels=channels)
        appt_id = appt.id
        if channel == "web":
            _print_result(db, appt, channel)
            return 0 if appt.status in ("confirmed", "requested") and appt.channel == "web" else 2
        if appt.status != "requested" or appt.channel != channel:
            _fail(f"{channel} not started (status {appt.status}); last attempts: {(appt.channel_attempts or [])[-2:]}")
        if channel == "phone":
            print(f"\nCalling now. Answer your phone and play the receptionist. Conversation id: {appt.external_ref}")
            print(f"Waiting up to {WAIT_SECONDS['phone'] // 60} min for the post-call webhook...")
        else:
            from app.services.booking.channels.email import reply_address

            token = appt.reply_token
            print(f"\nE-mail sent to {settings.sandbox_clinic_email} (sandbox).")
            print(f"Reply token  : {token}")
            print(f"Reply-To     : {reply_address(token) or user.email}")
            print(f"Now open the {settings.sandbox_clinic_email} inbox and REPLY to the booking request "
                  "(e.g. 'Confirmed for the first time' or propose another time).")
            print(f"Polling the appointment status for up to {WAIT_SECONDS['email'] // 60} min...")
        appt = _wait(db, appt_id, channel, llm)
        _print_result(db, appt, channel)
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run(args)
    except ScriptError as exc:
        print(f"ERROR: {exc.msg}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
