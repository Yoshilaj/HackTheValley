"""Hybrid retrieval on a tiny isolated SQLite corpus (FakeEmbeddings + IdentityReranker; no models)."""

import pytest
from sqlalchemy.orm import sessionmaker

from app.db.models import ResearchChunk, ResearchSource
from app.db.session import init_db, make_engine
from app.providers.embeddings import FakeEmbeddings
from app.rag import lexical, retrieval, store
from app.rag.expand import expand_to_parents
from app.rag.ingest import Section, _write_parents, build_parents
from app.rag.rerank import IdentityReranker
from app.services import rag_service

EMB = FakeEmbeddings()
IDENT = IdentityReranker()

DOCS = {
    "src_bp": ("Home blood pressure guideline", 2019, ["cardio"], [
        Section("Thresholds", ["Home blood pressure of 135/85 mmHg or higher defines hypertension. "
                               "Measure in the morning within one hour of waking, after urination, seated."]),
        Section("Devices", ["Use a validated upper-arm cuff device. Wrist devices are less accurate."]),
    ]),
    "src_af": ("Smartwatch irregular rhythm notification", 2022, ["cardio"], [
        Section("Results", ["The irregular rhythm notification feature identified atrial fibrillation with "
                            "a positive predictive value of 0.84 in the Apple Heart Study."]),
    ]),
    "src_recall": ("Story recall speech biomarkers", 2024, ["cognitive"], [
        Section("Abstract", ["Automated story recall scoring from speech correlated with amyloid status. "
                             "Semantic similarity of the retelling predicted memory decline."]),
        Section("Methods", ["Participants retold a short story immediately and after a delay."]),
    ]),
    "src_pd": ("Voice analysis in Parkinson disease", 2021, ["parkinsons"], [
        Section("Review", ["Sustained vowel phonation features such as jitter and shimmer separate "
                           "Parkinson disease from controls."]),
    ]),
}


@pytest.fixture()
def tdb(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'rag.db'}")
    init_db(eng)
    session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)()
    for sid, (title, year, tags, sections) in DOCS.items():
        session.add(ResearchSource(id=sid, title=title, year=year, pathway_tags=tags, url=f"https://x/{sid}"))
        session.flush()
        _write_parents(session, sid, build_parents(title, year, sections), EMB, {"loader": "test"})
    session.commit()
    retrieval.clear_cache()
    yield session
    session.close()
    eng.dispose()


def test_tokenizer_stems_and_drops_stopwords():
    toks = lexical.tokenize("The readings of blood pressures were measured")
    assert toks == lexical.tokenize("reading blood pressure measure") and "the" not in toks and len(toks) == 4
    assert lexical.stem("measuring") == lexical.stem("measure")
    assert lexical.stem("class") == "class" and lexical.stem("studies") == "study"


def test_bm25_lexical_search_matches_terms_and_filters(tdb):
    hits = store.lexical_search(tdb, "jitter shimmer vowel", k=5)
    assert hits and hits[0].source_id == "src_pd"
    assert all("jitter" in h.text or "vowel" in h.text or "shimmer" in h.text for h in hits)
    assert store.lexical_search(tdb, "the of and", k=5) == []  # stopwords only
    assert store.lexical_search(tdb, "xylophone", k=5) == []   # no shared term → no match
    cardio = store.lexical_search(tdb, "blood pressure amyloid", k=10, pathway="cardio")
    assert cardio and {h.source_id for h in cardio} <= {"src_bp", "src_af"}


def test_bm25_index_rebuilds_when_corpus_changes(tdb):
    before = store.corpus_index(tdb)
    assert store.lexical_search(tdb, "zebrafish", k=3) == []
    tdb.add(ResearchSource(id="src_new", title="Zebrafish", year=2020, pathway_tags=[]))
    tdb.flush()
    _write_parents(tdb, "src_new", build_parents("Zebrafish", 2020, [Section("S", ["Zebrafish model."])]), EMB, {})
    tdb.commit()
    assert store.corpus_index(tdb) is not before
    assert store.lexical_search(tdb, "zebrafish", k=3)[0].source_id == "src_new"


def test_vector_search_dense_index(tdb):
    hits = store.vector_search(tdb, EMB.embed(["atrial fibrillation smartwatch notification"])[0], k=3)
    assert hits[0].source_id == "src_af" and hits[0].parent_id
    assert hits[0].score >= hits[-1].score
    assert store.vector_search(tdb, EMB.embed(["x"])[0], pathway="nonexistent") == []


def test_expansion_dedupes_children_by_parent(tdb):
    chunks = tdb.query(ResearchChunk).filter_by(source_id="src_bp").all()
    hits = store._hydrate(tdb, [(c.id, 1.0 - i * 0.1) for i, c in enumerate(chunks)] * 1)
    dup = hits + [store.Hit(**{**hits[0].as_dict(), "chunk_id": "fake", "score": 0.05})]
    parents = expand_to_parents(tdb, dup)
    assert len(parents) == len({c.parent_id for c in chunks})
    first = parents[0]
    assert first.score == hits[0].score and first.snippet and not first.snippet.startswith("[")
    assert first.text.startswith("[Home blood pressure guideline (2019)] Section: Thresholds\n")
    assert first.meta["section"] == "Thresholds" and first.meta["n_children_matched"] == 2


def test_hybrid_retrieve_ranks_and_returns_parents(tdb):
    hits = retrieval.hybrid_retrieve(tdb, "home blood pressure threshold for hypertension", k=3,
                                     embedding_provider=EMB, reranker=IDENT)
    assert hits[0].source_id == "src_bp" and "135/85" in hits[0].text
    assert len({h.parent_id for h in hits}) == len(hits) <= 3
    assert [h.meta["n"] for h in hits] == list(range(1, len(hits) + 1))
    t = retrieval.trace_dict()
    assert t["counts"]["vector"] > 0 and t["counts"]["lexical"] > 0 and not t["cache_hit"]

    again = retrieval.hybrid_retrieve(tdb, "  Home blood PRESSURE threshold for hypertension ", k=3,
                                      embedding_provider=EMB, reranker=IDENT)
    assert retrieval.last_trace.cache_hit and [h.chunk_id for h in again] == [h.chunk_id for h in hits]


def test_hybrid_retrieve_source_diversity(tdb):
    q = "home blood pressure cuff device threshold story recall"
    uncapped = retrieval.hybrid_retrieve(tdb, q, k=4, embedding_provider=EMB, reranker=IDENT, use_cache=False,
                                         max_per_source=None)
    capped = retrieval.hybrid_retrieve(tdb, q, k=4, embedding_provider=EMB, reranker=IDENT, use_cache=False,
                                       max_per_source=1)
    srcs = [h.source_id for h in capped]
    assert len(srcs) == len(set(srcs)) and len(capped) == min(4, len(DOCS))
    assert [h.meta["n"] for h in capped] == list(range(1, len(capped) + 1))
    # with the cap off, the same query packs two sections of one source
    assert max([h.source_id for h in uncapped].count(s) for s in DOCS) == 2
    default = retrieval.hybrid_retrieve(tdb, q, k=6, embedding_provider=EMB, reranker=IDENT, use_cache=False)
    assert max([h.source_id for h in default].count(s) for s in DOCS) <= retrieval.MAX_PER_SOURCE


def test_hybrid_lexical_rescues_exact_term(tdb):
    # FakeEmbeddings has no semantics; a rare exact term must still surface via BM25.
    hits = retrieval.hybrid_retrieve(tdb, "amyloid", k=2, embedding_provider=EMB, reranker=IDENT, use_cache=False)
    assert hits[0].source_id == "src_recall"


def test_pathway_filter_and_fallback(tdb):
    hits = retrieval.hybrid_retrieve(tdb, "story recall speech amyloid", k=4, pathway="cognitive",
                                     embedding_provider=EMB, reranker=IDENT, use_cache=False)
    assert {h.source_id for h in hits} == {"src_recall"}
    tdb.query(ResearchSource).filter_by(id="src_pd").update({"pathway_tags": ["rare"]})
    tdb.query(ResearchChunk).filter(ResearchChunk.source_id != "src_pd").delete()
    tdb.commit()
    hits = retrieval.hybrid_retrieve(tdb, "vowel jitter", k=2, pathway="cardio", embedding_provider=EMB,
                                     reranker=IDENT, use_cache=False)
    assert retrieval.last_trace.pathway_fallback and hits[0].source_id == "src_pd"


def test_rag_service_retrieve_and_sources_shape(tdb):
    hits = rag_service.retrieve(tdb, "smartwatch irregular rhythm notification atrial fibrillation", k=6,
                                pathway="cardio", embedding_provider=EMB)
    assert hits[0].source_id == "src_af"
    src = rag_service.sources_for(hits)
    assert [s["n"] for s in src] == list(range(1, len(hits) + 1))
    assert set(src[0]) >= {"id", "title", "year", "url", "snippet", "n", "chunk_id", "score", "section"}
    assert src[0]["url"] == "https://x/src_af" and not src[0]["snippet"].startswith("[")
