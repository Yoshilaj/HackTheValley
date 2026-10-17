"""Liveness + configuration summary (no secrets): GET /api/health."""

from fastapi import APIRouter

from app import __version__
from app.core.config import settings

router = APIRouter(tags=["health"])


def provider_summary() -> dict[str, str]:
    """Which implementation each external integration resolves to right now."""
    from app.integrations.google_oauth import google_configured
    from app.providers.email import get_email_provider

    return {
        "llm": settings.llm_provider,
        "embeddings": settings.embedding_provider,
        "stt": settings.stt_provider,
        "email": get_email_provider().name,
        "calendar": "google" if google_configured() else "fake",
    }


@router.get("/health")
def health():
    """``{status, version, providers: {llm, embeddings, stt, email, calendar}, db, scheduler}``."""
    from app.jobs.scheduler import scheduler_status

    return {
        "status": "ok",
        "version": __version__,
        "providers": provider_summary(),
        "db": "postgres" if settings.is_postgres else "sqlite",
        "scheduler": "running" if scheduler_status()["running"] else "disabled",
    }
