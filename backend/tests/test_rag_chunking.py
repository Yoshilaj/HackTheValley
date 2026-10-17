"""Parent/child chunking (corpus v3): sentence packing, overlap, hard splits, prefixes."""

from app.rag.chunking import CHILD_WORDS, OVERLAP_WORDS, sentences, split_section, word_len
from app.rag.ingest import Section, build_chunks, build_parents


def _text(n: int) -> str:
    return " ".join(f"Sentence {i} talks about home blood pressure readings." for i in range(n))


def test_short_section_is_one_child():
    assert split_section("One sentence. Two sentences!") == ["One sentence. Two sentences!"]
    assert split_section("   ") == []


def test_children_respect_size_and_sentence_boundaries():
    chunks = split_section(_text(200))
    assert len(chunks) > 3
    assert all(word_len(c) <= CHILD_WORDS for c in chunks)
    assert all(c.endswith(".") for c in chunks)  # never cut mid-sentence
    assert all(c.startswith("Sentence ") for c in chunks)


def test_overlap_carries_trailing_sentences():
    chunks = split_section(_text(200))
    for a, b in zip(chunks, chunks[1:]):
        tail = sentences(a)[-1]
        assert tail in b  # last sentence of a child reappears at the start of the next
        head = b[: b.index(tail) + len(tail)]
        assert word_len(head) <= OVERLAP_WORDS + word_len(tail)


def test_oversized_sentence_is_hard_split():
    table = "cell | value | " * 400  # no sentence punctuation at all
    chunks = split_section(table)
    assert len(chunks) >= 3 and all(word_len(c) <= CHILD_WORDS for c in chunks)
    ja = "家庭血圧は朝と夜に測定します。" * 200
    assert all(word_len(c) <= CHILD_WORDS for c in split_section(ja))


def test_build_parents_prefix_and_global_child_index():
    secs = [Section("Intro", ["Short intro."]), Section("Methods", [_text(120)]), Section("Empty", [])]
    parents = build_parents("A Title", 2021, secs)
    assert [p.section for p in parents] == ["Intro", "Methods"]
    assert parents[0].text == "Short intro." and len(parents[0].children) == 1
    idx = [m["child_index"] for p in parents for _, m in p.children]
    assert idx == list(range(len(idx)))
    text, meta = parents[1].children[0]
    assert text.startswith("[A Title (2021)] Section: Methods\n") and meta["section"] == "Methods"
    assert len(build_chunks("A Title", 2021, secs)) == len(idx)
