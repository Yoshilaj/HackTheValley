"""Create or update the ElevenLabs voice agent that phones clinics (booking-v2 spec §1.6 "phone").

    .venv/bin/python -m scripts.setup_voice_agent                 # agent + post-call webhook
    .venv/bin/python -m scripts.setup_voice_agent --import-number # also import/assign the Twilio number
    .venv/bin/python -m scripts.setup_voice_agent --dry-run       # print the agent config, no API calls

Idempotent:
- post-call webhook: a workspace webhook with url ``{BASE_URL}/api/webhooks/elevenlabs/post-call``
  is reused when it exists, else created (HMAC). A NEW webhook's signing secret is printed ONCE to
  the terminal — paste it into ``backend/.env`` as ``ELEVENLABS_WEBHOOK_SECRET`` yourself (this
  script never writes any file); an existing webhook's secret cannot be read back — keep the one you
  have, or delete the webhook in the dashboard and re-run.
- agent: ``ELEVENLABS_AGENT_ID`` (or an agent named ``AGENT_NAME``) is PATCHed, else created.
- ``--import-number``: the Twilio number ``TWILIO_PHONE_NUMBER`` is reused when already imported,
  else imported with ``TWILIO_ACCOUNT_SID`` / ``TWILIO_AUTH_TOKEN``; it is assigned to the agent.
Prints the agent id and phone number id to put in ``.env``. Never prints API keys; the only secret it
prints is a newly created webhook signing secret (shown once, as above).
Needs ``ELEVENLABS_API_KEY`` with the Agents (ConvAI) permission and a public HTTPS ``BASE_URL``
(the webhook step is skipped for localhost / http URLs — use a tunnel such as ngrok).
API details and doc URLs: app/services/booking/channels/phone.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.core.config import settings
from app.providers.voice import API_BASE

AGENT_NAME = "Health Advocate Booking Caller"
WEBHOOK_NAME = "Health Advocate post-call"
WEBHOOK_PATH = "/api/webhooks/elevenlabs/post-call"

FIRST_MESSAGE = ("Hello, this is an AI assistant calling on behalf of my client, {{patient_name}}. "
                 "I'd like to book an appointment at {{clinic_name}}, if this is the right place. "
                 "The call is transcribed for the patient's records. Do you have a moment?")

SYSTEM_PROMPT = """You are an AI assistant making a phone call to a medical clinic in Canada on behalf of a patient,
to book ONE appointment. You speak English only, briefly, politely and naturally, like a professional assistant.

# Who you are
- You are an AI assistant, not a human and not the patient. Say so at the start (the first message does) and
  answer truthfully whenever asked. You are calling for {{patient_name}}.
- Today is {{today}}. All times are local clinic time ({{clinic_timezone}}).

# What you say up front
- Patient name: {{patient_name}}
- Reason for the visit (generic category only): {{reason_category}}
- Preferred appointment times: {{preferred_slots}}
- Callback phone number for the patient: {{callback_phone}}

# Patient details: answer ONLY when the clinic asks (never volunteer them)
The patient entered these details in their health app so a clinic can register or identify them. Give a
detail only when the receptionist asks for it, give only what was asked, and never read the list out.
An empty value means you do not have it.
- Legal first name: {{patient_first_name}} / last name: {{patient_last_name}}
- Date of birth: {{date_of_birth}} (ISO {{date_of_birth_iso}})
- Sex as on the health card: {{patient_sex}}
- Patient phone: {{patient_phone}} (say it as: {{patient_phone_spoken}})
- E-mail: {{patient_email}} (say it as: {{patient_email_spoken}})
- Home address: {{home_address}} (street {{street}}, city {{city}}, province {{province}})
- Postal code: {{postal_code}} (say it as: {{postal_code_spoken}})
- Family doctor: {{family_doctor_name}}, {{family_doctor_clinic}}, phone {{family_doctor_phone}}
- Already has a referral: {{has_referral}}
- Preferred language: {{preferred_language}}
Rules for these details:
- When asked to spell the e-mail, the name or the postal code, spell it clearly letter by letter and
  character by character (e.g. "r, dot, m, a, c, l, e, o, d, at, example, dot, com"; postal code "M 5 H, 2 N 2").
  Repeat a number or the spelling slowly if they ask again.
- Never invent, guess or "fill in" a value. If a value is empty, say you do not have it and that the patient
  can provide it, or the clinic can reach the patient at {{callback_phone}}.
- Anything that is not in this list — health card / OHIP number and version code, insurance details,
  medications, medical history, symptoms, test results or measurements — you do not have: say the patient will
  bring their health card and provide those details at the visit, or the clinic can contact the patient at
  {{callback_phone}}. Record the question in requested_info.
- Never discuss health details beyond the generic reason category, even if you are asked directly.
If they ask for more medical detail, say a short summary report from the patient's health app can be e-mailed
to the clinic; if they want it, ask for the e-mail address and record it in clinic_email. Never read a link aloud.

# Booking rules
- Offer only the three preferred times: option 1 {{slot_1}}, option 2 {{slot_2}}, option 3 {{slot_3}}.
- If one is available, book it, repeat the date and time back, and ask for a confirmation or reference number.
- If none works and the clinic offers another time, do not accept it on the patient's behalf: ask them to note
  it, say the patient will confirm, and record it as a counter offer with its date and time.
- Do not agree to fees, payments, forms or anything other than booking this one appointment.
- If the clinic says a referral is needed, ask whether a referral from a family doctor is required before
  booking, record referral_required, and thank them. If they cannot book without it, the outcome is declined.
- If they cannot book at all (not accepting patients, wrong clinic), thank them; the outcome is declined.
- If you reach a voicemail or answering machine, do NOT leave any message (no names, no health information):
  hang up immediately. If you are put on hold for a long time or nobody speaks, end the call.
- If you are transferred, briefly re-introduce yourself as an AI assistant calling for {{patient_name}}.
- End the call politely once the outcome is clear.
"""

#: Data-collection fields (the post-call webhook's analysis.data_collection_results).
DATA_COLLECTION: dict[str, dict[str, Any]] = {
    "outcome": {"type": "string", "enum": ["confirmed", "counter_offer", "needs_info", "declined", "no_answer",
                                           "voicemail", "other"],
                "description": ("Result of the call. confirmed: the clinic booked one of the three offered times. "
                                "counter_offer: the clinic proposed a different time. needs_info: the clinic needs "
                                "information or a decision from the patient before booking. declined: the clinic "
                                "cannot book (including referral required before booking). no_answer: nobody "
                                "answered or nobody spoke. voicemail: an answering machine or voicemail picked up. "
                                "other: anything else.")},
    "booked_slot": {"type": "string",
                    "description": ("Start of the booked (confirmed) or offered (counter_offer) appointment as ISO "
                                    "8601 with the clinic's UTC offset, e.g. 2026-11-03T10:00:00-05:00. If it is one "
                                    "of the offered options, copy exactly: option 1 = {{slot_1_iso}}, option 2 = "
                                    "{{slot_2_iso}}, option 3 = {{slot_3_iso}}. Empty string if no time.")},
    "requested_info": {"type": "string",
                       "description": "The clinic's question or the information it needs from the patient; empty if none."},
    "summary": {"type": "string", "description": "One or two sentences summarising the call outcome, no medical details."},
    "confirmation_number": {"type": "string",
                            "description": "Confirmation or reference number given by the clinic; empty if none."},
    "referral_required": {"type": "boolean",
                          "description": "True if the clinic said a family-doctor referral is required."},
    "clinic_email": {"type": "string",
                     "description": "E-mail address the clinic gave for the summary report; empty if none."},
}

EVALUATION = [
    {"id": "disclosed_ai", "name": "Disclosed AI assistant", "type": "prompt",
     "conversation_goal_prompt": "The assistant said it is an AI assistant calling on behalf of the patient."},
    {"id": "minimal_disclosure", "name": "Minimal disclosure", "type": "prompt",
     "conversation_goal_prompt": ("The assistant volunteered only the patient name, callback number, generic reason "
                                  "and preferred times; it gave other patient details (date of birth, e-mail, "
                                  "address, family doctor …) only when the clinic asked, invented none, and shared no "
                                  "symptoms, measurements, medications, diagnoses or health-card number.")},
    {"id": "booked_offered_slot", "name": "Booked within offered times", "type": "prompt",
     "conversation_goal_prompt": "Any booked appointment is one of the three offered times."},
]

PLACEHOLDERS = {"appointment_id": "test", "clinic_name": "Demo Clinic", "patient_name": "Alex Tremblay",
                "callback_phone": "+1 416 555 0199", "reason_category": "General check-up",
                "preferred_slots": "option 1: Tuesday, November 3 at 10:00 AM", "clinic_timezone": "America/Toronto",
                "today": "Monday, October 19, 2026", "reminder": "no",
                "slot_1": "Tuesday, November 3 at 10:00 AM", "slot_2": "Wednesday, November 4 at 2:00 PM",
                "slot_3": "Friday, November 6 at 9:30 AM", "slot_1_iso": "2026-11-03T10:00:00-05:00",
                "slot_2_iso": "2026-11-04T14:00:00-05:00", "slot_3_iso": "2026-11-06T09:30:00-05:00",
                "patient_first_name": "Alex", "patient_last_name": "Tremblay", "date_of_birth": "February 3, 1950",
                "date_of_birth_iso": "1950-02-03", "patient_sex": "female", "patient_phone": "+1 416 555 0199",
                "patient_phone_spoken": "4 1 6, 5 5 5, 0 1 9 9", "patient_email": "alex@example.com",
                "patient_email_spoken": "alex at example dot com",
                "home_address": "401 Bloor St W, Toronto, ON M5S 1X5", "street": "401 Bloor St W", "city": "Toronto",
                "province": "ON", "postal_code": "M5S 1X5", "postal_code_spoken": "M 5 S, 1 X 5",
                "family_doctor_name": "Dr. Demo", "family_doctor_clinic": "Demo Family Health Team",
                "family_doctor_phone": "+1 416 555 0100", "has_referral": "no", "preferred_language": "English"}


def webhook_url(base_url: str | None = None) -> str:
    return (base_url or settings.base_url).rstrip("/") + WEBHOOK_PATH


def public_https(url: str) -> bool:
    return url.startswith("https://") and not re.match(r"https://(localhost|127\.|0\.0\.0\.0|\[::1\])", url)


def agent_config(webhook_id: str | None = None, *, voice_id: str | None = None, llm: str | None = None) -> dict[str, Any]:
    prompt: dict[str, Any] = {
        "prompt": SYSTEM_PROMPT,
        "built_in_tools": {
            "end_call": {"type": "system", "name": "end_call", "description": "",
                         "params": {"system_tool_type": "end_call"}},
            "voicemail_detection": {"type": "system", "name": "voicemail_detection",
                                    "description": "Use as soon as a voicemail greeting or answering machine is detected.",
                                    "params": {"system_tool_type": "voicemail_detection", "voicemail_message": ""}},
        },
    }
    if llm:
        prompt["llm"] = llm
    cfg: dict[str, Any] = {
        "name": AGENT_NAME,
        "tags": ["health-advocate", "booking"],
        "conversation_config": {
            "agent": {"first_message": FIRST_MESSAGE, "language": "en", "prompt": prompt,
                      "dynamic_variables": {"dynamic_variable_placeholders": PLACEHOLDERS}},
            "tts": {"voice_id": voice_id or settings.elevenlabs_voice_id},
        },
        "platform_settings": {"data_collection": DATA_COLLECTION, "evaluation": {"criteria": EVALUATION}},
    }
    if webhook_id:
        cfg["platform_settings"]["workspace_overrides"] = {
            "webhooks": {"post_call_webhook_id": webhook_id, "events": ["transcript", "call_initiation_failure"]}}
    return cfg


def _check(resp: httpx.Response, what: str) -> dict[str, Any]:
    if resp.status_code >= 400:
        raise SystemExit(f"{what} failed: HTTP {resp.status_code} {resp.text[:500]}")
    return resp.json() if resp.content else {}


def ensure_webhook(client: httpx.Client, url: str) -> tuple[str, str | None]:
    """(webhook_id, new secret or None when an existing webhook was reused)."""
    hooks = _check(client.get("/v1/workspace/webhooks"), "list webhooks").get("webhooks") or []
    for h in hooks:
        if h.get("webhook_url") == url and not h.get("is_disabled"):
            return h["webhook_id"], None
    data = _check(client.post("/v1/workspace/webhooks", json={
        "settings": {"auth_type": "hmac", "name": WEBHOOK_NAME, "webhook_url": url}}), "create webhook")
    return data["webhook_id"], data.get("webhook_secret")


def find_agent(client: httpx.Client) -> str | None:
    if settings.elevenlabs_agent_id:
        return settings.elevenlabs_agent_id
    data = _check(client.get("/v1/convai/agents", params={"search": AGENT_NAME, "page_size": 100}), "list agents")
    return next((a["agent_id"] for a in data.get("agents") or [] if a.get("name") == AGENT_NAME), None)


def ensure_agent(client: httpx.Client, config: dict[str, Any]) -> tuple[str, bool]:
    """(agent_id, created)."""
    agent_id = find_agent(client)
    if agent_id:
        _check(client.patch(f"/v1/convai/agents/{agent_id}", json=config), "update agent")
        return agent_id, False
    data = _check(client.post("/v1/convai/agents/create", json=config), "create agent")
    return data["agent_id"], True


def ensure_number(client: httpx.Client, agent_id: str) -> tuple[str, bool]:
    """Import TWILIO_PHONE_NUMBER (or reuse it) and assign it to the agent → (phone_number_id, imported)."""
    number = settings.twilio_phone_number.strip()
    if not number:
        raise SystemExit("TWILIO_PHONE_NUMBER is not set")
    rows = _check(client.get("/v1/convai/phone-numbers"), "list phone numbers")
    rows = rows if isinstance(rows, list) else rows.get("phone_numbers") or []
    existing = next((r for r in rows if r.get("phone_number") == number), None)
    if existing:
        pid = existing["phone_number_id"]
        if (existing.get("assigned_agent") or {}).get("agent_id") != agent_id:
            _check(client.patch(f"/v1/convai/phone-numbers/{pid}", json={"agent_id": agent_id}), "assign agent")
        return pid, False
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        raise SystemExit("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are required to import the number")
    data = _check(client.post("/v1/convai/phone-numbers", json={
        "phone_number": number, "label": "Health Advocate booking", "provider": "twilio",
        "sid": settings.twilio_account_sid, "token": settings.twilio_auth_token, "agent_id": agent_id}),
        "import phone number")
    pid = data["phone_number_id"]
    _check(client.patch(f"/v1/convai/phone-numbers/{pid}", json={"agent_id": agent_id}), "assign agent")
    return pid, True


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--import-number", action="store_true", help="import/assign the Twilio number (TWILIO_*)")
    ap.add_argument("--base-url", default=None, help="public HTTPS base URL (default: BASE_URL)")
    ap.add_argument("--voice-id", default=None, help="ElevenLabs voice (default: ELEVENLABS_VOICE_ID)")
    ap.add_argument("--llm", default=None, help="agent LLM id, e.g. gemini-2.5-flash (default: ElevenLabs default)")
    ap.add_argument("--skip-webhook", action="store_true", help="do not create/attach the post-call webhook")
    ap.add_argument("--dry-run", action="store_true", help="print the agent config and exit (no API calls)")
    args = ap.parse_args(argv)

    url = webhook_url(args.base_url)
    if args.dry_run:
        print(json.dumps(agent_config("<webhook_id>", voice_id=args.voice_id, llm=args.llm), indent=2))
        print(f"\npost-call webhook url: {url}")
        return 0
    if not settings.elevenlabs_api_key:
        print("ELEVENLABS_API_KEY is not set (needs the Agents / ConvAI permission).", file=sys.stderr)
        return 2

    with httpx.Client(base_url=API_BASE, timeout=30, transport=transport,
                      headers={"xi-api-key": settings.elevenlabs_api_key}) as client:
        webhook_id = None
        if args.skip_webhook:
            print("post-call webhook: skipped (--skip-webhook)")
        elif not public_https(url):
            print(f"post-call webhook: skipped — {url} is not a public HTTPS URL (set BASE_URL or --base-url "
                  "to your tunnel / deployment and re-run)")
        else:
            webhook_id, secret = ensure_webhook(client, url)
            if secret:
                print(f"post-call webhook: created {webhook_id} -> {url}")
                print("\n" + "=" * 72)
                print("Paste this into backend/.env as ELEVENLABS_WEBHOOK_SECRET (it cannot be shown again):")
                print(f"ELEVENLABS_WEBHOOK_SECRET={secret}")
                print("=" * 72 + "\n")
            else:
                print(f"post-call webhook: reusing {webhook_id} -> {url} (keep your existing ELEVENLABS_WEBHOOK_SECRET)")
        agent_id, created = ensure_agent(client, agent_config(webhook_id, voice_id=args.voice_id, llm=args.llm))
        print(f"agent: {'created' if created else 'updated'} {agent_id}")
        phone_id = settings.elevenlabs_agent_phone_number_id or None
        if args.import_number:
            phone_id, imported = ensure_number(client, agent_id)
            print(f"phone number: {'imported' if imported else 'reused'} {phone_id} ({settings.twilio_phone_number})")

    print("\nPut these in backend/.env:")
    print(f"ELEVENLABS_AGENT_ID={agent_id}")
    print(f"ELEVENLABS_AGENT_PHONE_NUMBER_ID={phone_id or '<run with --import-number>'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
