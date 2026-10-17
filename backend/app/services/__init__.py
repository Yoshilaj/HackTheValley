"""Business logic. Routes stay thin and call into these modules."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def llm_audit_hook(db: Session, user_id: str | None, resource: str = "llm"):
    """Audit hook for `app.providers.llm` providers: writes AuditLog(action='llm_call',
    actor='llm', detail={model, input/output tokens, purpose, ...}) in the caller's
    transaction (BACKEND_PLAN Security: AuditLog on every LLM call)."""
    from app.core.security import audit

    def hook(detail: dict[str, Any]) -> None:
        audit(db, user_id, "llm_call", resource, detail, actor="llm")

    return hook
