from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.db.models import UsageEvent
from app.schemas.usage import UsageSummary

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/me", response_model=UsageSummary)
async def my_usage(user: CurrentUser, db: DbSession) -> UsageSummary:
    totals = await db.execute(
        select(
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.count(UsageEvent.id),
        ).where(UsageEvent.user_id == user.id)
    )
    input_tokens, output_tokens, request_count = totals.one()
    events = await db.scalars(
        select(UsageEvent)
        .where(UsageEvent.user_id == user.id)
        .order_by(UsageEvent.created_at.desc())
        .limit(100)
    )
    return UsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        request_count=request_count,
        events=list(events),
    )
