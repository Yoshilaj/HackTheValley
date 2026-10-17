"""Lexical (keyword) side of hybrid retrieval.

- Postgres: ``to_tsvector('english', text) @@ query`` ranked by ``ts_rank``, served by the GIN
  expression index ``ix_research_chunks_fts`` (created by the init hook in ``app.db.types``).
  The query is parsed with ``websearch_to_tsquery('english', …)`` (stemming, stopwords, quoted
  phrases, ``-negation``) and its top-level ``&`` are rewritten to ``|``: natural-language
  questions rarely contain *every* term of a passage, and an all-terms AND would usually return
  nothing. ``ts_rank`` still favours chunks matching more (and rarer) terms.
- SQLite (dev/tests): Okapi BM25 (``rank_bm25``) over an in-process index built lazily from
  ``research_chunks`` and rebuilt when the corpus signature changes (see ``store.corpus_index``).

The tokenizer mirrors the Postgres english config closely enough for ranking: lowercase
alphanumerics, english stopwords removed, a light suffix stemmer (plural / -ing / -ed / -ly / -e).
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*|[぀-ヿ㐀-鿿]")

STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below between
both but by can did do does doing down during each few for from further had has have having he her here
hers herself him himself his how i if in into is it its itself just me more most my myself no nor not now
of off on once only or other our ours ourselves out over own same she should so some such than that the
their theirs them themselves then there these they this those through to too under until up very was we
were what when where which while who whom why will with would you your yours yourself yourselves
""".split())


def stem(tok: str) -> str:
    """Tiny suffix stripper: plural → -ing/-ed/-ly → trailing e, so pressure/pressures/pressured and
    measure/measured/measuring collapse to one stem (enough for BM25; not linguistics)."""
    if len(tok) <= 3 or not tok.isalpha():
        return tok
    if tok.endswith("ies") and len(tok) > 4:
        tok = tok[:-3] + "y"
    elif tok.endswith("sses"):
        tok = tok[:-2]
    elif tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        tok = tok[:-1]
    for suf in ("ing", "edly", "ed", "ly"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            tok = tok[: -len(suf)]
            break
    if tok.endswith("e") and len(tok) > 4:
        tok = tok[:-1]
    return tok


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in _TOKEN.findall((text or "").lower()) if t not in STOPWORDS]


PG_LEXICAL_SQL = """
SELECT c.id AS id, ts_rank(to_tsvector('english', c.text), q.tsq) AS rank
FROM research_chunks c,
     (SELECT replace(websearch_to_tsquery('english', :query)::text, ' & ', ' | ')::tsquery AS tsq) q
WHERE to_tsvector('english', c.text) @@ q.tsq {source_filter}
ORDER BY rank DESC
LIMIT :limit
"""

from app.db.types import POSTGRES_SEARCH_INDEXES  # noqa: E402  (GIN index DDL, created by init_db)
