from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageEvent, UserQuota


def month_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def monthly_tokens(db: AsyncSession, user_id: object) -> int:
    value = await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens), 0)).where(
            UsageEvent.user_id == user_id, UsageEvent.created_at >= month_start()
        )
    )
    return int(value or 0)


async def enforce_quota(db: AsyncSession, user_id: object) -> None:
    quota = await db.get(UserQuota, user_id)
    if quota is None or quota.monthly_token_limit is None:
        return
    used = await monthly_tokens(db, user_id)
    if used >= quota.monthly_token_limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Monthly token limit reached. Contact an administrator.",
            headers={"Retry-After": "2592000"},
        )
