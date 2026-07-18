from hashlib import sha256
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import DocumentChunk, KnowledgeDocument
from app.services.ollama import OllamaService

ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}


def extract_text(filename: str, raw: bytes) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Supported documents are .txt, .md, and .pdf")
    if extension == ".pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(raw)).pages).strip()
    return raw.decode("utf-8", errors="replace").strip()


def split_text(text: str) -> list[str]:
    settings = get_settings()
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + settings.rag_chunk_size)
        if end < len(normalized):
            boundary = max(normalized.rfind(". ", start, end), normalized.rfind("\n", start, end))
            if boundary > start + settings.rag_chunk_size // 2:
                end = boundary + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - settings.rag_chunk_overlap, start + 1)
    return chunks


async def index_document(
    db: AsyncSession, ollama: OllamaService, user_id: object, filename: str, content_type: str, raw: bytes
) -> KnowledgeDocument:
    text = extract_text(filename, raw)
    if not text:
        raise ValueError("No readable text was found in this document")
    chunks = split_text(text)
    if not chunks:
        raise ValueError("No indexable text was found in this document")
    fingerprint = sha256(raw).hexdigest()
    existing = await db.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.user_id == user_id, KnowledgeDocument.content_hash == fingerprint)
    )
    if existing:
        return existing
    vectors = await ollama.embed(chunks)
    document = KnowledgeDocument(
        user_id=user_id,
        filename=Path(filename).name[:255],
        content_type=content_type or "application/octet-stream",
        content_hash=fingerprint,
        chunk_count=len(chunks),
    )
    db.add(document)
    await db.flush()
    db.add_all(
        DocumentChunk(document_id=document.id, user_id=user_id, chunk_index=index, content=chunk, embedding=vector)
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
    )
    await db.commit()
    await db.refresh(document)
    return document


async def retrieve_context(
    db: AsyncSession, ollama: OllamaService, user_id: object, query: str
) -> list[tuple[DocumentChunk, KnowledgeDocument, float]]:
    settings = get_settings()
    if not settings.rag_enabled or not query.strip():
        return []
    has_documents = await db.scalar(
        select(DocumentChunk.id).where(DocumentChunk.user_id == user_id).limit(1)
    )
    if has_documents is None:
        return []
    vector = (await ollama.embed([query]))[0]
    distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")
    rows = await db.execute(
        select(DocumentChunk, KnowledgeDocument, distance)
        .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
        .where(DocumentChunk.user_id == user_id)
        .order_by(distance)
        .limit(settings.rag_top_k)
    )
    return [(chunk, document, float(score)) for chunk, document, score in rows.all()]


def build_rag_instruction(results: list[tuple[DocumentChunk, KnowledgeDocument, float]]) -> str | None:
    if not results:
        return None
    excerpts = "\n\n".join(
        f"[Source: {document.filename}, section {chunk.chunk_index + 1}]\n{chunk.content}"
        for chunk, document, _ in results
    )
    return (
        "Answer using the knowledge-base excerpts below when they are relevant. "
        "Do not invent facts not supported by the excerpts. If the answer is not in the excerpts, say so. "
        "Cite supporting sources using [Source: filename, section N].\n\n"
        f"Knowledge-base excerpts:\n{excerpts}"
    )
