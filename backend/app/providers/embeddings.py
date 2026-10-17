"""Text-embedding providers.

Used by the research RAG (``app/rag``) and the experimental semantic-recall score.

Selection: ``EMBEDDING_PROVIDER`` = ``fake`` (default, tests/offline) | ``local`` | ``voyage``.

- ``LocalEmbeddings``: nomic-embed-text-v1.5 (768-d, ``settings.embedding_model``, the quantized
  ``-Q`` build by default) in-process via fastembed/ONNX. Lazy: the model is loaded (downloaded on
  first use into ``FASTEMBED_CACHE_PATH``) on the first ``embed`` call and shared per process.
- ``VoyageEmbeddings``: hosted ``voyage-3`` (1024-d; NOT compatible with the 768-d pgvector column).
- ``FakeEmbeddings``: deterministic hashed bag-of-words, ``EMBEDDING_DIM`` (768) by default.

Query vs document: ``embed(texts, input_type="document")`` is the protocol; pass
``input_type="query"`` for search queries (or use :func:`embed_query`). Asymmetric models need
it: nomic expects ``search_query: `` / ``search_document: `` task prefixes, and fastembed does
NOT add them itself, so ``LocalEmbeddings`` prepends them.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from typing import Any, Protocol, Sequence, runtime_checkable

__all__ = [
    "ProviderConfigError",
    "EmbeddingProvider",
    "VoyageEmbeddings",
    "LocalEmbeddings",
    "FakeEmbeddings",
    "embed_query",
    "get_embedding_provider",
    "cosine",
]


def _settings():
    """Lazy settings import (keys/provider choices may come from ``.env``, which is not exported to
    ``os.environ``)."""
    from app.core.config import settings

    return settings


class ProviderConfigError(RuntimeError):
    """A real provider was selected but is misconfigured (missing key or package)."""


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embeds texts into fixed-dimension vectors; ``name`` is stored with every score."""

    dim: int
    name: str

    def embed(self, texts: list[str], input_type: str | None = "document") -> list[list[float]]: ...


def embed_query(provider: EmbeddingProvider, text: str) -> list[float]:
    """Embed one search query (``input_type="query"``), tolerating legacy ``embed(texts)`` providers."""
    try:
        return provider.embed([text], input_type="query")[0]
    except TypeError:
        return provider.embed([text])[0]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0.0 if either vector is all-zero."""
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0.0 or nb == 0.0 else dot / (na * nb)


class VoyageEmbeddings:
    """Voyage AI embeddings (``voyage-3``, 1024-d, multilingual incl. Japanese).

    Batches ≤128 texts per request, retries transient errors with exponential
    backoff. ``input_type`` defaults to ``"document"``; RAG queries should pass
    ``input_type="query"`` to :meth:`embed`.
    """

    MAX_BATCH = 128

    def __init__(
        self,
        model: str = "voyage-3",
        dim: int = 1024,
        api_key: str | None = None,
        max_retries: int = 3,
        timeout_s: float = 30.0,
    ) -> None:
        key = api_key or os.environ.get("VOYAGE_API_KEY", "") or _settings().voyage_api_key
        if not key:
            raise ProviderConfigError("EMBEDDING_PROVIDER=voyage but VOYAGE_API_KEY is not set")
        try:
            import voyageai
        except ImportError as e:  # pragma: no cover - installed in this venv
            raise ProviderConfigError("voyageai package is not installed") from e
        self._client: Any = voyageai.Client(api_key=key, max_retries=0, timeout=timeout_s)
        self.model = model
        self.dim = dim
        self.name = f"voyage:{model}"
        self.max_retries = max_retries

    def embed(self, texts: list[str], input_type: str | None = "document") -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.MAX_BATCH):
            batch = [t if t.strip() else " " for t in texts[i : i + self.MAX_BATCH]]
            for attempt in range(self.max_retries + 1):
                try:
                    res = self._client.embed(batch, model=self.model, input_type=input_type)
                    out.extend([list(map(float, v)) for v in res.embeddings])
                    break
                except Exception:
                    if attempt >= self.max_retries:
                        raise
                    time.sleep(min(8.0, 0.5 * 2**attempt))
        return out


_LOCAL_MODELS: dict[tuple, Any] = {}  # (model, max_tokens) -> fastembed TextEmbedding (one per process)
_NOMIC_PREFIX = {"query": "search_query: ", "document": "search_document: "}


class LocalEmbeddings:
    """In-process fastembed embeddings (default ``nomic-ai/nomic-embed-text-v1.5-Q``, 768-d).

    Vectors are L2-normalised (cosine == dot; matches pgvector ``<=>`` and the SQLite numpy path).

    Memory (measured, -Q model, CPU EP, macOS arm64): ~285 MB RSS once loaded; a forward pass adds
    roughly with the longest sequence in the process (peak ≈ 290 / 420 / 580 / 810 MB at 128 / 512 /
    768 / 1000 tokens, batch 1). Queries (~20 tokens) stay at the loaded size. Corpus children are
    ~500 tokens (p99 ≈ 860), so ``max_tokens`` defaults to 1024 (nomic allows 8192); set
    ``LOCAL_EMBED_MAX_TOKENS=512`` to ingest on a 512 MB box (tails beyond 512 tokens are then only
    keyword-indexed). The ONNX CPU arena is disabled and inputs are padded to 128-token buckets so
    the number of distinct input shapes ONNX Runtime plans for stays small; documents are embedded
    in length-sorted batches capped at ``batch_tokens`` padded tokens.
    """

    PAD_MULTIPLE = 128

    def __init__(self, model: str | None = None, dim: int = 768, max_tokens: int | None = None,
                 batch_tokens: int | None = None, threads: int | None = None) -> None:
        try:
            import fastembed  # noqa: F401
        except ImportError as e:  # pragma: no cover - installed in this venv
            raise ProviderConfigError("EMBEDDING_PROVIDER=local but fastembed is not installed") from e
        self.model = model or _settings().embedding_model
        self.dim = dim
        self.name = f"local:{self.model}"
        self.max_tokens = max_tokens or int(os.environ.get("LOCAL_EMBED_MAX_TOKENS") or 1024)
        self.batch_tokens = batch_tokens or int(os.environ.get("LOCAL_EMBED_BATCH_TOKENS") or 512)
        self.threads = threads
        self._nomic = "nomic" in self.model.lower()

    def _ensure_model(self):
        key = (self.model, self.max_tokens)
        m = _LOCAL_MODELS.get(key)
        if m is None:
            from fastembed import TextEmbedding

            m = TextEmbedding(model_name=self.model, threads=self.threads, enable_cpu_mem_arena=False)
            tok = self._tokenizer(m)
            if tok is not None:
                tok.enable_truncation(max_length=self.max_tokens)
                # ONNX Runtime keeps memory per distinct input shape; padding to multiples of
                # PAD_MULTIPLE bounds the number of shapes (measured: 5.2 GB peak with exact
                # lengths at batch 1 vs a few hundred MB with buckets).
                pad = dict(tok.padding or {})
                if pad:
                    pad["pad_to_multiple_of"] = self.PAD_MULTIPLE
                    pad.pop("length", None)
                    tok.enable_padding(**pad)
            _LOCAL_MODELS[key] = m
        return m

    @staticmethod
    def _tokenizer(m):
        return getattr(getattr(m, "model", None), "tokenizer", None)

    def _batches(self, m, texts: list[str]) -> list[list[int]]:
        """Indices grouped so that len(batch) × longest_in_batch ≤ batch_tokens (≥ 1 per batch)."""
        tok = self._tokenizer(m)
        if tok is None or len(texts) == 1:
            return [[i] for i in range(len(texts))]
        pm = self.PAD_MULTIPLE
        lens = [-(-sum(e.attention_mask) // pm) * pm for e in tok.encode_batch(texts)]  # bucketed
        order = sorted(range(len(texts)), key=lambda i: lens[i])
        out: list[list[int]] = []
        cur: list[int] = []
        for i in order:
            if cur and (len(cur) + 1) * lens[i] > self.batch_tokens:
                out.append(cur)
                cur = []
            cur.append(i)
        if cur:
            out.append(cur)
        return out

    def embed(self, texts: list[str], input_type: str | None = "document") -> list[list[float]]:
        if not texts:
            return []
        kind = "query" if input_type == "query" else "document"
        prefix = _NOMIC_PREFIX[kind] if self._nomic else ""
        batch = [prefix + (t if t.strip() else " ") for t in texts]
        m = self._ensure_model()
        out: list[list[float] | None] = [None] * len(batch)
        for idx in self._batches(m, batch):
            for i, v in zip(idx, m.embed([batch[i] for i in idx], batch_size=len(idx))):
                n = math.sqrt(float((v * v).sum())) or 1.0
                out[i] = [float(x) / n for x in v]
        return out  # type: ignore[return-value]


def _stable_hash(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


_CONTENT_TAGS = {"NOUN", "PROPN", "VERB", "ADJ", "ADV", "NUM", "X"}


class FakeEmbeddings:
    """Deterministic hashed bag-of-words embedding for tests and offline demos.

    Features: tokens from the shared tokenizer (``app.ml.features.speech``;
    language auto-detected per text so Japanese works), weighted 1.0 for
    content words and 0.3 for function words, plus sub-word features (EN: a
    5-character prefix as a crude stem; JA: character bigrams of content
    morphemes) at 0.5 so inflections still overlap. Each feature is hashed
    into ``dim`` buckets with a hashed sign (signed feature hashing keeps
    unrelated texts near cosine 0), then L2-normalised. Meaningful only as
    lexical overlap: paraphrases sharing words score above unrelated text,
    but synonyms do not match.
    """

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self.name = f"fake-hash-bow:{dim}"

    def _features(self, text: str) -> list[tuple[str, float]]:
        from app.ml.features.speech import is_japanese, pos_tags

        lang = "ja" if is_japanese(text) else "en"
        feats: list[tuple[str, float]] = []
        for tok, pos in pos_tags(text, lang):
            content = pos in _CONTENT_TAGS
            feats.append(("w:" + tok, 1.0 if content else 0.3))
            if not content:
                continue
            if lang == "en" and len(tok) > 5:
                feats.append(("p:" + tok[:5], 0.5))
            elif lang == "ja" and len(tok) > 1:
                feats.extend(("b:" + tok[j : j + 2], 0.5) for j in range(len(tok) - 1))
        return feats

    def embed(self, texts: list[str], input_type: str | None = "document") -> list[list[float]]:
        out: list[list[float]] = []  # symmetric: input_type is ignored
        for text in texts:
            vec = [0.0] * self.dim
            for feat, w in self._features(text or ""):
                h = _stable_hash(feat)
                vec[h % self.dim] += w if (h >> 32) & 1 else -w
            norm = math.sqrt(sum(v * v for v in vec))
            out.append([v / norm for v in vec] if norm else vec)
        return out


def get_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    """Factory: ``name``, else ``EMBEDDING_PROVIDER`` env, else ``settings.embedding_provider``
    (which also reads ``.env``); ``fake`` default, ``local``, ``voyage``."""
    choice = (name or os.environ.get("EMBEDDING_PROVIDER") or _settings().embedding_provider or "fake").strip().lower()
    if choice == "fake":
        from app.db.types import EMBEDDING_DIM

        return FakeEmbeddings(dim=int(os.environ.get("EMBEDDING_DIM") or EMBEDDING_DIM))
    if choice == "local":
        return LocalEmbeddings(model=os.environ.get("EMBEDDING_MODEL") or None)
    if choice == "voyage":
        return VoyageEmbeddings(model=os.environ.get("VOYAGE_MODEL", "voyage-3"))
    raise ProviderConfigError(f"unknown EMBEDDING_PROVIDER={choice!r} (expected 'fake', 'local' or 'voyage')")
