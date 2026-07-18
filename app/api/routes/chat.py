import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.db.models import ChatAudit, UsageEvent
from app.schemas.chat import ChatMessage, ChatRequest
from app.services.rate_limit import limit_request
from app.services.quota import enforce_quota
from app.services.domain_lookup import live_domain_context

router = APIRouter(prefix="/chat", tags=["chat"])
log = structlog.get_logger()


def prompt_size(payload: ChatRequest) -> int:
    return sum(len(message.content) for message in payload.messages)


async def audit(db: AsyncSession, user_id: object, payload: ChatRequest, result: str, error: str | None = None) -> None:
    db.add(ChatAudit(user_id=user_id, model=payload.model, prompt_chars=prompt_size(payload), status=result, error=error))
    await db.commit()


async def record_usage(db: AsyncSession, user_id: object, payload: ChatRequest, response: dict) -> None:
    db.add(
        UsageEvent(
            user_id=user_id,
            model=payload.model,
            input_tokens=int(response.get("prompt_eval_count", 0)),
            output_tokens=int(response.get("eval_count", 0)),
            total_duration_ns=int(response.get("total_duration", 0)),
            load_duration_ns=int(response.get("load_duration", 0)),
        )
    )
    await db.commit()


@router.get("/models")
async def models(request: Request, _: CurrentUser, __: None = Depends(limit_request)) -> dict:
    try:
        return await request.app.state.ollama.list_models()
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model service unavailable") from exc


@router.post("")
async def chat(
    payload: ChatRequest,
    request: Request,
    user: CurrentUser,
    db: DbSession,
    _: None = Depends(limit_request),
):
    if prompt_size(payload) > get_settings().max_prompt_chars:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Prompt exceeds configured limit")
    await enforce_quota(db, user.id)
    latest_user_message = next((message.content for message in reversed(payload.messages) if message.role == "user"), "")
    tool_instruction = await live_domain_context(latest_user_message)
    if tool_instruction:
        existing_system = next((message for message in payload.messages if message.role == "system"), None)
        if existing_system:
            existing_system.content = f"{existing_system.content}\n\n{tool_instruction}"
        else:
            payload.messages.insert(0, ChatMessage(role="system", content=tool_instruction))
    try:
        if payload.stream:
            await audit(db, user.id, payload, "streaming")
            return StreamingResponse(
                request.app.state.ollama.stream_chat(payload),
                media_type="application/x-ndjson",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        response = await request.app.state.ollama.chat(payload)
        await audit(db, user.id, payload, "completed")
        await record_usage(db, user.id, payload, response)
        return response
    except httpx.TimeoutException as exc:
        await audit(db, user.id, payload, "timeout", "Ollama request timed out")
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "Model request timed out") from exc
    except httpx.HTTPError as exc:
        await audit(db, user.id, payload, "failed", str(exc)[:1000])
        log.warning("ollama_request_failed", user_id=str(user.id), model=payload.model)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model service unavailable") from exc
