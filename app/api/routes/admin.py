from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, update

from app.api.deps import AdminUser, DbSession
from app.core.security import create_api_key
from app.db.models import ApiKey, UsageEvent, User
from app.schemas.admin import AdminApiKeyCreate, AdminOverview, AdminUserUsage, ApiKeyCreated, ApiKeyResponse

router = APIRouter(prefix="/admin", tags=["admin"])


async def user_usage_rows(db: DbSession) -> list[AdminUserUsage]:
    rows = await db.execute(
        select(
            User.id,
            User.email,
            User.is_active,
            User.is_admin,
            func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
            func.count(UsageEvent.id).label("request_count"),
            func.max(UsageEvent.created_at).label("last_activity"),
        )
        .outerjoin(UsageEvent, UsageEvent.user_id == User.id)
        .group_by(User.id)
        .order_by(func.coalesce(func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens), 0).desc())
    )
    return [
        AdminUserUsage(
            id=row.id, email=row.email, is_active=row.is_active, is_admin=row.is_admin,
            input_tokens=row.input_tokens, output_tokens=row.output_tokens,
            total_tokens=row.input_tokens + row.output_tokens, request_count=row.request_count,
            last_activity=row.last_activity,
        )
        for row in rows
    ]


@router.get("/overview", response_model=AdminOverview)
async def overview(_: AdminUser, db: DbSession) -> AdminOverview:
    users = await user_usage_rows(db)
    return AdminOverview(
        user_count=len(users), active_user_count=sum(user.is_active for user in users),
        request_count=sum(user.request_count for user in users),
        input_tokens=sum(user.input_tokens for user in users), output_tokens=sum(user.output_tokens for user in users),
        total_tokens=sum(user.total_tokens for user in users), users=users,
    )


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(_: AdminUser, db: DbSession) -> list[ApiKey]:
    result = await db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()).limit(500))
    return list(result)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def generate_api_key(payload: AdminApiKeyCreate, _: AdminUser, db: DbSession) -> ApiKeyCreated:
    owner = await db.get(User, payload.user_id)
    if owner is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target user not found")
    secret, prefix, key_hash = create_api_key()
    key = ApiKey(
        user_id=owner.id, name=payload.name, key_prefix=prefix, key_hash=key_hash,
        expires_at=(datetime.now(UTC) + timedelta(days=payload.expires_in_days)) if payload.expires_in_days else None,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreated(**ApiKeyResponse.model_validate(key).model_dump(), api_key=secret)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: UUID, _: AdminUser, db: DbSession) -> None:
    result = await db.execute(
        update(ApiKey).where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC))
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active API key not found")
    await db.commit()
