from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


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


class AdminDailyUsage(BaseModel):
    day: date
    request_count: int
    input_tokens: int
    output_tokens: int


class AdminModelUsage(BaseModel):
    model: str
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    average_duration_ms: float


class AdminRecentUsage(BaseModel):
    id: UUID
    email: str
    model: str
    input_tokens: int
    output_tokens: int
    total_duration_ns: int
    status: str
    created_at: datetime


class AdminAnalytics(BaseModel):
    days: int
    active_key_count: int
    revoked_key_count: int
    daily: list[AdminDailyUsage]
    models: list[AdminModelUsage]
    recent: list[AdminRecentUsage]


class AdminUserUpdate(BaseModel):
    is_active: bool


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class AdminPasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class AdminQuotaUpdate(BaseModel):
    monthly_token_limit: int | None = Field(default=None, ge=1_000, le=100_000_000)


class AdminUserQuota(BaseModel):
    user_id: UUID
    email: str
    monthly_token_limit: int | None
    monthly_tokens_used: int
    remaining_tokens: int | None


class AdminRoleUpdate(BaseModel):
    roles: list[str] = Field(min_length=1, max_length=4)


class AdminUserRoles(BaseModel):
    user_id: UUID
    email: str
    roles: list[str]
