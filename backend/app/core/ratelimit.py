"""Per-user (or per-IP) rate limits for expensive endpoints (chat, uploads, tasks).

    router = APIRouter(dependencies=[Depends(rate_limit("tasks"))])
    @router.post("/chat", dependencies=[Depends(rate_limit("chat"))])

Built on slowapi's ``Limiter`` (in-memory moving window, one uvicorn worker assumed). Implemented
as a FastAPI dependency instead of slowapi's decorator so endpoints need no ``request`` argument.
The key is the bearer-token user id when the token decodes, else the X-User-Id header in dev mode,
else the client IP. Disabled with ``RATE_LIMIT_ENABLED=false`` (the test suite does this).
Exceeding a limit returns 429 ``{"detail": ...}`` with a ``Retry-After`` header.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import HTTPException, Request, status
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.auth import decode_access_token
from app.core.config import settings


def rate_limit_key(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        user_id = decode_access_token(auth[7:].strip())
        if user_id:
            return f"user:{user_id}"
    if settings.dev_mode and request.headers.get("x-user-id"):
        return f"user:{request.headers['x-user-id']}"
    device = request.headers.get("x-device-token")
    if device:
        return f"device:{device[:16]}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=rate_limit_key, storage_uri="memory://")


def rate_limit(scope: str, limit: str | None = None) -> Callable[[Request], None]:
    """Dependency factory: at most ``limit`` (default ``settings.rate_limit_default``) calls per key
    per window for every route sharing ``scope``."""

    def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        item = parse(limit or settings.rate_limit_default)
        key = rate_limit_key(request)
        if not limiter.limiter.hit(item, scope, key):
            reset, _ = limiter.limiter.get_window_stats(item, scope, key)
            retry = max(1, int(reset - time.time()))
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                f"Rate limit exceeded ({item}); try again in {retry} s",
                                headers={"Retry-After": str(retry)})

    return dependency
