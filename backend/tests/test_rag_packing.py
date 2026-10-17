"""Parent expansion and token-budget packing."""

from app.rag.packing import estimate_tokens, format_context, pack
from app.rag.store import Hit

PREFIX = "[Paper (2020)] Section: Results\n"


def _parent(cid: str, body: str, score: float, children: list[dict] | None = None) -> Hit:
    return Hit(chunk_id=cid, source_id="s", text=PREFIX + body, score=score, parent_id="p" + cid,
               source={"id": "s", "title": "Paper", "year": 2020, "url": None},
               meta={"children": children or [{"chunk_id": cid, "child_index": 0, "ordinal": 0, "text": body}]})


def test_pack_respects_budget_and_numbers():
    hits = [_parent(str(i), "word " * 200, 1.0 - i / 10) for i in range(6)]  # ~258 tokens each
    out = pack(hits, token_budget=600)
    assert len(out) == 2 and [h.meta["n"] for h in out] == [1, 2]
    assert sum(h.meta["tokens"] for h in out) <= 600
    assert all(h.text.startswith(PREFIX) for h in out)


def test_pack_skips_too_long_passage_but_fills_with_later_shorter():
    long_children = [{"chunk_id": "L", "ordinal": 0, "text": "long " * 900}]
    hits = [_parent("a", "x " * 400, 0.9), _parent("L", "long " * 900, 0.8, long_children),
            _parent("b", "short passage.", 0.7)]
    out = pack(hits, token_budget=500, max_passage_tokens=500)
    assert [h.chunk_id for h in out] == ["a", "b"]


def test_long_parent_falls_back_to_matched_children():
    kids = [{"chunk_id": "c2", "ordinal": 2, "text": "best child text."},
            {"chunk_id": "c0", "ordinal": 0, "text": "earlier matched child."}]
    h = _parent("c2", "filler " * 2000, 0.9, kids)
    out = pack([h], token_budget=1500, max_passage_tokens=300)
    assert out[0].text == PREFIX + "earlier matched child.\n…\nbest child text."  # reading order


def test_first_passage_always_kept_and_trimmed():
    h = Hit(chunk_id="z", source_id="s", text="y" * 20000, score=1.0)
    out = pack([h], token_budget=100)
    assert len(out) == 1 and estimate_tokens(out[0].text) <= 101


def test_format_context_numbering():
    out = pack([_parent("a", "alpha.", 0.9), _parent("b", "beta.", 0.8)])
    ctx = format_context(out)
    assert ctx.startswith("[1] Paper (2020)\n") and "\n\n[2] Paper (2020)\n" in ctx


def _src(cid: str, source_id: str, score: float) -> Hit:
    return Hit(chunk_id=cid, source_id=source_id, text=PREFIX + f"passage {cid}.", score=score, parent_id="p" + cid,
               source={"id": source_id, "title": source_id, "year": 2020, "url": None}, meta={})


def test_pack_caps_passages_per_source_and_fills_with_other_sources():
    hits = [_src("a1", "A", 0.99), _src("a2", "A", 0.98), _src("a3", "A", 0.97), _src("a4", "A", 0.96),
            _src("b1", "B", 0.5), _src("c1", "C", 0.4), _src("a5", "A", 0.3)]
    out = pack(hits, max_passages=4)
    assert [h.chunk_id for h in out] == ["a1", "a2", "b1", "c1"]
    assert [h.meta["n"] for h in out] == [1, 2, 3, 4]
    out1 = pack(hits, max_passages=3, max_per_source=1)
    assert [h.source_id for h in out1] == ["A", "B", "C"]
    assert len(pack(hits, max_passages=4, max_per_source=None)) == 4  # cap disabled: rank order
    assert [h.chunk_id for h in pack(hits, max_passages=4, max_per_source=None)] == ["a1", "a2", "a3", "a4"]


def test_soft_cap_fills_leftover_slots_from_capped_source():
    hits = [_src("a1", "A", 0.99), _src("a2", "A", 0.98), _src("a3", "A", 0.97), _src("b1", "B", 0.5),
            _src("a4", "A", 0.3)]
    out = pack(hits, max_passages=6)  # only one other source: the rest of A fills the free slots
    assert [h.chunk_id for h in out] == ["a1", "a2", "b1", "a3", "a4"]
    assert [h.meta["n"] for h in out] == [1, 2, 3, 4, 5]
    single = [_src(f"s{i}", "S", 1 - i / 10) for i in range(5)]
    assert len(pack(single, max_passages=4)) == 4  # a one-source question is not starved at 2
