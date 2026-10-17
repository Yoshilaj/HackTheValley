"""Context packing: fit expanded parents into a token budget, numbered ``[1]..[n]``.

Greedy in rank order. Each parent contributes the full section when it fits the per-passage cap,
otherwise its matched children (best first, then in reading order) up to the cap, otherwise just
the best child. A passage that does not fit the remaining budget is skipped (a later, shorter one
may still fit); the first passage is always kept, trimmed to the budget if necessary. Source
diversity (soft cap): in the first pass at most ``max_per_source`` (default :data:`MAX_PER_SOURCE`)
passages come from one source, so the slots go to the next-best other sources; passages skipped
only for that cap then fill whatever slots and budget are left, in rank order (a question about one
paper still gets several passages of it). ``None`` disables the cap. Tokens are estimated at ~4
characters each (no tokenizer dependency).
"""

from __future__ import annotations

from dataclasses import replace

from app.rag.expand import split_prefix
from app.rag.store import Hit

TOKEN_BUDGET = 3000  # ~4-6 passages; 1500 left only 2-3 once sections were expanded
MAX_PASSAGE_TOKENS = 700
MAX_PER_SOURCE = 2  # passages per source document in one packed context


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _passage(h: Hit, cap: int) -> str:
    prefix, body = split_prefix(h.text)
    if estimate_tokens(prefix + body) <= cap:
        return prefix + body
    children = h.meta.get("children") or []
    if not children:
        return prefix + body
    best, rest = children[0], children[1:]
    chosen = [best]
    used = estimate_tokens(prefix + best["text"])
    for c in rest:
        cost = estimate_tokens(c["text"]) + 1
        if used + cost <= cap:
            chosen.append(c)
            used += cost
    chosen.sort(key=lambda c: (c.get("ordinal") is None, c.get("ordinal") or 0))
    return prefix + "\n…\n".join(c["text"] for c in chosen)


def pack(hits: list[Hit], token_budget: int = TOKEN_BUDGET, max_passage_tokens: int = MAX_PASSAGE_TOKENS,
         max_passages: int | None = None, max_per_source: int | None = MAX_PER_SOURCE) -> list[Hit]:
    """Returns the hits that made it, with ``text`` replaced by the packed passage and
    ``meta["n"]`` / ``meta["tokens"]`` set. Positions in the returned list are the citation numbers."""
    out: list[Hit] = []
    used = 0
    per_source: dict[str, int] = {}
    overflow: list[Hit] = []  # skipped only because their source hit the cap
    cap = min(max_passage_tokens, token_budget)

    def full() -> bool:
        return max_passages is not None and len(out) >= max_passages

    def take(h: Hit) -> None:
        nonlocal used
        text = _passage(h, cap)
        cost = estimate_tokens(text)
        if out and used + cost > token_budget:
            return
        if not out and cost > token_budget:
            text = text[: token_budget * 4].rstrip() + "…"
            cost = estimate_tokens(text)
        out.append(replace(h, text=text, meta=dict(h.meta, n=len(out) + 1, tokens=cost)))
        used += cost
        per_source[h.source_id] = per_source.get(h.source_id, 0) + 1

    for h in hits:
        if full():
            break
        if max_per_source is not None and per_source.get(h.source_id, 0) >= max_per_source:
            overflow.append(h)
            continue
        take(h)
    for h in overflow:
        if full():
            break
        take(h)
    return out


def format_context(hits: list[Hit]) -> str:
    """``[n] Title (Year)\\npassage`` blocks (same shape as ``prompts.chat_system.format_excerpts``)."""
    blocks = []
    for n, h in enumerate(hits, start=1):
        src = h.source or {}
        blocks.append(f"[{n}] {src.get('title') or h.source_id} ({src.get('year') or 'n.d.'})\n{h.text.strip()}")
    return "\n\n".join(blocks)
