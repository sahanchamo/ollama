from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import admin, auth, chat, conversations, health, knowledge, tools, usage
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models import Base, User
from app.db.session import SessionLocal, engine
from app.services.ollama import OllamaService
from app.services.rate_limit import RateLimiter

settings = get_settings()
configure_logging(settings.log_level)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.rate_limiter = RateLimiter(app.state.redis)
    app.state.ollama = OllamaService()
    app.state.session_factory = SessionLocal
    # Use Alembic migrations in mature deployments. This bootstrap creates the initial schema.
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
        if settings.bootstrap_admin_email:
            await connection.execute(
                update(User)
                .where(User.email == settings.bootstrap_admin_email.lower())
                .values(is_admin=True)
            )
            await connection.execute(
                text(
                    "INSERT INTO user_roles (user_id, role) "
                    "SELECT id, 'admin' FROM users WHERE email = :email "
                    "ON CONFLICT (user_id, role) DO NOTHING"
                ),
                {"email": settings.bootstrap_admin_email.lower()},
            )
    yield
    await app.state.ollama.close()
    await app.state.redis.aclose()
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan, docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(origin).rstrip("/") for origin in settings.allowed_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    with structlog.contextvars.bound_contextvars(request_id=request_id):
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_request_error", method=request.method, path=request.url.path)
            raise
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": "Invalid request", "errors": exc.errors()})


@app.exception_handler(SQLAlchemyError)
async def database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    log.error("database_error", error=str(exc))
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


app.include_router(health.router)
app.include_router(auth.router, prefix=settings.api_v1_prefix)
app.include_router(chat.router, prefix=settings.api_v1_prefix)
app.include_router(conversations.router, prefix=settings.api_v1_prefix)
app.include_router(usage.router, prefix=settings.api_v1_prefix)
app.include_router(knowledge.router, prefix=settings.api_v1_prefix)
app.include_router(tools.router, prefix=settings.api_v1_prefix)
app.include_router(admin.router, prefix=settings.api_v1_prefix)
