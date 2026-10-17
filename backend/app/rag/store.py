"""Research corpus store: parents + embedded child chunks, vector and lexical search.

Tables (``app.db.models``): ``ResearchSource`` 1─* ``ResearchParent`` (a full section, what the LLM
reads) 1─* ``ResearchChunk`` (≈380-word child, embedded + keyword-indexed, what retrieval matches).

Search sides (each returns child-level :class:`Hit` lists for RRF, see ``app.rag.retrieval``):
- :func:`vector_search` — Postgres: pgvector ``<=>`` (cosine distance) ordered in SQL.
  SQLite: cosine over an in-process float32 matrix of all chunk embeddings.
- :func:`lexical_search` — Postgres: english full-text search + ``ts_rank`` (GIN index).
  SQLite: in-process BM25.

The SQLite in-process index (:func:`corpus_index`) is built lazily and rebuilt whenever the corpus
signature (chunk count, min/max chunk id, ``settings.rag_corpus_version``) changes, so ingest in the
same process (tests, the admin ingest route) is picked up without restarts.

Pathway filtering uses ``ResearchSource.pathway_tags``; the source table is small, so matching
source ids are resolved in Python and applied as ``source_id IN (...)`` on both dialects.
``InMemoryStore`` keeps the vector ``search`` semantics without a DB (unit tests, scripts).
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import numpy as np
from sqlalchemy import Float, bindparam, cast, delete, func, select, text
from sqlalchemy.orm import Session

from app.db.models import ResearchChunk, ResearchParent, ResearchSource
from app.db.types import EMBEDDING_DIM, is_postgres
from app.rag import lexical

__all__ = ["Hit", "add_parent", "add_chunks", "search", "vector_search", "lexical_search", "delete_chunks",
           "chunk_count", "corpus_signature", "corpus_index", "InMemoryStore"]


@dataclass
class Hit:
    chunk_id: str
    source_id: str
    text: str
    score: float  # higher = better (cosine for vector, bm25/ts_rank for lexical, rrf/rerank later)
    meta: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)  # {id, title, year, url}
    parent_id: str | None = None
    snippet: str | None = None  # set on expanded parent hits: text of the best-matching child

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source_ref(s: ResearchSource | None, source_id: str) -> dict[str, Any]:
    if s is None:
        return {"id": source_id, "title": source_id, "year": None, "url": None}
    return {"id": s.id, "title": s.title, "year": s.year, "url": s.url}


# ================================================================ writes

def add_parent(db: Session, source_id: str, ordinal: int, section: str | None, text_: str) -> ResearchParent:
    row = ResearchParent(source_id=source_id, ordinal=ordinal, section=(section or "")[:500] or None, text=text_)
    db.add(row)
    db.flush()
    return row


def add_chunks(db: Session, source_id: str, chunks: list[str], embeddings: Sequence[Sequence[float]],
               meta: dict[str, Any] | list[dict[str, Any]] | None = None, start_ordinal: int = 0,
               parent_ids: Sequence[str | None] | None = None) -> list[ResearchChunk]:
    """Insert chunks for an existing ``ResearchSource``. ``meta`` is shared (dict) or per chunk (list);
    ``parent_ids`` aligns with ``chunks``. Flushes; the caller commits."""
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings length mismatch")
    if parent_ids is not None and len(parent_ids) != len(chunks):
        raise ValueError("chunks and parent_ids length mismatch")
    rows = []
    for i, (txt, vec) in enumerate(zip(chunks, embeddings)):
        m = meta[i] if isinstance(meta, list) else dict(meta or {})
        rows.append(ResearchChunk(source_id=source_id, ordinal=start_ordinal + i, text=txt,
                                  embedding=[float(x) for x in vec], meta=m,
                                  parent_id=parent_ids[i] if parent_ids is not None else None))
    db.add_all(rows)
    db.flush()
    return rows


def delete_chunks(db: Session, source_id: str) -> int:
    """Delete every chunk AND parent of ``source_id``; returns the number of chunks removed."""
    res = db.execute(delete(ResearchChunk).where(ResearchChunk.source_id == source_id))
    db.execute(delete(ResearchParent).where(ResearchParent.source_id == source_id))
    return res.rowcount or 0


def chunk_count(db: Session, source_id: str) -> int:
    return db.scalar(select(func.count(ResearchChunk.id)).where(ResearchChunk.source_id == source_id)) or 0


def parent_count(db: Session, source_id: str) -> int:
    return db.scalar(select(func.count(ResearchParent.id)).where(ResearchParent.source_id == source_id)) or 0


# ================================================================ helpers

def _filter_source_ids(db: Session, pathway: str | None, source_ids: Sequence[str] | None) -> list[str] | None:
    """None = no filter; [] = filter matches nothing."""
    if pathway is None and source_ids is None:
        return None
    rows = db.execute(select(ResearchSource.id, ResearchSource.pathway_tags)).all()
    ids = [r.id for r in rows if pathway is None or pathway in (r.pathway_tags or [])]
    if source_ids is not None:
        allowed = set(source_ids)
        ids = [i for i in ids if i in allowed]
    return ids


def _cosine_rows(query: np.ndarray, mat: np.ndarray) -> np.ndarray:
    qn = np.linalg.norm(query)
    mn = np.linalg.norm(mat, axis=1)
    denom = mn * qn
    denom[denom == 0] = np.inf
    return (mat @ query) / denom


def _hydrate(db: Session, scored: list[tuple[str, float]]) -> list[Hit]:
    """(chunk_id, score) in rank order → Hits with source refs."""
    if not scored:
        return []
    by_id = {c.id: c for c in db.scalars(select(ResearchChunk).where(ResearchChunk.id.in_([i for i, _ in scored])))}
    sources = {s.id: s for s in db.scalars(select(ResearchSource).where(
        ResearchSource.id.in_({c.source_id for c in by_id.values()})))}
    out = []
    for cid, s in scored:
        c = by_id.get(cid)
        if c is None:
            continue
        out.append(Hit(chunk_id=c.id, source_id=c.source_id, text=c.text, score=round(float(s), 6),
                       meta=dict(c.meta or {}), source=_source_ref(sources.get(c.source_id), c.source_id),
                       parent_id=c.parent_id))
    return out


def corpus_signature(db: Session) -> tuple:
    """Cheap fingerprint of the chunk table; changes on any (re-)ingest."""
    from app.core.config import settings

    n, lo, hi = db.execute(select(func.count(ResearchChunk.id), func.min(ResearchChunk.id),
                                  func.max(ResearchChunk.id))).one()
    return (str(db.get_bind().url), int(n or 0), lo, hi, settings.rag_corpus_version)


# ================================================================ SQLite in-process index

@dataclass
class CorpusIndex:
    signature: tuple
    ids: list[str]
    source_ids: np.ndarray        # object array aligned with ids
    matrix: np.ndarray | None     # float32 (n, dim), rows L2-normalised; None if no embeddings
    has_vec: np.ndarray           # bool mask: row has an embedding of the matrix dim
    bm25: Any                     # rank_bm25.BM25Okapi | None


_INDEX: dict[str, CorpusIndex] = {}
_INDEX_LOCK = threading.Lock()


def _build_index(db: Session, sig: tuple) -> CorpusIndex:
    """Stream chunk rows into a float32 matrix + BM25 corpus (never holds every JSON vector at once)."""
    from rank_bm25 import BM25Okapi

    n = int(sig[1])
    dim = EMBEDDING_DIM
    first = db.scalar(select(ResearchChunk.embedding).where(ResearchChunk.embedding.is_not(None)).limit(1))
    if first:
        dim = len(first)
    ids: list[str] = []
    srcs: list[str] = []
    corpus: list[list[str]] = []
    matrix = np.zeros((n, dim), dtype=np.float32) if first else None
    has_vec = np.zeros(n, dtype=bool)
    stmt = select(ResearchChunk.id, ResearchChunk.source_id, ResearchChunk.text,
                  ResearchChunk.embedding).order_by(ResearchChunk.id).execution_options(yield_per=256)
    for i, r in enumerate(db.execute(stmt)):
        if i >= n:  # rows added between the signature and this scan: next call rebuilds
            break
        ids.append(r.id)
        srcs.append(r.source_id)
        corpus.append(lexical.tokenize(r.text))
        if matrix is not None and r.embedding and len(r.embedding) == dim:
            matrix[i] = r.embedding
            has_vec[i] = True
    m = len(ids)
    if matrix is not None:
        matrix = matrix[:m]
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix /= norms
    bm25 = BM25Okapi(corpus) if m and any(corpus) else None
    return CorpusIndex(sig, ids, np.array(srcs, dtype=object), matrix, has_vec[:m], bm25)


def corpus_index(db: Session) -> CorpusIndex:
    sig = corpus_signature(db)
    key = sig[0]
    idx = _INDEX.get(key)
    if idx is not None and idx.signature == sig:
        return idx
    with _INDEX_LOCK:
        idx = _INDEX.get(key)
        if idx is None or idx.signature != sig:
            idx = _build_index(db, sig)
            _INDEX[key] = idx
    return idx


def _mask(idx: CorpusIndex, ids: list[str] | None) -> np.ndarray:
    if ids is None:
        return np.ones(len(idx.ids), dtype=bool)
    return np.isin(idx.source_ids, np.array(ids, dtype=object))


# ================================================================ search sides

def vector_search(db: Session, query_embedding: Sequence[float], k: int = 20, pathway: str | None = None,
                  source_ids: Sequence[str] | None = None) -> list[Hit]:
    """Top-``k`` chunks by cosine similarity, optionally restricted to a pathway / source ids."""
    ids = _filter_source_ids(db, pathway, source_ids)
    if ids is not None and not ids:
        return []
    q = [float(x) for x in query_embedding]
    if is_postgres(db.get_bind()):
        from pgvector.sqlalchemy import Vector

        vtype = Vector(len(q) or EMBEDDING_DIM)
        dist = ResearchChunk.embedding.op("<=>", return_type=Float)(cast(bindparam("qvec", q, type_=vtype), vtype))
        stmt = select(ResearchChunk.id, dist.label("dist")).where(ResearchChunk.embedding.is_not(None))
        if ids is not None:
            stmt = stmt.where(ResearchChunk.source_id.in_(ids))
        rows = db.execute(stmt.order_by(dist).limit(k)).all()
        return _hydrate(db, [(r[0], 1.0 - float(r[1])) for r in rows])

    idx = corpus_index(db)
    if idx.matrix is None or idx.matrix.shape[1] != len(q):
        return []
    qv = np.asarray(q, dtype=np.float32)
    qn = float(np.linalg.norm(qv))
    if qn == 0:
        return []
    sims = idx.matrix @ (qv / qn)
    valid = _mask(idx, ids) & idx.has_vec
    sims = np.where(valid, sims, -np.inf)
    n_valid = int(valid.sum())
    top = np.argsort(-sims, kind="stable")[: min(k, n_valid)]
    return _hydrate(db, [(idx.ids[i], float(sims[i])) for i in top])


search = vector_search  # backwards-compatible name (vector-only search)


def lexical_search(db: Session, query: str, k: int = 20, pathway: str | None = None,
                   source_ids: Sequence[str] | None = None) -> list[Hit]:
    """Top-``k`` chunks by keyword relevance (FTS on Postgres, BM25 on SQLite). Only chunks that
    share at least one (non-stopword) term with the query are returned."""
    ids = _filter_source_ids(db, pathway, source_ids)
    if ids is not None and not ids:
        return []
    if not (query or "").strip():
        return []
    if is_postgres(db.get_bind()):
        params: dict[str, Any] = {"query": query, "limit": k}
        sql = lexical.PG_LEXICAL_SQL.format(source_filter="AND c.source_id IN :ids" if ids is not None else "")
        stmt = text(sql)
        if ids is not None:
            stmt = stmt.bindparams(bindparam("ids", expanding=True))
            params["ids"] = ids
        rows = db.execute(stmt, params).all()
        return _hydrate(db, [(r.id, float(r.rank)) for r in rows])

    toks = lexical.tokenize(query)
    idx = corpus_index(db)
    if not toks or idx.bm25 is None:
        return []
    scores = np.asarray(idx.bm25.get_scores(toks), dtype=np.float64)
    # A "match" = shares a query term (BM25 can score 0 for common terms; mirror ``@@`` semantics).
    qset = set(toks)
    matched = np.fromiter((not qset.isdisjoint(f) for f in idx.bm25.doc_freqs), dtype=bool, count=len(idx.ids))
    valid = _mask(idx, ids) & matched
    scores = np.where(valid, scores, -np.inf)
    top = np.argsort(-scores, kind="stable")[: min(k, int(valid.sum()))]
    return _hydrate(db, [(idx.ids[i], float(scores[i])) for i in top])


# ================================================================ in-memory (no DB)

class InMemoryStore:
    """DB-free store with the same vector ranking (cosine) and pathway filter, for unit tests."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self.sources: dict[str, dict[str, Any]] = {}

    def add_source(self, source_id: str, title: str = "", year: int | None = None, url: str | None = None,
                   pathway_tags: list[str] | None = None) -> None:
        self.sources[source_id] = {"id": source_id, "title": title or source_id, "year": year, "url": url,
                                   "pathway_tags": list(pathway_tags or [])}

    def add_chunks(self, source_id: str, chunks: list[str], embeddings: Sequence[Sequence[float]],
                   meta: dict[str, Any] | None = None) -> int:
        self.sources.setdefault(source_id, {"id": source_id, "title": source_id, "year": None, "url": None,
                                            "pathway_tags": []})
        for i, (t, v) in enumerate(zip(chunks, embeddings)):
            self._rows.append({"id": f"{source_id}:{len(self._rows)}", "source_id": source_id, "text": t,
                               "vec": np.asarray(v, dtype=np.float64), "meta": dict(meta or {}, ordinal=i)})
        return len(chunks)

    def search(self, query_embedding: Sequence[float], k: int = 6, pathway: str | None = None,
               source_ids: Sequence[str] | None = None) -> list[Hit]:
        rows = [r for r in self._rows
                if (pathway is None or pathway in self.sources[r["source_id"]]["pathway_tags"])
                and (source_ids is None or r["source_id"] in source_ids)]
        if not rows:
            return []
        sims = _cosine_rows(np.asarray(query_embedding, dtype=np.float64), np.stack([r["vec"] for r in rows]))
        out = []
        for i in np.argsort(-sims)[:k]:
            r = rows[i]
            s = {kk: vv for kk, vv in self.sources[r["source_id"]].items() if kk != "pathway_tags"}
            out.append(Hit(chunk_id=r["id"], source_id=r["source_id"], text=r["text"], score=float(sims[i]),
                           meta=r["meta"], source=s))
        return out
