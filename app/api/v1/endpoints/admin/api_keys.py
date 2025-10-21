from typing import List, Optional
from datetime import datetime, timedelta, timezone
from uuid import UUID
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.api.auth_deps import require_admin
from app.db.session import get_db, AsyncSession
from app.db.models import User, APIKey, Model
from app.core.security import hash_api_key
from app.core.structured_logging import get_logger

router = APIRouter()
logger = get_logger("api.admin.api_keys")

# Request/Response models
class APIKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rate_limit: Optional[int] = Field(None, ge=1)
    rate_limit_period: Optional[int] = Field(60, ge=1)
    expires_in_days: Optional[int] = Field(None, ge=1)
    allowed_models: Optional[List[str]] = None


class APIKeyResponse(BaseModel):
    id: UUID
    key: Optional[str] = None  # 仅创建时返回一次明文
    name: str
    user_id: UUID
    is_active: bool
    rate_limit: Optional[int]
    rate_limit_period: int
    total_requests: int
    total_tokens: int
    created_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]
    allowed_models: List[str]
    
    model_config = ConfigDict(from_attributes=True)

# API Key management endpoints
@router.post("/", response_model=APIKeyResponse)
async def create_api_key(
    key_data: APIKeyCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new API key"""
    # Generate secure key
    api_key_str = f"sk-{secrets.token_urlsafe(32)}"
    
    # Calculate expiration
    expires_at = None
    if key_data.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=key_data.expires_in_days)
    
    # Create API key
    api_key = APIKey(
        key=hash_api_key(api_key_str),
        name=key_data.name,
        user_id=current_user.id,
        rate_limit=key_data.rate_limit,
        rate_limit_period=key_data.rate_limit_period,
        expires_at=expires_at
    )
    
    # Add allowed models if specified
    if key_data.allowed_models:
        result = await db.execute(
            select(Model).where(Model.name.in_(key_data.allowed_models))
        )
        models = result.scalars().all()
        api_key.allowed_models.extend(models)
    
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    
    # Load relationships for response
    await db.refresh(api_key, ["allowed_models"])
    
    logger.info(
        "API key created",
        api_key_id=str(api_key.id),
        created_by=str(current_user.id)
    )
    
    # Convert allowed models to string list for response
    response = APIKeyResponse.model_validate(api_key, from_attributes=True)
    response.allowed_models = [m.name for m in api_key.allowed_models if m.name]
    response.key = api_key_str
    return response


@router.get("/", response_model=List[APIKeyResponse])
async def list_api_keys(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100
):
    """List API keys"""
    query = select(APIKey).options(selectinload(APIKey.allowed_models))
    
    if user_id:
        query = query.where(APIKey.user_id == user_id)
    
    result = await db.execute(
        query
        .offset(skip)
        .limit(limit)
        .order_by(APIKey.created_at.desc())
    )
    
    api_keys = result.scalars().all()
    
    responses = []
    for api_key in api_keys:
        response = APIKeyResponse.model_validate(api_key, from_attributes=True)
        response.allowed_models = [m.name for m in api_key.allowed_models if m.name]
        responses.append(response)
    
    return responses


@router.delete("/{api_key_id}")
async def delete_api_key(
    api_key_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete an API key"""
    result = await db.execute(
        delete(APIKey).where(APIKey.id == api_key_id)
    )
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )
    
    await db.commit()
    
    logger.info(
        "API key deleted",
        api_key_id=str(api_key_id),
        deleted_by=str(current_user.id)
    )
    
    return {"message": "API key deleted successfully"}