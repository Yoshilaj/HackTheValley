"""Parent expansion: map reranked child chunks to their parent sections.

Children are small for retrieval precision; the LLM should read the fuller section. Surviving
children are grouped by ``parent_id`` (first appearance = rank order, score = best child score);
each group becomes one parent :class:`Hit` carrying the parent text, the best child (``chunk_id``,
``snippet``) and every matched child (``meta["children"]``) so packing can fall back to them when
the section is too long for the budget. Chunks without a parent (legacy rows, ad-hoc text) pass
through unchanged as their own group.
"""

from __future__ import annotations

import re
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ResearchParent
from app.rag.store import Hit


_PREFIX = re.compile(r"^\[[^\n]*\] Section: [^\n]*\n")


def split_prefix(text: str) -> tuple[str, str]:
    """``(prefix_line_with_newline, body)``; prefix is ``""`` when absent."""
    m = _PREFIX.match(text or "")
    return (m.group(0), text[m.end():]) if m else ("", text or "")


def strip_prefix(text: str) -> str:
    """Drop the ``"[Title (Year)] Section: …\\n"`` line prepended to every child chunk."""
    return split_prefix(text)[1]


def expand_to_parents(db: Session, hits: list[Hit]) -> list[Hit]:
    groups: dict[str, list[Hit]] = {}
    for h in hits:
        groups.setdefault(h.parent_id or f"chunk:{h.chunk_id}", []).append(h)
    pids = [g for g in groups if not g.startswith("chunk:")]
    parents = {p.id: p for p in db.scalars(select(ResearchParent).where(ResearchParent.id.in_(pids)))} if pids else {}

    out: list[Hit] = []
    for key, members in groups.items():
        best = max(members, key=lambda h: h.score)  # members are already rank-ordered; max for safety
        children = [{"chunk_id": m.chunk_id, "ordinal": m.meta.get("child_index"), "text": strip_prefix(m.text),
                     "score": m.score} for m in members]
        p = parents.get(key)
        if p is None:
            out.append(replace(best, snippet=strip_prefix(best.text), meta=dict(best.meta, children=children)))
            continue
        prefix = split_prefix(best.text)[0]
        out.append(replace(best, text=prefix + p.text, snippet=strip_prefix(best.text),
                           meta=dict(best.meta, section=p.section, parent_ordinal=p.ordinal, children=children,
                                     n_children_matched=len(members))))
    out.sort(key=lambda h: -h.score)
    return out
