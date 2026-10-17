from fastapi import APIRouter

from app.rag.pipeline import answer_question, ingest_text
from app.schemas.chat import ChatRequest, ChatResponse, IngestRequest, IngestResponse

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    answer, sources = answer_question(req.message)
    return ChatResponse(answer=answer, sources=sources)


@router.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest):
    added = ingest_text(req.text, req.source)
    return IngestResponse(chunks_added=added)
