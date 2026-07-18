import time

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import get_settings


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def check(self, key: str) -> None:
        settings = get_settings()
        bucket = time.time_ns() // 60_000_000_000
        redis_key = f"ratelimit:{key}:{bucket}"
        count = await self.redis.incr(redis_key)
        if count == 1:
            await self.redis.expire(redis_key, 70)
        if count > settings.rate_limit_per_minute:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests. Please retry shortly.",
                headers={"Retry-After": "60"},
            )


async def limit_request(request: Request) -> None:
    user = getattr(request.state, "user", None)
    identity = f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    await request.app.state.rate_limiter.check(identity)
