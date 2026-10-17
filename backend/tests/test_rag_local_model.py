"""Optional: real local models (fastembed). Skipped unless the weights are already cached —
tests never download models."""

import math

import pytest

MODEL = "nomic-ai/nomic-embed-text-v1.5-Q"
RERANKER = "Xenova/ms-marco-MiniLM-L-6-v2"


def _cached(cls, name: str) -> bool:
    try:
        cls(model_name=name, local_files_only=True)
        return True
    except Exception:  # not cached / fastembed missing / offline
        return False


@pytest.fixture(scope="module")
def local():
    fastembed = pytest.importorskip("fastembed")
    if not _cached(fastembed.TextEmbedding, MODEL):
        pytest.skip(f"{MODEL} not in the fastembed cache")
    from app.providers.embeddings import LocalEmbeddings

    return LocalEmbeddings(MODEL)


def test_local_embeddings_semantics_and_prefixes(local):
    from app.providers.embeddings import cosine, embed_query

    q = embed_query(local, "home blood pressure threshold for hypertension")
    docs = local.embed(["Home BP of 135/85 mmHg or higher is diagnosed as hypertension.",
                        "Sustained vowel phonation features separate Parkinson disease from controls."])
    assert len(q) == 768 and abs(math.sqrt(sum(x * x for x in q)) - 1) < 1e-5
    assert cosine(q, docs[0]) > cosine(q, docs[1]) + 0.1
    assert local.embed(["x"], input_type="query") != local.embed(["x"], input_type="document")  # task prefix


def test_cross_encoder_orders_relevant_first(local):
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    if not _cached(TextCrossEncoder, RERANKER):
        pytest.skip(f"{RERANKER} not in the fastembed cache")
    from app.rag.rerank import CrossEncoderReranker
    from app.rag.store import Hit

    hits = [Hit(chunk_id="pd", source_id="s", text="Jitter and shimmer in Parkinson voice.", score=0),
            Hit(chunk_id="bp", source_id="s", text="Home blood pressure 135/85 defines hypertension.", score=0)]
    out = CrossEncoderReranker(RERANKER).rerank("home blood pressure hypertension threshold", hits, 2)
    assert out[0].chunk_id == "bp"
