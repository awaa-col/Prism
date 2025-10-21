"""
User self-service API Key management endpoints.
"""
from typing import List, Optional
from uuid import UUID
from datetime import datetime, timedelta

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth_deps import require_user
from app.api.deps import get_db
from app.core.security import hash_api_key
from app.db.models import APIKey, Model, User
from app.schemas.base import APIResponseSchema


router = APIRouter()


class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rate_limit: Optional[int] = Field(None, ge=1)
    rate_limit_period: Optional[int] = Field(60, ge=1)
    expires_in_days: Optional[int] = Field(None, ge=1)
    allowed_models: Optional[List[str]] = None


class APIKeyItem(BaseModel):
    id: UUID
    name: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    rate_limit: Optional[int]
    rate_limit_period: int
    allowed_models: List[str] = []

    model_config = ConfigDict(from_attributes=True)


class UserAPIKeyCreateResponse(APIKeyItem):
    key: str  # Only returned on creation


@router.get("/api-keys", response_model=APIResponseSchema[List[APIKeyItem]])
async def list_my_api_keys(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> APIResponseSchema[List[APIKeyItem]]:
    result = await db.execute(
        select(APIKey)
        .options(selectinload(APIKey.allowed_models))
        .where(APIKey.user_id == current_user.id)
        .order_by(APIKey.created_at.desc())
    )
    items = result.scalars().all()

    response_items = [
        APIKeyItem(
            id=k.id,
            name=k.name,
            is_active=k.is_active,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            expires_at=k.expires_at,
            rate_limit=k.rate_limit,
            rate_limit_period=k.rate_limit_period,
            allowed_models=[m.name for m in k.allowed_models],
        )
        for k in items
    ]

    return APIResponseSchema(data=response_items)


@router.post(
    "/api-keys",
    response_model=APIResponseSchema[UserAPIKeyCreateResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_my_api_key(
    data: APIKeyCreateRequest,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> APIResponseSchema[UserAPIKeyCreateResponse]:
    # Generate secure key (returned once)
    api_key_plain = f"sk-{secrets.token_urlsafe(32)}"

    expires_at = None
    if data.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=data.expires_in_days)

    api_key = APIKey(
        key=hash_api_key(api_key_plain),
        name=data.name,
        user_id=current_user.id,
        rate_limit=data.rate_limit,
        rate_limit_period=data.rate_limit_period or 60,
        expires_at=expires_at,
    )

    # Attach allowed models if provided
    if data.allowed_models:
        result = await db.execute(
            select(Model).where(Model.name.in_(data.allowed_models))
        )
        models = result.scalars().all()
        api_key.allowed_models.extend(models)

    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    await db.refresh(api_key, ["allowed_models"])

    response_data = UserAPIKeyCreateResponse.model_validate(api_key)
    response_data.key = api_key_plain
    response_data.allowed_models = [m.name for m in api_key.allowed_models]

    return APIResponseSchema(
        data=response_data,
        message="API key created",
        code=str(status.HTTP_201_CREATED)
    )


@router.delete("/api-keys/{api_key_id}")
async def delete_my_api_key(
    api_key_id: str,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    from uuid import UUID
    try:
        key_uuid = UUID(api_key_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid api_key_id")

    result = await db.execute(
        delete(APIKey).where(APIKey.id == key_uuid, APIKey.user_id == current_user.id)
    )

    if result.rowcount == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")

    await db.commit()
    return {"message": "API key deleted"}





