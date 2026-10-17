"""High-level RAG entry points used by the API layer."""

from app.rag.chunking import chunk_text
from app.rag.embeddings import embed
from app.rag.store import store


def ingest_text(text: str, source: str) -> int:
    chunks = chunk_text(text)
    vectors = embed(chunks)
    for chunk, vec in zip(chunks, vectors):
        store.add(chunk, source, vec)
    return len(chunks)


def answer_question(question: str) -> tuple[str, list[str]]:
    hits = store.search(embed([question])[0])
    if not hits:
        return "No documents ingested yet.", []
    # TODO: pass `hits` as context to an LLM and return its answer.
    context = "\n---\n".join(h.text for h in hits)
    return f"[stub] Retrieved context:\n{context}", [h.source for h in hits]
