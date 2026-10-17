"""Text chunking for the RAG corpus.

Parent/child design (GymSync ``ingest/chunk.py``): a paper SECTION becomes a parent (what the LLM
reads); :func:`split_section` cuts it into CHILD chunks of ~``CHILD_WORDS`` words on sentence
boundaries, carrying ~``OVERLAP_WORDS`` words of trailing sentences into the next child so a fact
spanning a boundary is never lost. Children are what get embedded and lexically indexed.

Word counts approximate tokens (1 token ≈ 0.75 words → 380 words ≈ 512 tokens). Sentences longer
than a whole child (tables flattened to "a | b" rows, unpunctuated PDF text) are hard-split on
words first; CJK runs count one "word" per character so Japanese text is sized sensibly too.

:func:`chunk_text` / :func:`chunk_paragraphs` (char-based) are the legacy v2 chunkers, kept for
callers that want plain character windows.
"""

from __future__ import annotations

import re

_SENT_END = re.compile(r"(?<=[.!?。！？])\s*")


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping character chunks."""
    if size <= overlap:
        raise ValueError("size must be greater than overlap")
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size - overlap
    return chunks


def _split_long(text: str, size: int, overlap: int) -> list[str]:
    """Like :func:`chunk_text` but cuts at a sentence end in the last 40% of each window when possible."""
    out: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            window = text[start:end]
            cut = None
            for m in _SENT_END.finditer(window):
                if m.end() >= size * 0.6:
                    cut = m.end()
            if cut:
                end = start + cut
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        nxt = max(end - overlap, start + 1)
        sp = text.find(" ", nxt, end)  # start the overlap on a word boundary (no-op for JA)
        start = sp + 1 if sp != -1 and sp + 1 < end else nxt
    return out


def chunk_paragraphs(paragraphs: list[str], size: int = 500, overlap: int = 50) -> list[str]:
    """Greedily pack paragraphs into chunks of ≤ ``size`` chars, preferring paragraph boundaries.

    Paragraphs longer than ``size`` are split with :func:`_split_long` (sentence-aware,
    ``overlap`` chars of overlap). Packed paragraphs are joined with a newline.
    """
    chunks: list[str] = []
    buf = ""
    for p in (p.strip() for p in paragraphs):
        if not p:
            continue
        if len(p) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_long(p, size, overlap))
            continue
        if buf and len(buf) + 1 + len(p) > size:
            chunks.append(buf)
            buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


# ---------------------------------------------------------------- parent/child (v3)

CHILD_WORDS = 380
OVERLAP_WORDS = 60

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=[。！？])\s*|\n+")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")


def word_len(text: str) -> int:
    """Whitespace words, plus one per CJK character (unspaced scripts)."""
    cjk = len(_CJK.findall(text))
    return len(text.split()) + cjk


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s and s.strip()]


def _hard_split(sent: str, limit: int) -> list[str]:
    """Split one oversized 'sentence' into ≤ ``limit``-word pieces (chars for CJK runs)."""
    words = sent.split()
    if len(words) > 1 and not _CJK.search(sent):
        return [" ".join(words[i : i + limit]) for i in range(0, len(words), limit)]
    out, buf = [], ""
    for tok in re.findall(r"\S+\s*", sent):
        if buf and word_len(buf + tok) > limit:
            out.append(buf.strip())
            buf = ""
        if word_len(tok) > limit:  # one unspaced CJK run longer than a chunk
            step = max(1, limit)
            out.extend(tok[i : i + step].strip() for i in range(0, len(tok), step) if tok[i : i + step].strip())
            continue
        buf += tok
    if buf.strip():
        out.append(buf.strip())
    return out


def split_section(text: str, child_words: int = CHILD_WORDS, overlap_words: int = OVERLAP_WORDS) -> list[str]:
    """Greedy sentence packing to ~``child_words`` with ~``overlap_words`` carried between chunks."""
    if child_words <= overlap_words:
        raise ValueError("child_words must be greater than overlap_words")
    sents: list[str] = []
    for s in sentences(text):
        sents.extend(_hard_split(s, child_words) if word_len(s) > child_words else [s])
    if not sents:
        return []
    chunks: list[str] = []
    cur: list[str] = []
    cur_words = 0
    added_since_flush = False
    for sent in sents:
        w = word_len(sent)
        if cur and added_since_flush and cur_words + w > child_words:
            chunks.append(" ".join(cur))
            overlap: list[str] = []
            ow = 0
            for prev in reversed(cur):
                pw = word_len(prev)
                if ow + pw > overlap_words:
                    break
                overlap.insert(0, prev)
                ow += pw
            if ow + w > child_words:  # overlap + next sentence would overflow → drop the overlap
                overlap, ow = [], 0
            cur, cur_words = overlap, ow
            added_since_flush = False
        cur.append(sent)
        cur_words += w
        added_since_flush = True
    if cur and added_since_flush:
        chunks.append(" ".join(cur))
    return chunks
