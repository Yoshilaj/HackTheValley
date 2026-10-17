"""Inbound webhooks for the booking agent (public; signature-verified).

    POST /api/webhooks/resend/inbound           Resend Receiving `email.received` (Svix-signed)
    POST /api/webhooks/elevenlabs/post-call     ElevenLabs Agents post-call webhook (HMAC-signed)

Verification: with RESEND_WEBHOOK_SECRET set, the Svix headers (`svix-id`, `svix-timestamp`,
`svix-signature`) are checked against the raw body (401 when invalid). Without a secret the
request is accepted only in DEV_MODE (503 otherwise). Processing: app/services/booking/inbound.py.
ElevenLabs: with ELEVENLABS_WEBHOOK_SECRET set, `ElevenLabs-Signature: t=…,v0=…` is checked against
the raw body (401 when missing/invalid); without a secret only in DEV_MODE (503 otherwise).
Processing: app/services/booking/channels/phone.py (`handle_post_call`).
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.services.booking import inbound

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/resend/inbound")
def resend_inbound(request: Request, body: bytes = Depends(raw_body), db: Session = Depends(get_db)) -> dict:
    """Clinic reply received by Resend → matched to an appointment, classified, handled →
    `{status: processed|duplicate|unmatched|ignored, appointment_id?, outcome?, action?, appointment_status?}`."""
    secret = settings.resend_webhook_secret
    if secret:
        try:
            inbound.verify_svix(secret, request.headers.get("svix-id"), request.headers.get("svix-timestamp"),
                                request.headers.get("svix-signature"), body)
        except inbound.WebhookVerificationError as exc:
            log.warning("resend webhook rejected", extra={"reason": str(exc)})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature") from exc
    elif not settings.dev_mode:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "RESEND_WEBHOOK_SECRET is not configured")
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid payload")
    return inbound.handle_resend_event(db, payload)


@router.post("/elevenlabs/post-call")
def elevenlabs_post_call(request: Request, body: bytes = Depends(raw_body), db: Session = Depends(get_db)) -> dict:
    """ElevenLabs `post_call_transcription` / `call_initiation_failure` → transcript stored, outcome
    applied → `{status: processed|duplicate|unmatched|ignored, appointment_id?, outcome?, action?,
    appointment_status?}`. `post_call_audio` and other types are acknowledged and ignored."""
    from app.services.booking.channels import phone

    secret = settings.elevenlabs_webhook_secret
    if secret:
        try:
            phone.verify_signature(secret, request.headers.get("elevenlabs-signature"), body)
        except phone.WebhookVerificationError as exc:
            log.warning("elevenlabs webhook rejected", extra={"reason": str(exc)})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid webhook signature") from exc
    elif not settings.dev_mode:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ELEVENLABS_WEBHOOK_SECRET is not configured")
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid payload")
    return phone.handle_post_call(db, payload)
