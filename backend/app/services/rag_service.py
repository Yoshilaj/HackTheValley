"""RAG retrieval and cited answering (BACKEND_PLAN C3).

``retrieve`` runs hybrid retrieval (:mod:`app.rag.retrieval`: vector + lexical → RRF → rerank →
parent expansion → token-budget packing) and returns expanded parent hits (``Hit.text`` = packed
passage, ``Hit.snippet`` = best-matching child); ``answer_with_citations`` numbers the hits ``[1]..[k]``, sends them with the user's
health-context block to the LLM under the advocate system prompt, and returns
``{answer, sources[{id, title, year, url, snippet, n}]}`` where ``n`` matches the
``[n]`` markers. The output guard is applied by the caller (chat_service) so this
function stays reusable for reports.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterator

from sqlalchemy.orm import Session

from app.prompts.chat_system import build_user_turn, format_excerpts, system_prompt
from app.providers.embeddings import EmbeddingProvider
from app.providers.llm import LlmConfigError, LlmError, LlmProvider
from app.rag.retrieval import hybrid_retrieve
from app.rag.store import Hit

log = logging.getLogger(__name__)

SNIPPET_CHARS = 240

#: Simple keyword → pathway map for retrieval filtering (English corpus). Most keyword hits wins.
PATHWAY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cardio": ("blood pressure", "bp", "hypertension", "heart", "afib", "atrial", "fibrillation", "ecg", "ekg",
               "palpitation", "pulse", "cholesterol", "ldl", "omron", "irregular rhythm", "stroke", "framingham",
               "cardiovascular", "statin"),
    "cognitive": ("memory", "forget", "dementia", "alzheimer", "cognitive", "recall", "story", "confus"),
    "parkinsons": ("parkinson", "tremor", "stiff", "voice", "vowel", "tapping", "dysphonia", "gait", "slowness"),
}


def infer_pathway(text: str) -> str | None:
    """Return the pathway with the most keyword hits, or None (ties → None). Keywords match at the
    start of a word, so "palpitation" also counts "palpitations" and "confus" counts "confused"."""
    t = (text or "").lower()
    counts = {p: sum(1 for kw in kws if re.search(rf"\b{re.escape(kw)}", t)) for p, kws in PATHWAY_KEYWORDS.items()}
    best = max(counts.values())
    if best == 0:
        return None
    winners = [p for p, c in counts.items() if c == best]
    return winners[0] if len(winners) == 1 else None


def retrieve(db: Session, query: str, k: int = 6, pathway: str | None = None,
             embedding_provider: EmbeddingProvider | None = None, reranker: Any = None,
             token_budget: int | None = None) -> list[Hit]:
    """Up to ``k`` expanded research passages for ``query`` (fewer when the ~3000-token budget is
    spent); falls back to all pathways when the pathway filter matches nothing."""
    kw: dict[str, Any] = {} if token_budget is None else {"token_budget": token_budget}
    return hybrid_retrieve(db, query, k=k, pathway=pathway, embedding_provider=embedding_provider,
                           reranker=reranker, **kw)


def _snippet(text: str) -> str:
    from app.rag.expand import strip_prefix

    body = strip_prefix(text)  # drop "[Title (Year)] Section:" prefix
    body = " ".join(body.split())
    return body if len(body) <= SNIPPET_CHARS else body[: SNIPPET_CHARS - 1].rstrip() + "…"


def sources_for(hits: list[Hit]) -> list[dict[str, Any]]:
    """Citation list aligned with the prompt numbering (``n`` = 1-based position)."""
    out = []
    for n, h in enumerate(hits, start=1):
        src = h.source or {}
        out.append({"n": n, "id": h.source_id, "title": src.get("title") or h.source_id, "year": src.get("year"),
                    "url": src.get("url"), "snippet": _snippet(h.snippet or h.text), "chunk_id": h.chunk_id,
                    "section": (h.meta or {}).get("section"), "score": round(float(h.score), 4)})
    return out


def cited_numbers(answer: str, n_max: int) -> list[int]:
    """Distinct ``[n]`` markers in ``answer`` (also ``[1, 3]`` / ``[1-2]``) within ``1..n_max``."""
    nums: set[int] = set()
    for grp in re.findall(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]", answer or ""):
        for part in re.split(r"\s*,\s*", grp):
            if re.search(r"[\-–]", part):
                a, b = (int(x) for x in re.split(r"\s*[\-–]\s*", part)[:2])
                nums.update(range(min(a, b), max(a, b) + 1))
            else:
                nums.add(int(part))
    return sorted(n for n in nums if 1 <= n <= n_max)


def answer_with_citations(db: Session, llm: LlmProvider, question: str, hits: list[Hit], user_context_block: str,
                          language: str, history: list[dict[str, str]] | None = None,
                          audit_hook: Callable[[dict[str, Any]], None] | None = None,
                          max_tokens: int = 1024) -> dict[str, Any]:
    """Ask the LLM to answer ``question`` from the numbered ``hits`` + context block.

    Returns ``{answer, sources, cited, model}``; ``sources`` lists every provided excerpt
    (numbered) and ``cited`` the numbers actually used in the answer. On ``LlmError`` the
    answer is a short apology in the user's language (sources still returned).
    """
    lang = _lang(language)
    messages = _answer_messages(question, hits, user_context_block, history)
    sources = sources_for(hits)
    try:
        resp = llm.complete(system_prompt(lang), messages, max_tokens=max_tokens, purpose="chat_answer",
                            audit_hook=audit_hook)
        answer, model = resp.text.strip(), resp.model
    except LlmConfigError:
        raise  # misconfiguration (missing key) → 503 via app.main handler, not a silent fallback
    except LlmError as exc:
        log.warning("chat LLM call failed: %s", exc)
        answer, model = _apology(lang), None
    return {"answer": answer, "sources": sources, "cited": cited_numbers(answer, len(hits)), "model": model}


def stream_answer_with_citations(db: Session, llm: LlmProvider, question: str, hits: list[Hit],
                                 user_context_block: str, language: str,
                                 history: list[dict[str, str]] | None = None,
                                 audit_hook: Callable[[dict[str, Any]], None] | None = None,
                                 max_tokens: int = 1024) -> Iterator[tuple[str, Any]]:
    """`answer_with_citations`, streamed: yields ("thought", text) and ("text", chunk) while the LLM
    writes, then ("result", {answer, sources, cited, model}) last. On ``LlmError`` the result's
    answer is the apology, even if some text was already yielded."""
    lang = _lang(language)
    messages = _answer_messages(question, hits, user_context_block, history)
    sources = sources_for(hits)
    parts: list[str] = []
    try:
        for kind, chunk in llm.stream(system_prompt(lang), messages, max_tokens=max_tokens, purpose="chat_answer",
                                      audit_hook=audit_hook):
            if kind == "text":
                parts.append(chunk)
            yield kind, chunk
        answer, model = "".join(parts).strip(), llm.model_default
    except LlmConfigError:
        raise
    except LlmError as exc:
        log.warning("chat LLM stream failed: %s", exc)
        answer, model = _apology(lang), None
    yield "result", {"answer": answer, "sources": sources, "cited": cited_numbers(answer, len(hits)), "model": model}


def _lang(language: str | None) -> str:
    return "ja" if (language or "").lower().startswith("ja") else "en"


def _apology(lang: str) -> str:
    return ("申し訳ありません。現在回答を生成できません。しばらくしてからもう一度お試しください。" if lang == "ja" else
            "Sorry — I can't generate an answer right now. Please try again in a moment.")


def _answer_messages(question: str, hits: list[Hit], user_context_block: str,
                     history: list[dict[str, str]] | None) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in (history or [])
                                      if m.get("role") in ("user", "assistant") and m.get("content")]
    # Anthropic requires alternating roles starting with user.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    if messages and messages[-1]["role"] == "user":
        messages.pop()
    messages.append({"role": "user", "content": build_user_turn(question, format_excerpts(hits), user_context_block)})
    return messages
