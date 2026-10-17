"""Embedding entry point for RAG: a thin batching wrapper over ``app.providers.embeddings``.

``input_type`` is ``"document"`` for corpus chunks and ``"query"`` for user questions (nomic
task prefixes in ``LocalEmbeddings``, Voyage's asymmetric mode; the fake provider ignores it).
Providers with the legacy ``embed(texts)`` signature are still accepted.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.providers.embeddings import EmbeddingProvider, get_embedding_provider

BATCH = 128


def _accepts_input_type(provider: Any) -> bool:
    try:
        return "input_type" in inspect.signature(provider.embed).parameters
    except (TypeError, ValueError):
        return False


def embed(texts: list[str], input_type: str = "document",
          provider: EmbeddingProvider | None = None) -> list[list[float]]:
    """Embed ``texts`` (batched) with ``provider`` (default: ``get_embedding_provider()``)."""
    provider = provider or get_embedding_provider()
    with_type = _accepts_input_type(provider)
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        out.extend(provider.embed(batch, input_type=input_type) if with_type else provider.embed(batch))
    return out


def embed_query(text: str, provider: EmbeddingProvider | None = None) -> list[float]:
    return embed([text], input_type="query", provider=provider)[0]
