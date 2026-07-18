from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from datetime import UTC, datetime

from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, hash_api_key
from app.db.models import ApiKey, User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    api_key: Annotated[str | None, Depends(api_key_header)],
) -> User:
    key: ApiKey | None = None
    if credentials:
        user_id = decode_access_token(credentials.credentials)
        user = await db.scalar(select(User).where(User.id == user_id))
    elif api_key:
        key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(api_key)))
        if key is None or key.revoked_at or (key.expires_at and key.expires_at <= datetime.now(UTC)):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid, revoked, or expired API key")
        user = await db.scalar(select(User).where(User.id == key.user_id))
        key.last_used_at = datetime.now(UTC)
        await db.commit()
    else:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is unavailable")
    request.state.user = user
    request.state.api_key_id = key.id if key else None
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
