"""High-level RAG entry points (kept for backwards compatibility; logic lives in services).

- ``ingest_text`` → :func:`app.rag.ingest.ingest_text`
- ``answer_question`` → retrieve + cited answer via :mod:`app.services.rag_service`
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.rag.ingest import ingest_text  # noqa: F401  (re-export)


def answer_question(db: Session, question: str, llm: Any = None, provider: Any = None,
                    language: str = "en", k: int = 6) -> dict[str, Any]:
    """Retrieve ``k`` chunks and answer with ``[n]`` citations (no user context)."""
    from app.providers.llm import get_llm_provider
    from app.services import rag_service

    hits = rag_service.retrieve(db, question, k=k, embedding_provider=provider)
    return rag_service.answer_with_citations(db, llm or get_llm_provider(), question, hits, "", language)
