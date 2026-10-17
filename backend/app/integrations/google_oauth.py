"""Google OAuth 2.0 (web server flow) for Calendar access.

    oauth = get_oauth_client()
    url = oauth.authorization_url(state=make_state(user.id))     # GET /api/integrations/google/start
    cred = handle_callback(db, code, state, oauth)                # GET /api/integrations/google/callback
    tokens = fresh_tokens(db, user.id, oauth)                     # refreshes when < 60 s left

Tokens are stored as Fernet-encrypted JSON in `OAuthCredential(provider='google')`.
`state` is `<user_id>.<unix_ts>.<hmac>` signed with JWT_SECRET (the callback has no auth
header because Google redirects the browser), valid for 15 minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import audit, decrypt_str, encrypt_str
from app.db.models import OAuthCredential

log = get_logger(__name__)

PROVIDER = "google"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]
STATE_TTL_S = 15 * 60


class OAuthError(RuntimeError):
    pass


def redirect_uri() -> str:
    return settings.base_url.rstrip("/") + "/api/integrations/google/callback"


# ---------------------------------------------------------------- state

def _sign(msg: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"oauth-state:{msg}".encode(), hashlib.sha256).hexdigest()[:32]


def make_state(user_id: str, now: float | None = None) -> str:
    ts = str(int(now if now is not None else time.time()))
    return f"{user_id}.{ts}.{_sign(user_id + '.' + ts)}"


def verify_state(state: str, now: float | None = None) -> str:
    """Return the user id in a valid, unexpired state; raise OAuthError otherwise."""
    try:
        user_id, ts, sig = state.rsplit(".", 2)
    except ValueError as exc:
        raise OAuthError("malformed state") from exc
    if not hmac.compare_digest(sig, _sign(user_id + "." + ts)):
        raise OAuthError("bad state signature")
    if (now if now is not None else time.time()) - int(ts) > STATE_TTL_S:
        raise OAuthError("state expired")
    return user_id


# ---------------------------------------------------------------- clients

class OAuthClient(Protocol):
    name: str
    is_fake: bool

    def authorization_url(self, state: str) -> str: ...
    def exchange_code(self, code: str) -> dict[str, Any]: ...
    def refresh(self, refresh_token: str) -> dict[str, Any]: ...


class GoogleOAuth:
    """Real Google OAuth client (httpx; no google-auth-oauthlib Flow needed)."""

    name = "google"
    is_fake = False

    def __init__(self, client_id: str | None = None, client_secret: str | None = None) -> None:
        self.client_id = client_id or settings.google_client_id
        self.client_secret = client_secret or settings.google_client_secret
        if not (self.client_id and self.client_secret):
            raise OAuthError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured")

    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def _post(self, data: dict[str, str]) -> dict[str, Any]:
        import httpx

        resp = httpx.post(TOKEN_URL, data=data, timeout=15)
        if resp.status_code != 200:
            raise OAuthError(f"token endpoint returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def exchange_code(self, code: str) -> dict[str, Any]:
        return self._post({
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
        })

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._post({
            "refresh_token": refresh_token, "client_id": self.client_id,
            "client_secret": self.client_secret, "grant_type": "refresh_token",
        })


class FakeGoogleOAuth:
    """Offline OAuth: any code is accepted and yields deterministic fake tokens."""

    name = "google"
    is_fake = True

    def __init__(self) -> None:
        self.exchanged: list[str] = []
        self.refreshed: list[str] = []

    def authorization_url(self, state: str) -> str:
        params = {"client_id": "fake-client-id", "redirect_uri": redirect_uri(), "response_type": "code",
                  "scope": " ".join(SCOPES), "state": state, "fake": "1"}
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        self.exchanged.append(code)
        return {"access_token": f"fake-access-{code}", "refresh_token": f"fake-refresh-{code}",
                "expires_in": 3600, "scope": " ".join(SCOPES), "token_type": "Bearer"}

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        self.refreshed.append(refresh_token)
        return {"access_token": f"fake-access-refreshed-{len(self.refreshed)}", "expires_in": 3600,
                "scope": " ".join(SCOPES), "token_type": "Bearer"}


def google_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def get_oauth_client() -> OAuthClient:
    """GoogleOAuth when client id + secret are set, else FakeGoogleOAuth."""
    return GoogleOAuth() if google_configured() else FakeGoogleOAuth()


# ---------------------------------------------------------------- token storage

def get_credential(db: Session, user_id: str) -> OAuthCredential | None:
    return db.scalar(select(OAuthCredential).where(
        OAuthCredential.user_id == user_id, OAuthCredential.provider == PROVIDER))


def load_tokens(cred: OAuthCredential | None) -> dict[str, Any] | None:
    if cred is None:
        return None
    try:
        return json.loads(decrypt_str(cred.encrypted_tokens))
    except Exception:  # key rotated / ephemeral dev key -> treat as disconnected
        log.warning("could not decrypt stored google tokens", extra={"user_id": cred.user_id})
        return None


def store_tokens(db: Session, user_id: str, tokens: dict[str, Any], *, fake: bool = False) -> OAuthCredential:
    """Upsert the encrypted token JSON. Keeps an existing refresh_token when Google omits it."""
    cred = get_credential(db, user_id)
    previous = load_tokens(cred) or {}
    merged = {**previous, **{k: v for k, v in tokens.items() if v is not None}}
    if not tokens.get("refresh_token") and previous.get("refresh_token"):
        merged["refresh_token"] = previous["refresh_token"]
    merged["fake"] = fake
    expires_at = None
    if tokens.get("expires_in"):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(tokens["expires_in"]))
    scopes = (tokens.get("scope") or "").split() or (cred.scopes if cred else None) or SCOPES
    if cred is None:
        cred = OAuthCredential(user_id=user_id, provider=PROVIDER, encrypted_tokens="")
        db.add(cred)
    cred.encrypted_tokens = encrypt_str(json.dumps(merged))
    cred.scopes = scopes
    cred.expires_at = expires_at
    db.flush()
    return cred


def handle_callback(db: Session, code: str, state: str, oauth: OAuthClient | None = None) -> OAuthCredential:
    """Verify `state`, exchange `code`, store tokens encrypted. Commits. Raises OAuthError."""
    oauth = oauth or get_oauth_client()
    user_id = verify_state(state)
    tokens = oauth.exchange_code(code)
    cred = store_tokens(db, user_id, tokens, fake=oauth.is_fake)
    audit(db, user_id, "integration.google.connect", f"oauth_credential:{cred.id}",
          {"scopes": cred.scopes, "fake": oauth.is_fake}, actor="user")
    db.commit()
    return cred


def fresh_tokens(db: Session, user_id: str, oauth: OAuthClient | None = None) -> dict[str, Any] | None:
    """Stored tokens, refreshed first when they expire within 60 s (refresh helper)."""
    cred = get_credential(db, user_id)
    tokens = load_tokens(cred)
    if not tokens or cred is None:
        return None
    soon = datetime.now(timezone.utc) + timedelta(seconds=60)
    if cred.expires_at and cred.expires_at <= soon and tokens.get("refresh_token"):
        oauth = oauth or get_oauth_client()
        try:
            refreshed = oauth.refresh(tokens["refresh_token"])
        except Exception as exc:
            log.warning("google token refresh failed", extra={"error": str(exc)})
            return tokens
        store_tokens(db, user_id, refreshed, fake=oauth.is_fake)
        db.commit()
        tokens = load_tokens(get_credential(db, user_id))
    return tokens


def disconnect(db: Session, user_id: str) -> bool:
    """Delete the stored Google credential (best effort; does not call Google's revoke endpoint)."""
    cred = get_credential(db, user_id)
    if cred is None:
        return False
    db.delete(cred)
    audit(db, user_id, "integration.google.disconnect", f"oauth_credential:{cred.id}", actor="user")
    db.commit()
    return True
