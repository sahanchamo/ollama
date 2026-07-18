from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    app_name: str = "Ollama Gateway"
    environment: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    secret_key: str = Field(min_length=32)
    access_token_expire_minutes: int = Field(default=60, ge=5, le=1440)
    database_url: str
    redis_url: str
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_timeout_seconds: float = Field(default=180, ge=10, le=600)
    allowed_origins: list[AnyHttpUrl] = []
    rate_limit_per_minute: int = Field(default=30, ge=1, le=1000)
    max_prompt_chars: int = Field(default=20000, ge=100, le=200000)
    log_level: str = "INFO"
    rag_enabled: bool = True
    rag_embedding_model: str = "nomic-embed-text"
    rag_embedding_dimensions: int = Field(default=768, ge=64, le=4096)
    rag_top_k: int = Field(default=4, ge=1, le=12)
    rag_chunk_size: int = Field(default=1200, ge=200, le=5000)
    rag_chunk_overlap: int = Field(default=200, ge=0, le=1000)
    rag_max_upload_bytes: int = Field(default=10_485_760, ge=1024, le=104_857_600)
    bootstrap_admin_email: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
