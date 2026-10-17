"""Embedding backend. Swap the implementation here without touching callers.

Currently a placeholder that returns a trivial vector so the pipeline runs
end-to-end before a real model is wired in (e.g. sentence-transformers).
"""


def embed(texts: list[str]) -> list[list[float]]:
    return [[float(len(t))] for t in texts]
