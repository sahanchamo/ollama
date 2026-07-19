from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select, update

from app.api.deps import AdminUser, DbSession, ROLE_PERMISSIONS, require_permission, roles_for_user
from app.core.security import create_api_key, hash_password
from app.db.models import ApiKey, UsageEvent, User, UserQuota, UserRole
from app.schemas.admin import (
    AdminAnalytics, AdminApiKeyCreate, AdminDailyUsage, AdminModelUsage, AdminOverview, AdminRecentUsage,
    AdminPasswordReset, AdminQuotaUpdate, AdminRoleUpdate, AdminUserCreate, AdminUserQuota, AdminUserRoles, AdminUserUpdate, AdminUserUsage, ApiKeyCreated, ApiKeyResponse,
)
from app.services.quota import monthly_tokens

router = APIRouter(prefix="/admin", tags=["admin"])
AnalyticsPrincipal = Annotated[User, Depends(require_permission("analytics.read"))]
KeyManager = Annotated[User, Depends(require_permission("keys.manage"))]
UserManager = Annotated[User, Depends(require_permission("users.manage"))]


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
async def overview(_: AnalyticsPrincipal, db: DbSession) -> AdminOverview:
    users = await user_usage_rows(db)
    return AdminOverview(
        user_count=len(users), active_user_count=sum(user.is_active for user in users),
        request_count=sum(user.request_count for user in users),
        input_tokens=sum(user.input_tokens for user in users), output_tokens=sum(user.output_tokens for user in users),
        total_tokens=sum(user.total_tokens for user in users), users=users,
    )


@router.get("/analytics", response_model=AdminAnalytics)
async def analytics(_: AnalyticsPrincipal, db: DbSession, days: int = Query(default=30, ge=1, le=365)) -> AdminAnalytics:
    start = datetime.now(UTC) - timedelta(days=days)
    daily_rows = await db.execute(
        select(
            func.date(UsageEvent.created_at).label("day"),
            func.count(UsageEvent.id).label("request_count"),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
        )
        .where(UsageEvent.created_at >= start)
        .group_by(func.date(UsageEvent.created_at))
        .order_by(func.date(UsageEvent.created_at))
    )
    model_rows = await db.execute(
        select(
            UsageEvent.model,
            func.count(UsageEvent.id).label("request_count"),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.avg(UsageEvent.total_duration_ns), 0).label("average_duration_ns"),
        )
        .where(UsageEvent.created_at >= start)
        .group_by(UsageEvent.model)
        .order_by(func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens).desc())
    )
    recent_rows = await db.execute(
        select(UsageEvent, User.email)
        .join(User, User.id == UsageEvent.user_id)
        .order_by(UsageEvent.created_at.desc())
        .limit(50)
    )
    active_keys = await db.scalar(select(func.count(ApiKey.id)).where(ApiKey.revoked_at.is_(None))) or 0
    revoked_keys = await db.scalar(select(func.count(ApiKey.id)).where(ApiKey.revoked_at.is_not(None))) or 0
    return AdminAnalytics(
        days=days,
        active_key_count=active_keys,
        revoked_key_count=revoked_keys,
        daily=[AdminDailyUsage(**row._mapping) for row in daily_rows],
        models=[
            AdminModelUsage(
                model=row.model, request_count=row.request_count, input_tokens=row.input_tokens,
                output_tokens=row.output_tokens, total_tokens=row.input_tokens + row.output_tokens,
                average_duration_ms=round(float(row.average_duration_ns) / 1_000_000, 1),
            )
            for row in model_rows
        ],
        recent=[
            AdminRecentUsage(
                id=event.id, email=email, model=event.model, input_tokens=event.input_tokens,
                output_tokens=event.output_tokens, total_duration_ns=event.total_duration_ns,
                status=event.status, created_at=event.created_at,
            )
            for event, email in recent_rows
        ],
    )


@router.patch("/users/{user_id}", response_model=AdminUserUsage)
async def update_user(user_id: UUID, payload: AdminUserUpdate, admin: UserManager, db: DbSession) -> AdminUserUsage:
    if user_id == admin.id and not payload.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "You cannot disable your own administrator account")
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    target.is_active = payload.is_active
    await db.commit()
    users = await user_usage_rows(db)
    return next(user for user in users if user.id == target.id)


@router.post("/users", response_model=AdminUserUsage, status_code=status.HTTP_201_CREATED)
async def create_user(payload: AdminUserCreate, _: UserManager, db: DbSession) -> AdminUserUsage:
    email = payload.email.lower()
    if await db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email is already registered")
    user = User(email=email, password_hash=hash_password(payload.password), is_active=True, is_admin=False)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return AdminUserUsage(
        id=user.id, email=user.email, is_active=user.is_active, is_admin=user.is_admin,
        input_tokens=0, output_tokens=0, total_tokens=0, request_count=0, last_activity=None,
    )


@router.put("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(user_id: UUID, payload: AdminPasswordReset, _: UserManager, db: DbSession) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.password_hash = hash_password(payload.password)
    await db.commit()


@router.get("/quotas", response_model=list[AdminUserQuota])
async def list_quotas(_: UserManager, db: DbSession) -> list[AdminUserQuota]:
    users = await db.scalars(select(User).order_by(User.email))
    quotas = {quota.user_id: quota for quota in await db.scalars(select(UserQuota))}
    result: list[AdminUserQuota] = []
    for user in users:
        limit = quotas.get(user.id).monthly_token_limit if user.id in quotas else None
        used = await monthly_tokens(db, user.id)
        result.append(AdminUserQuota(
            user_id=user.id, email=user.email, monthly_token_limit=limit, monthly_tokens_used=used,
            remaining_tokens=max(limit - used, 0) if limit is not None else None,
        ))
    return result


@router.put("/users/{user_id}/quota", response_model=AdminUserQuota)
async def set_quota(user_id: UUID, payload: AdminQuotaUpdate, _: UserManager, db: DbSession) -> AdminUserQuota:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    quota = await db.get(UserQuota, user_id)
    if quota is None:
        quota = UserQuota(user_id=user_id, monthly_token_limit=payload.monthly_token_limit)
        db.add(quota)
    else:
        quota.monthly_token_limit = payload.monthly_token_limit
    await db.commit()
    used = await monthly_tokens(db, user_id)
    return AdminUserQuota(
        user_id=user.id, email=user.email, monthly_token_limit=payload.monthly_token_limit,
        monthly_tokens_used=used,
        remaining_tokens=max(payload.monthly_token_limit - used, 0) if payload.monthly_token_limit else None,
    )


@router.get("/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(_: KeyManager, db: DbSession) -> list[ApiKey]:
    result = await db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()).limit(500))
    return list(result)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def generate_api_key(payload: AdminApiKeyCreate, _: KeyManager, db: DbSession) -> ApiKeyCreated:
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
async def revoke_api_key(key_id: UUID, _: KeyManager, db: DbSession) -> None:
    result = await db.execute(
        update(ApiKey).where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC))
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active API key not found")
    await db.commit()


@router.get("/roles", response_model=list[AdminUserRoles])
async def list_roles(_: AdminUser, db: DbSession) -> list[AdminUserRoles]:
    users = await db.scalars(select(User).order_by(User.email))
    return [
        AdminUserRoles(user_id=user.id, email=user.email, roles=sorted(await roles_for_user(db, user)))
        for user in users
    ]


@router.put("/users/{user_id}/roles", response_model=AdminUserRoles)
async def set_roles(user_id: UUID, payload: AdminRoleUpdate, admin: AdminUser, db: DbSession) -> AdminUserRoles:
    requested = set(payload.roles)
    unknown = requested.difference(ROLE_PERMISSIONS)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown role(s): {', '.join(sorted(unknown))}")
    if user_id == admin.id and "admin" not in requested:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "You cannot remove your own admin role")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
    db.add_all(UserRole(user_id=user_id, role=role) for role in requested)
    user.is_admin = "admin" in requested
    await db.commit()
    return AdminUserRoles(user_id=user.id, email=user.email, roles=sorted(requested))
