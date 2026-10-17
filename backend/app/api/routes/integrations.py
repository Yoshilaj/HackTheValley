"""Google integration endpoints (thin; logic in app/integrations/google_oauth.py)."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.security import has_consent
from app.db.models import User
from app.db.session import get_db
from app.integrations import google_oauth
from app.integrations.google_places import places_configured
from app.providers.email import get_email_provider
from app.schemas.appointments import GoogleStartOut, IntegrationsStatusOut

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/google/start", response_model=GoogleStartOut)
def google_start(user: User = Depends(get_current_user)):
    """Authorization URL for Google Calendar access (frontend redirects the browser to it)."""
    oauth = google_oauth.get_oauth_client()
    state = google_oauth.make_state(user.id)
    return GoogleStartOut(auth_url=oauth.authorization_url(state), state=state, fake=oauth.is_fake)


@router.get("/google/callback")
def google_callback(code: str | None = Query(None), state: str | None = Query(None),
                    error: str | None = Query(None), db: Session = Depends(get_db)):
    """Google redirects here; stores encrypted tokens, then redirects to the frontend settings page."""
    target = settings.frontend_origin.rstrip("/") + "/settings?integration=google"
    if error:
        return RedirectResponse(f"{target}&status=error&reason={quote(error[:200], safe='')}", status_code=303)
    if not code or not state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "code and state are required")
    try:
        google_oauth.handle_callback(db, code, state)
    except google_oauth.OAuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"OAuth failed: {exc}") from exc
    return RedirectResponse(f"{target}&status=connected", status_code=303)


@router.delete("/google", status_code=204)
def google_disconnect(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    google_oauth.disconnect(db, user.id)


@router.get("/status", response_model=IntegrationsStatusOut)
def integrations_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    cred = google_oauth.get_credential(db, user.id)
    tokens = google_oauth.load_tokens(cred) or {}
    return IntegrationsStatusOut(
        google={"configured": google_oauth.google_configured(), "connected": bool(tokens),
                "fake": bool(tokens.get("fake")) if tokens else None,
                "scopes": cred.scopes if cred else [], "expires_at": cred.expires_at if cred else None,
                "calendar_consent": has_consent(db, user.id, "calendar_access")},
        places={"configured": places_configured(), "source": "google_places" if places_configured() else "seed"},
        email={"provider": get_email_provider().name, "clinic_fallback_email": bool(settings.clinic_fallback_email)},
    )
