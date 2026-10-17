"""RAG store, corpus ingestion and cited answering (fake providers only)."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import ResearchChunk, ResearchParent, ResearchSource
from app.db.types import EMBEDDING_DIM
from app.providers.embeddings import FakeEmbeddings
from app.providers.llm import FakeLlm
from app.rag import store
from app.rag.chunking import chunk_paragraphs, chunk_text
from app.rag.embeddings import embed
from app.rag.ingest import ingest_corpus, parse_html, parse_jats
from app.services import rag_service

FIXTURES = Path(__file__).parent / "fixtures" / "rag"
SOURCES = FIXTURES / "sources.json"
TEST_IDS = ["src_test_bp_jats", "src_test_memory_html", "src_test_notes"]


@pytest.fixture()
def emb():
    return FakeEmbeddings()


def _vec(*xs: float) -> list[float]:
    return list(xs) + [0.0] * (EMBEDDING_DIM - len(xs))


# ---------------------------------------------------------------- chunking

def test_chunk_text_unchanged():
    assert chunk_text("abcdefghij", size=4, overlap=1) == ["abcd", "defg", "ghij", "j"]


def test_chunk_paragraphs_prefers_boundaries():
    paras = ["a" * 200, "b" * 200, "c" * 200]
    chunks = chunk_paragraphs(paras, size=500, overlap=50)
    assert chunks == ["a" * 200 + "\n" + "b" * 200, "c" * 200]
    long = chunk_paragraphs(["word " * 300], size=500, overlap=50)
    assert all(len(c) <= 500 for c in long) and len(long) >= 3


# ---------------------------------------------------------------- store

def test_store_add_search_cosine_and_pathway(db):
    for sid, tags in (("src_store_a", ["cardio"]), ("src_store_b", ["cognitive"])):
        if not db.get(ResearchSource, sid):
            db.add(ResearchSource(id=sid, title=sid.upper(), year=2020, pathway_tags=tags))
    db.flush()
    store.delete_chunks(db, "src_store_a")
    store.delete_chunks(db, "src_store_b")
    store.add_chunks(db, "src_store_a", ["a-near", "a-far"], [_vec(1, 0.1), _vec(0, 1)], {"section": "s"})
    store.add_chunks(db, "src_store_b", ["b-mid"], [_vec(1, 0.5)])
    ids = ["src_store_a", "src_store_b"]

    hits = store.search(db, _vec(1, 0), k=3, source_ids=ids)
    assert [h.text for h in hits] == ["a-near", "b-mid", "a-far"]
    assert hits[0].score > hits[1].score > hits[2].score
    assert hits[0].source == {"id": "src_store_a", "title": "SRC_STORE_A", "year": 2020, "url": None}
    assert hits[0].meta == {"section": "s"}

    cardio = store.search(db, _vec(1, 0), k=5, pathway="cardio", source_ids=ids)
    assert {h.source_id for h in cardio} == {"src_store_a"}
    assert store.search(db, _vec(1, 0), pathway="nonexistent") == []
    db.rollback()


def test_in_memory_store():
    s = store.InMemoryStore()
    s.add_source("x", "X", pathway_tags=["cardio"])
    s.add_source("y", "Y", pathway_tags=["parkinsons"])
    s.add_chunks("x", ["x1"], [_vec(1, 0)])
    s.add_chunks("y", ["y1"], [_vec(0.9, 0.1)])
    assert [h.text for h in s.search(_vec(1, 0), k=2)] == ["x1", "y1"]
    assert [h.text for h in s.search(_vec(1, 0), pathway="parkinsons")] == ["y1"]


def test_embed_wrapper_handles_providers_without_input_type(emb):
    vecs = embed(["hello world", "blood pressure"], input_type="query", provider=emb)
    assert len(vecs) == 2 and len(vecs[0]) == EMBEDDING_DIM


# ---------------------------------------------------------------- loaders

def test_parse_jats_sections_tables_no_refs():
    title, sections = parse_jats((FIXTURES / "bp_article.xml").read_bytes())
    assert title == "Home blood pressure monitoring protocol (test article)"
    heads = [s.heading for s in sections]
    assert "Abstract > Background" in heads and "Introduction > Thresholds" in heads
    text = "\n".join(p for s in sections for p in s.paragraphs)
    assert "Normal | <115" in text and "Table 1 Home BP categories" in text
    assert "REFERENCE SHOULD BE DROPPED" not in text


def test_parse_html_drops_boilerplate_keeps_japanese():
    title, sections = parse_html((FIXTURES / "memory_page.html").read_bytes())
    assert title == "Story recall and memory"
    text = "\n".join(p for s in sections for p in s.paragraphs)
    assert "retells it immediately" in text and "物忘れが増えた" in text
    for junk in ("NAVIGATION", "FOOTER", "DROPPED_SCRIPT", "color:red"):
        assert junk not in text
    assert "物忘れについて" in [s.heading for s in sections]


# ---------------------------------------------------------------- ingestion

def test_ingest_fixture_corpus_counts_prefix_idempotent(db, emb):
    res = ingest_corpus(db, emb, SOURCES, force=True)
    assert res["sources"] == 3 and res["ingested"] == 3 and res["failed"] == 0
    per = {r["id"]: r for r in res["per_source"]}
    assert per["src_test_bp_jats"]["loader"] == "jats"
    assert per["src_test_memory_html"]["loader"] == "html"
    assert per["src_test_notes"]["loader"] == "notes" and per["src_test_notes"]["chunks"] == 1
    assert res["total_chunks"] == sum(store.chunk_count(db, i) for i in TEST_IDS)
    texts = db.scalars(select(ResearchChunk.text).where(ResearchChunk.source_id.in_(TEST_IDS))).all()
    assert texts and not any("物忘れ" in t for t in texts)  # English-only corpus: JA paragraphs dropped

    src = db.get(ResearchSource, "src_test_bp_jats")
    assert src.year == 2020 and src.pathway_tags == ["cardio"] and src.doi == "10.0000/test.1"
    chunks = db.scalars(select(ResearchChunk).where(ResearchChunk.source_id == "src_test_bp_jats")
                        .order_by(ResearchChunk.ordinal)).all()
    assert chunks[0].text.startswith("[Home blood pressure monitoring protocol (test article) (2020)] Section: Abstract")
    assert all(c.embedding and len(c.embedding) == EMBEDDING_DIM for c in chunks)
    assert {c.meta["loader"] for c in chunks} == {"jats"}
    assert all(c.parent_id for c in chunks) and {c.meta["embedder"] for c in chunks} == {emb.name}
    parents = db.scalars(select(ResearchParent).where(ResearchParent.source_id == "src_test_bp_jats")).all()
    assert len(parents) == per["src_test_bp_jats"]["parents"] >= 2
    assert {c.parent_id for c in chunks} == {p.id for p in parents}

    again = ingest_corpus(db, emb, SOURCES)
    assert again["skipped"] == 3 and again["ingested"] == 0
    assert again["total_chunks"] == res["total_chunks"]

    limited = ingest_corpus(db, emb, SOURCES, force=True, limit_chunks_per_source=1)
    assert limited["total_chunks"] == 3


def test_retrieve_ranks_relevant_source(db, emb):
    ingest_corpus(db, emb, SOURCES)
    hits = rag_service.retrieve(db, "morning home blood pressure 135/85 thresholds", k=3, pathway="cardio",
                                embedding_provider=emb)
    assert "src_test_bp_jats" in {h.source_id for h in hits}
    assert all("cardio" in (db.get(ResearchSource, h.source_id).pathway_tags or []) for h in hits)
    assert rag_service.infer_pathway("my blood pressure was high") == "cardio"
    assert rag_service.infer_pathway("I keep forgetting names and feel confused") == "cognitive"
    assert rag_service.infer_pathway("palpitations at night") == "cardio"  # word-prefix match
    assert rag_service.infer_pathway("hello") is None


# ---------------------------------------------------------------- cited answer

def test_answer_with_citations_numbering_and_sources(db, emb):
    ingest_corpus(db, emb, SOURCES)
    hits = store.search(db, embed(["story recall memory"], provider=emb)[0], k=2,
                        source_ids=["src_test_memory_html", "src_test_bp_jats"])
    llm = FakeLlm(canned_answer="Story recall is used in screening [1], see also [2] and [9].")
    out = rag_service.answer_with_citations(db, llm, "What is story recall?", hits, "Profile: age 68", "en")
    assert [s["n"] for s in out["sources"]] == [1, 2]
    assert out["sources"][0]["id"] == hits[0].source_id
    assert set(out["sources"][0]) >= {"id", "title", "year", "url", "snippet", "n"}
    assert not out["sources"][0]["snippet"].startswith("[")  # prefix stripped
    assert out["cited"] == [1, 2]  # [9] is out of range
    assert llm.calls == [{"method": "complete", "purpose": "chat_answer"}]


def test_prompt_contains_numbered_excerpts_and_rules(db, emb, monkeypatch):
    ingest_corpus(db, emb, SOURCES)
    hits = store.search(db, embed(["blood pressure"], provider=emb)[0], k=2, source_ids=TEST_IDS)
    seen = {}

    class Spy(FakeLlm):
        def complete(self, system, messages, **kw):
            seen["system"], seen["messages"] = system, messages
            return super().complete(system, messages, **kw)

    rag_service.answer_with_citations(db, Spy(), "質問です", hits, "ctx-block", "ja",
                                      history=[{"role": "assistant", "content": "x"}, {"role": "user", "content": "q0"},
                                               {"role": "assistant", "content": "a0"}])
    user_turn = seen["messages"][-1]["content"]
    assert "[1] " in user_turn and "[2] " in user_turn and "ctx-block" in user_turn and "質問です" in user_turn
    assert [m["role"] for m in seen["messages"]] == ["user", "assistant", "user"]
    assert "Never diagnose" in seen["system"] and "[n]" in seen["system"] and "Japanese" in seen["system"]
    assert "これは診断ではありません" in seen["system"]
