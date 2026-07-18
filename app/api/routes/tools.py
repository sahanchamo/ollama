from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_permission
from app.db.models import User
from app.schemas.tools import DomainLookupRequest, DomainLookupResponse
from app.services.domain_lookup import lookup_domain

router = APIRouter(prefix="/tools", tags=["security tools"])
ToolOperator = Annotated[User, Depends(require_permission("tools.run"))]


@router.post("/domain-lookup", response_model=DomainLookupResponse)
async def domain_lookup(payload: DomainLookupRequest, _: ToolOperator) -> DomainLookupResponse:
    try:
        domain, records, hint, scope = await lookup_domain(payload.domain)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return DomainLookupResponse(domain=domain, records=records, provider_hint=hint, provider_hint_scope=scope)
