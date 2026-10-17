"""FastAPI app: lifespan (tables, scheduler, follow-up hooks), CORS, error handlers, routers.

Every router is mounted exactly once under /api (the public report page is at /r/{token}).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import (
    admin, appointments, auth, chat, dashboard, health, integrations, jobs, logs, metrics, notifications,
    observations, profile, reports, risk, screener, tasks, uploads, webhooks,
)
from app.core.config import check_production_settings, settings
from app.core.logging import get_logger, setup_logging
from app.db import session as db_session
from app.providers.embeddings import ProviderConfigError
from app.providers.llm import LlmConfigError

setup_logging(settings.log_level, settings.log_json)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_production_settings(settings)
    # Hackathon: create tables at startup instead of Alembic migrations.
    db_session.init_db(db_session.engine)
    if not settings.dev_mode:
        log.info("production mode: X-User-Id header disabled")
    # Background jobs (app/jobs): in-process APScheduler, 1 uvicorn worker assumed.
    from app.jobs.scheduler import start_scheduler, stop_scheduler

    if settings.scheduler_enabled:
        start_scheduler()
    # Report + booking agent on followup_recommended (guarded; idempotent; import-order independent).
    from app.services.followup import register_followup_hooks

    register_followup_hooks()
    try:
        yield
    finally:
        stop_scheduler()  # idempotent


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ProviderConfigError)
@app.exception_handler(LlmConfigError)
async def provider_config_error(_: Request, exc: Exception) -> JSONResponse:
    """A real provider is selected but not configured (e.g. missing API key) → 503, not a traceback."""
    log.error("provider not configured", extra={"error": str(exc)})
    return JSONResponse(status_code=503, content={"detail": str(exc), "error": "provider_not_configured"})


# Everything is mounted under /api so the frontend proxy can forward it.
for module in (
    health,                       # GET /api/health
    auth, profile,                # accounts, profile, consent, export / delete
    metrics, logs, uploads, observations,   # health-data ingestion (C1)
    screener, tasks,              # questionnaires + assessment tasks
    chat,                         # chat, check-in, RAG ingest, sources (C2/C3)
    risk, dashboard,              # risk engine + pathway cards (C4)
    reports, appointments, integrations,    # reports (C7), booking agent (C6), Google
    notifications, jobs, admin,   # notifications, background jobs, dev/admin helpers
    webhooks,                     # inbound provider webhooks (Resend inbound e-mail)
):
    app.include_router(module.router, prefix="/api")
app.include_router(reports.public_router)  # public GET /r/{share_token}
from app.api.routes import mock_clinic  # noqa: E402  (fictional clinic site for the web channel, /mock-clinic)
app.include_router(mock_clinic.router)  # booking-v2 §3, not under /api: it is a "clinic website"
