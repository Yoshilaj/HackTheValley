from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = []


class IngestRequest(BaseModel):
    text: str
    source: str = "manual"


class IngestResponse(BaseModel):
    chunks_added: int
