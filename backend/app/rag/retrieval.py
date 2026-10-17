"""Hybrid research retrieval (GymSync design, sync SQLAlchemy).

    query ─┬─ embed (input_type="query") → vector_search  top 20 ─┐
           └─ lexical_search (FTS / BM25)                 top 20 ─┴→ RRF (k=60) top 20
      → rerank (cross-encoder | identity) all 20 → relevance floor (cross-encoder only)
      → parent expansion (dedupe by parent, best child score)
      → token-budget packing (~3000 tokens, ≤ k passages, ≤ 2 per source)
      → list[Hit] numbered [1]..[n]  (may be EMPTY: nothing in the library is relevant)

Relevance floor: cross-encoder scores are logits on a fixed scale, so candidates scoring below
``settings.rag_min_rerank_score`` are dropped (calibrated on ``tests/fixtures/rag_eval.json``, see
``scripts/eval_rag.py``). The identity reranker's RRF scores carry no relevance signal, so no floor
is applied there. Pathway filter is applied on both search sides; if it leaves nothing (after the
floor), the search is retried without it. Results are cached in-process (LRU), keyed by
(normalised query, pathway, k, budget, floor, embedding model, reranker,
``settings.rag_corpus_version``, corpus signature) so a re-ingest in the same process invalidates
automatically. Each call records stage timings in :data:`last_trace`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session

from app.providers.embeddings import EmbeddingProvider, embed_query, get_embedding_provider
from app.rag import store
from app.rag.expand import expand_to_parents
from app.rag.fusion import RRF_K, reciprocal_rank_fusion
from app.rag.packing import MAX_PER_SOURCE, TOKEN_BUDGET, pack
from app.rag.rerank import Reranker, get_reranker
from app.rag.store import Hit

log = logging.getLogger(__name__)

TOP_K_VECTOR = 20
TOP_K_LEXICAL = 20
RERANK_CANDIDATES = 20  # fused candidates sent to the reranker (cross-encoder cost is linear in this)
CACHE_SIZE = 256


@dataclass
class Trace:
    timings_ms: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    cache_hit: bool = False
    pathway_fallback: bool = False


last_trace: Trace = Trace()
_CACHE: OrderedDict[tuple, list[Hit]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def normalise_query(q: str) -> str:
    return " ".join((q or "").lower().split())


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _candidates(db: Session, query: str, qvec: list[float], pathway: str | None, trace: Trace,
                rrf_k: int) -> list[Hit]:
    t = time.perf_counter()
    vec = store.vector_search(db, qvec, k=TOP_K_VECTOR, pathway=pathway)
    trace.timings_ms["vector"] = round((time.perf_counter() - t) * 1000, 1)
    t = time.perf_counter()
    lex = store.lexical_search(db, query, k=TOP_K_LEXICAL, pathway=pathway)
    trace.timings_ms["lexical"] = round((time.perf_counter() - t) * 1000, 1)
    trace.counts.update(vector=len(vec), lexical=len(lex))
    fused = reciprocal_rank_fusion([vec, lex], k=rrf_k)
    trace.counts["fused"] = len(fused)
    return fused


def _ranked(db: Session, query: str, qvec: list[float], pathway: str | None, reranker: Reranker,
            min_score: float | None, trace: Trace, rrf_k: int) -> list[Hit]:
    """Fused candidates → reranked → relevance floor (only for a calibrated reranker)."""
    fused = _candidates(db, query, qvec, pathway, trace, rrf_k)
    t = time.perf_counter()
    reranked = reranker.rerank(query, fused[:RERANK_CANDIDATES], RERANK_CANDIDATES)
    trace.timings_ms["rerank"] = round((time.perf_counter() - t) * 1000, 1)
    if min_score is not None and getattr(reranker, "calibrated", False):
        kept = [h for h in reranked if h.score >= min_score]
        trace.counts["below_floor"] = len(reranked) - len(kept)
        reranked = kept
    return reranked


def hybrid_retrieve(db: Session, query: str, k: int = 6, pathway: str | None = None,
                    embedding_provider: EmbeddingProvider | None = None, reranker: Reranker | None = None,
                    token_budget: int = TOKEN_BUDGET, rrf_k: int = RRF_K, use_cache: bool = True,
                    max_per_source: int | None = MAX_PER_SOURCE, min_score: float | None = None) -> list[Hit]:
    """Expanded, packed parent hits for ``query`` (≤ ``k``; fewer when the budget is spent; empty
    when no candidate clears the relevance floor).

    All reranked candidates (not just the top ``k``) reach packing so that, once a source has
    ``max_per_source`` passages, the remaining slots are filled from the next-best other sources.
    ``min_score`` defaults to ``settings.rag_min_rerank_score``; it only applies to a calibrated
    (cross-encoder) reranker."""
    from app.core.config import settings

    global last_trace
    trace = Trace()
    t0 = time.perf_counter()
    provider = embedding_provider or get_embedding_provider()
    reranker = reranker or get_reranker(provider)
    if min_score is None:
        min_score = settings.rag_min_rerank_score
    key = None
    if use_cache:
        key = (normalise_query(query), pathway, k, token_budget, rrf_k, max_per_source, min_score,
               getattr(provider, "name", "?"), getattr(reranker, "name", "?"), settings.rag_corpus_version,
               store.corpus_signature(db))
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None:
                _CACHE.move_to_end(key)
        if cached is not None:
            trace.cache_hit = True
            trace.timings_ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
            last_trace = trace
            return [replace(h, meta=dict(h.meta), source=dict(h.source)) for h in cached]

    t = time.perf_counter()
    qvec = embed_query(provider, query)
    trace.timings_ms["embed"] = round((time.perf_counter() - t) * 1000, 1)

    reranked = _ranked(db, query, qvec, pathway, reranker, min_score, trace, rrf_k)
    if pathway and not reranked:
        trace.pathway_fallback = True
        reranked = _ranked(db, query, qvec, None, reranker, min_score, trace, rrf_k)

    t = time.perf_counter()
    parents = expand_to_parents(db, reranked)
    packed = pack(parents, token_budget=token_budget, max_passages=k, max_per_source=max_per_source)
    trace.timings_ms["expand_pack"] = round((time.perf_counter() - t) * 1000, 1)
    trace.counts.update(reranked=len(reranked), parents=len(parents), packed=len(packed),
                        packed_tokens=sum(int(h.meta.get("tokens", 0)) for h in packed))
    trace.timings_ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
    last_trace = trace
    log.debug("rag retrieve", extra={"timings_ms": trace.timings_ms, "counts": trace.counts})

    if key is not None:
        with _CACHE_LOCK:
            _CACHE[key] = [replace(h, meta=dict(h.meta), source=dict(h.source)) for h in packed]
            while len(_CACHE) > CACHE_SIZE:
                _CACHE.popitem(last=False)
    return packed


def trace_dict() -> dict[str, Any]:
    t = last_trace
    return {"timings_ms": dict(t.timings_ms), "counts": dict(t.counts), "cache_hit": t.cache_hit,
            "pathway_fallback": t.pathway_fallback}
