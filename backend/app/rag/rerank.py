"""Rerankers: reorder fused candidates by query×passage relevance before parent expansion.

- ``IdentityReranker`` — keeps the fused (RRF) order, truncated to ``top_n`` (tests, offline mode).
- ``CrossEncoderReranker`` — fastembed ``TextCrossEncoder`` (``Xenova/ms-marco-MiniLM-L-6-v2``,
  ONNX, ~80 MB). A bi-encoder scores query and passage separately; a cross-encoder reads them
  together, so it orders the final few far more accurately. Lazy: loaded on first use and shared
  per process. Scores are raw logits (higher = more relevant; can be negative).
  Memory (measured): +140 MB RSS loaded; scoring 20 passages adds ~+90 MB at batch 1 vs ~+1.2 GB
  at batch 16 (activations scale with batch × 512²), and batch 1 is also faster on CPU — so the
  default batch is 1, inputs are padded to 128-token buckets, and the ONNX arena is disabled.

:func:`get_reranker` picks by ``settings.reranker`` but falls back to identity when the embedding
provider is the offline ``fake`` one, so tests and the no-model demo never download weights.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, Protocol

from app.rag.store import Hit

_MODELS: dict[str, Any] = {}
_LOCK = threading.Lock()


class Reranker(Protocol):
    name: str
    calibrated: bool  # True when scores are comparable across queries (a relevance floor applies)

    def rerank(self, query: str, hits: list[Hit], top_n: int) -> list[Hit]: ...


class IdentityReranker:
    name = "identity"
    calibrated = False  # keeps RRF scores: rank-only, no relevance floor possible

    def rerank(self, query: str, hits: list[Hit], top_n: int) -> list[Hit]:
        return list(hits[:top_n])


class CrossEncoderReranker:
    calibrated = True  # logits on a fixed scale: retrieval applies settings.rag_min_rerank_score

    def __init__(self, model: str | None = None, batch_size: int = 1) -> None:
        if model is None:
            from app.core.config import settings

            model = settings.reranker_model
        self.model = model
        self.name = f"cross_encoder:{model}"
        self.batch_size = batch_size

    def _ensure_model(self):
        m = _MODELS.get(self.model)
        if m is None:
            with _LOCK:
                m = _MODELS.get(self.model)
                if m is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    m = TextCrossEncoder(model_name=self.model, enable_cpu_mem_arena=False)
                    tok = getattr(getattr(m, "model", None), "tokenizer", None)
                    pad = dict(getattr(tok, "padding", None) or {})
                    if pad:
                        pad["pad_to_multiple_of"] = 128
                        pad.pop("length", None)
                        tok.enable_padding(**pad)
                    _MODELS[self.model] = m
        return m

    def rerank(self, query: str, hits: list[Hit], top_n: int) -> list[Hit]:
        if not hits:
            return []
        scores = list(self._ensure_model().rerank(query, [h.text for h in hits], batch_size=self.batch_size))
        scored = [replace(h, score=round(float(s), 6), meta=dict(h.meta, rrf=h.score)) for h, s in zip(hits, scores)]
        order = sorted(range(len(scored)), key=lambda i: (-scored[i].score, i))
        return [scored[i] for i in order[:top_n]]


def get_reranker(embedding_provider: Any = None, name: str | None = None) -> Reranker:
    from app.core.config import settings

    choice = (name or settings.reranker or "identity").lower()
    if name is None and str(getattr(embedding_provider, "name", "")).startswith("fake"):
        choice = "identity"
    if choice == "cross_encoder":
        return CrossEncoderReranker()
    return IdentityReranker()
