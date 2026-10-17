"""Reciprocal Rank Fusion — merge the vector and lexical rankings into one list.

RRF score of a chunk = Σ_lists 1 / (k + rank), rank 1-based. It uses only ranks (cosine and
BM25/ts_rank scores are not comparable), which is why hybrid retrieval uses it; ``k`` damps the
weight of low ranks (60 is the canonical default, Cormack et al. 2009). Ties keep the order of
first appearance (vector list first), so results are deterministic.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from app.rag.store import Hit

RRF_K = 60


def reciprocal_rank_fusion(ranked_lists: Sequence[Sequence[Hit]], k: int = RRF_K,
                           top_n: int | None = None) -> list[Hit]:
    scores: dict[str, float] = {}
    by_id: dict[str, Hit] = {}
    order: dict[str, int] = {}
    for lst in ranked_lists:
        seen: set[str] = set()
        for rank, hit in enumerate(lst, start=1):
            if hit.chunk_id in seen:  # a list should not vote twice for one chunk
                continue
            seen.add(hit.chunk_id)
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            if hit.chunk_id not in by_id:
                by_id[hit.chunk_id] = hit
                order[hit.chunk_id] = len(order)
    fused = sorted(by_id, key=lambda cid: (-scores[cid], order[cid]))
    out = [replace(by_id[cid], score=round(scores[cid], 8)) for cid in fused]
    return out[:top_n] if top_n is not None else out
