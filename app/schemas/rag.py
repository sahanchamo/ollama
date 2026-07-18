from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    id: UUID
    filename: str
    content_type: str
    chunk_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)


class KnowledgeSearchResult(BaseModel):
    document_id: UUID
    filename: str
    chunk_index: int
    content: str
    distance: float
