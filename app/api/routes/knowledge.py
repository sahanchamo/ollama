import httpx
from uuid import UUID
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.db.models import KnowledgeDocument
from app.schemas.rag import DocumentResponse, KnowledgeSearchRequest, KnowledgeSearchResult
from app.services.rag import index_document, retrieve_context
from app.services.rate_limit import limit_request

router = APIRouter(prefix="/knowledge", tags=["knowledge base"])


@router.get("/documents", response_model=list[DocumentResponse])
async def list_documents(user: CurrentUser, db: DbSession) -> list[KnowledgeDocument]:
    result = await db.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.user_id == user.id)
        .order_by(KnowledgeDocument.created_at.desc())
    )
    return list(result)


@router.post("/documents", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
    _: None = Depends(limit_request),
) -> KnowledgeDocument:
    if not file.filename:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A filename is required")
    raw = await file.read(get_settings().rag_max_upload_bytes + 1)
    if len(raw) > get_settings().rag_max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Document exceeds upload size limit")
    try:
        return await index_document(db, request.app.state.ollama, user.id, file.filename, file.content_type or "", raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Could not index document: {exc}") from exc


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: UUID, user: CurrentUser, db: DbSession) -> Response:
    document = await db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document_id, KnowledgeDocument.user_id == user.id)
    )
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await db.delete(document)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=list[KnowledgeSearchResult])
async def search_knowledge(
    payload: KnowledgeSearchRequest, request: Request, user: CurrentUser, db: DbSession, _: None = Depends(limit_request)
) -> list[KnowledgeSearchResult]:
    try:
        results = await retrieve_context(db, request.app.state.ollama, user.id, payload.query)
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"Knowledge search unavailable: {exc}") from exc
    return [
        KnowledgeSearchResult(
            document_id=document.id,
            filename=document.filename,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            distance=distance,
        )
        for chunk, document, distance in results
    ]
