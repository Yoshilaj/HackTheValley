"""In-memory vector store. Replace with FAISS / Chroma when needed."""

from dataclasses import dataclass, field


@dataclass
class Document:
    text: str
    source: str
    vector: list[float]


@dataclass
class VectorStore:
    docs: list[Document] = field(default_factory=list)

    def add(self, text: str, source: str, vector: list[float]) -> None:
        self.docs.append(Document(text=text, source=source, vector=vector))

    def search(self, vector: list[float], k: int = 3) -> list[Document]:
        def dist(d: Document) -> float:
            return sum((a - b) ** 2 for a, b in zip(d.vector, vector))

        return sorted(self.docs, key=dist)[:k]


store = VectorStore()
