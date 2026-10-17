"""Retrieval quality gate on the REAL corpus (tests/fixtures/rag_eval.json via scripts.eval_rag).

Skipped unless the local models are already cached (tests never download) and backend/data/dev.db
holds a corpus embedded with the local embedder (``python -m scripts.ingest_corpus --provider local``).
Thresholds sit a little under the measured numbers (2026-10-17: hit@k 1.0, hit@1 0.857, term recall
1.0, abstain 0.833 with the cross-encoder) so a regression fails while small ties do not.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import ResearchChunk
from app.db.session import make_engine

DEV_DB = Path(__file__).resolve().parents[1] / "data" / "dev.db"
EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5-Q"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"


def _cached(cls, name: str) -> bool:
    try:
        cls(model_name=name, local_files_only=True)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def corpus():
    fastembed = pytest.importorskip("fastembed")
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    if not (_cached(fastembed.TextEmbedding, EMBED_MODEL) and _cached(TextCrossEncoder, RERANK_MODEL)):
        pytest.skip("local embedder / reranker not in the fastembed cache")
    if not DEV_DB.exists():
        pytest.skip("no dev corpus (backend/data/dev.db)")
    eng = make_engine(f"sqlite:///{DEV_DB}")
    db = sessionmaker(bind=eng)()
    n = db.scalar(select(func.count(ResearchChunk.id)).where(
        func.json_extract(ResearchChunk.meta, "$.embedder") == f"local:{EMBED_MODEL}"))
    if not n:
        db.close()
        eng.dispose()
        pytest.skip("dev corpus not embedded with the local model")
    from app.providers.embeddings import LocalEmbeddings
    from app.rag import retrieval

    retrieval.clear_cache()
    yield db, LocalEmbeddings(EMBED_MODEL)
    db.close()
    eng.dispose()


def _run(corpus, reranker_name: str, floor: float | None):
    from app.rag.rerank import get_reranker
    from scripts.eval_rag import evaluate, load_cases

    db, emb = corpus
    return evaluate(db, load_cases(), emb, get_reranker(emb, name=reranker_name), k=6, min_score=floor)


def test_cross_encoder_quality_gate(corpus):
    from app.core.config import settings

    rep = _run(corpus, "cross_encoder", settings.rag_min_rerank_score)
    s = rep["summary"]
    misses = [c["id"] for c in rep["cases"] if not c["expect_empty"] and not c["hit"]]
    leaks = [c["id"] for c in rep["cases"] if c["expect_empty"] and c["n"]]
    assert s["hit_at_k"] >= 0.95, misses
    assert s["hit_at_1"] >= 0.8 and s["mrr"] >= 0.85
    assert s["term_recall"] >= 0.9
    assert s["empty_on_answerable"] == 0  # the floor never silences an answerable question
    assert s["abstain_rate"] >= 0.8, leaks
    assert s["mean_passages"] >= 4


def test_identity_reranker_still_finds_the_source(corpus):
    s = _run(corpus, "identity", None)["summary"]
    assert s["hit_at_k"] >= 0.95 and s["hit_at_1"] >= 0.8
