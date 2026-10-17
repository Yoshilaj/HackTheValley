"""Corpus hygiene, pruning and the relevance floor (no models: FakeEmbeddings + stub rerankers)."""

from dataclasses import replace

import pytest
from sqlalchemy.orm import sessionmaker

from app.db.models import ResearchSource
from app.db.session import init_db, make_engine
from app.providers.embeddings import FakeEmbeddings
from app.rag import retrieval, store
from app.rag.ingest import Section, _write_parents, build_parents, is_skipped_heading, prune_sources
from app.rag.rerank import IdentityReranker

EMB = FakeEmbeddings()


class StubCrossEncoder:
    """Calibrated reranker whose score is looked up by a word in the passage."""

    name = "stub-cross-encoder"
    calibrated = True

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def rerank(self, query, hits, top_n):
        def score(h):
            return max((v for w, v in self.scores.items() if w in h.text.lower()), default=-10.0)

        return sorted((replace(h, score=score(h)) for h in hits), key=lambda h: -h.score)[:top_n]


@pytest.fixture()
def tdb(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'q.db'}")
    init_db(eng)
    session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)()
    docs = {
        "src_bp": ("Home BP", ["cardio"], "Home blood pressure of 135/85 mmHg defines hypertension."),
        "src_mem": ("Memory", ["cognitive"], "Story recall measures episodic memory and blood flow."),
    }
    for sid, (title, tags, text) in docs.items():
        session.add(ResearchSource(id=sid, title=title, year=2020, pathway_tags=tags, kind="guideline"))
        session.flush()
        _write_parents(session, sid, build_parents(title, 2020, [Section("Body", [text])]), EMB, {})
    session.commit()
    retrieval.clear_cache()
    yield session
    session.close()
    eng.dispose()


@pytest.mark.parametrize("heading,skip", [
    ("References", True), ("12. Acknowledgement", True), ("ACKNOWLEDGMENTS", True), ("Bibliography", True),
    ("Funding Sources", True), ("Disclosures", True), ("Conflicts of Interest", True),
    ("Body > References > Web", True), ("Supplementary Material", True),
    ("Results", False), ("4.2 Investigations", False), ("Reference ranges for home BP", False),
    ("CONSENT STATEMENT", True), ("Associated Data > Data Availability Statement", True),
    ("Contributor Information", True), ("Supporting information", True),
    ("Referral criteria", False), ("p. 6", False), ("Data analysis", False),
])
def test_skipped_headings(heading, skip):
    assert is_skipped_heading(heading) is skip


def test_build_parents_drops_back_matter_and_japanese():
    drafts = build_parents("T", 2020, [
        Section("Results", ["Home BP fell by 5 mmHg.", "家庭血圧は朝と夜に測定します。"]),
        Section("References", ["1. Smith J. Hypertension. Can J Cardiol 2020;36:596-624."]),
        Section("Acknowledgements", ["We thank the participants."]),
        Section("Abstract", ["Keywords: hypertension, home monitoring", "Home BP is measured twice daily."]),
    ])
    assert [d.section for d in drafts] == ["Results", "Abstract"]
    assert "Keywords" not in drafts[1].text
    assert "家庭血圧" not in drafts[0].text and all(m["lang"] == "en" for _, m in drafts[0].children)


def test_prune_removes_unlisted_curated_sources_keeps_manual(tdb):
    tdb.add(ResearchSource(id="src_manual", title="Ad hoc", kind="manual"))
    tdb.commit()
    assert prune_sources(tdb, ["src_bp"]) == ["src_mem"]
    assert tdb.get(ResearchSource, "src_mem") is None and store.chunk_count(tdb, "src_mem") == 0
    assert tdb.get(ResearchSource, "src_manual") is not None and store.chunk_count(tdb, "src_bp") > 0


def test_floor_drops_irrelevant_and_can_return_nothing(tdb):
    rr = StubCrossEncoder({"135/85": 5.0, "story": -6.0})
    hits = retrieval.hybrid_retrieve(tdb, "blood pressure", k=6, embedding_provider=EMB, reranker=rr,
                                     min_score=-4.0, use_cache=False)
    assert [h.source_id for h in hits] == ["src_bp"]
    assert retrieval.last_trace.counts["below_floor"] >= 1
    none = retrieval.hybrid_retrieve(tdb, "blood pressure", k=6, embedding_provider=EMB,
                                     reranker=StubCrossEncoder({}), min_score=-4.0, use_cache=False)
    assert none == []


def test_floor_empty_under_pathway_retries_without_it(tdb):
    rr = StubCrossEncoder({"story": 3.0})  # only the cognitive passage is relevant
    hits = retrieval.hybrid_retrieve(tdb, "blood flow", k=6, pathway="cardio", embedding_provider=EMB,
                                     reranker=rr, min_score=-4.0, use_cache=False)
    assert retrieval.last_trace.pathway_fallback and [h.source_id for h in hits] == ["src_mem"]


def test_identity_reranker_is_never_floored(tdb):
    hits = retrieval.hybrid_retrieve(tdb, "blood pressure", k=6, embedding_provider=EMB,
                                     reranker=IdentityReranker(), min_score=100.0, use_cache=False)
    assert hits and "below_floor" not in retrieval.last_trace.counts
