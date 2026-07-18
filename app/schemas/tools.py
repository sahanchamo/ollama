from pydantic import BaseModel, Field


class DomainLookupRequest(BaseModel):
    domain: str = Field(min_length=1, max_length=253, examples=["example.com"])


class DomainLookupResponse(BaseModel):
    domain: str
    records: dict[str, list[str]]
    provider_hint: str | None = None
    provider_hint_scope: str | None = None
