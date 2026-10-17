"""Reciprocal rank fusion and the reranker selection."""

from app.providers.embeddings import FakeEmbeddings
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.rerank import CrossEncoderReranker, IdentityReranker, get_reranker
from app.rag.store import Hit


def _h(cid: str, score: float = 0.0) -> Hit:
    return Hit(chunk_id=cid, source_id="s", text=cid, score=score)


def test_rrf_scores_and_order():
    vec = [_h("a", 0.9), _h("b", 0.8), _h("c", 0.7)]
    lex = [_h("c", 12.0), _h("d", 5.0), _h("a", 1.0)]
    fused = reciprocal_rank_fusion([vec, lex], k=60)
    ids = [h.chunk_id for h in fused]
    # a: 1/61 + 1/63; c: 1/63 + 1/61 → tie, broken by first appearance (a, from the vector list).
    assert ids == ["a", "c", "b", "d"]
    assert abs(fused[0].score - (1 / 61 + 1 / 63)) < 1e-7
    assert abs(fused[2].score - 1 / 62) < 1e-7
    assert vec[0].score == 0.9  # inputs are not mutated


def test_rrf_both_lists_beat_single_list_top():
    fused = reciprocal_rank_fusion([[_h("x"), _h("both")], [_h("y"), _h("both")]], k=60)
    assert fused[0].chunk_id == "both"
    assert [h.chunk_id for h in reciprocal_rank_fusion([[_h("x")], []], top_n=1)] == ["x"]
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_ignores_duplicates_within_one_list():
    fused = reciprocal_rank_fusion([[_h("a"), _h("a"), _h("b")]], k=60)
    assert [h.chunk_id for h in fused] == ["a", "b"] and abs(fused[0].score - 1 / 61) < 1e-7


def test_reranker_selection_never_loads_models_for_fake_embeddings():
    assert isinstance(get_reranker(FakeEmbeddings()), IdentityReranker)
    assert isinstance(get_reranker(None, name="identity"), IdentityReranker)
    ce = get_reranker(None, name="cross_encoder")
    assert isinstance(ce, CrossEncoderReranker) and ce.model  # constructed lazily, nothing loaded yet
    hits = [_h("a"), _h("b"), _h("c")]
    assert [h.chunk_id for h in IdentityReranker().rerank("q", hits, 2)] == ["a", "b"]
