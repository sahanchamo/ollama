from collections.abc import AsyncIterator

import httpx

from app.core.config import get_settings
from app.schemas.chat import ChatRequest


class OllamaService:
    def __init__(self) -> None:
        settings = get_settings()
        self.client = httpx.AsyncClient(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.ollama_timeout_seconds, connect=10),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def health(self) -> bool:
        try:
            response = await self.client.get("/api/tags")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def list_models(self) -> dict:
        response = await self.client.get("/api/tags")
        response.raise_for_status()
        return response.json()

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        settings = get_settings()
        response = await self.client.post(
            "/api/embed",
            json={"model": settings.rag_embedding_model, "input": inputs, "truncate": True},
        )
        if response.status_code == 404:
            # Ollama versions before the batch /api/embed endpoint expose the legacy single-prompt endpoint.
            embeddings = []
            for text in inputs:
                legacy_response = await self.client.post(
                    "/api/embeddings", json={"model": settings.rag_embedding_model, "prompt": text}
                )
                legacy_response.raise_for_status()
                embeddings.append(legacy_response.json()["embedding"])
        else:
            response.raise_for_status()
            embeddings = response.json().get("embeddings", [])
        if len(embeddings) != len(inputs):
            raise RuntimeError("Embedding service returned an unexpected number of vectors")
        if any(len(vector) != settings.rag_embedding_dimensions for vector in embeddings):
            raise RuntimeError(
                f"Embedding dimensions do not match RAG_EMBEDDING_DIMENSIONS={settings.rag_embedding_dimensions}"
            )
        return embeddings

    async def chat(self, request: ChatRequest) -> dict:
        payload = request.model_dump(exclude={"stream"}) | {"stream": False}
        response = await self.client.post("/api/chat", json=payload)
        response.raise_for_status()
        return response.json()

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[bytes]:
        payload = request.model_dump() | {"stream": True}
        async with self.client.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk
