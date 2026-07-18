from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AdminApiKeyCreate(BaseModel):
    user_id: UUID
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    key_prefix: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyResponse):
    api_key: str


class AdminUserUsage(BaseModel):
    id: UUID
    email: str
    is_active: bool
    is_admin: bool
    input_tokens: int
    output_tokens: int
    total_tokens: int
    request_count: int
    last_activity: datetime | None


class AdminOverview(BaseModel):
    user_count: int
    active_user_count: int
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    users: list[AdminUserUsage]
