from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, delete

from app.api.auth_deps import require_admin
from app.db.session import get_db, AsyncSession
from app.db.models import User, Credential
from app.core.structured_logging import get_logger

router = APIRouter()
logger = get_logger("api.admin.credentials")

class CredentialCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., description="Credential type: api_key, cookie, oauth")
    plugin_name: str = Field(..., min_length=1, max_length=50)
    data: Dict[str, Any] = Field(..., description="Credential data")
    metadata: Optional[Dict[str, Any]] = None
    expires_in_days: Optional[int] = Field(None, ge=1)


class CredentialResponse(BaseModel):
    id: UUID
    name: str
    type: str
    plugin_name: str
    user_id: UUID
    is_active: bool
    metadata: Optional[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    
    model_config = ConfigDict(from_attributes=True)

@router.post("/", response_model=CredentialResponse)
async def create_credential(
    cred_data: CredentialCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Create a new credential"""
    from app.core.encryption import encrypt_credential
    
    try:
        encrypted_data = encrypt_credential(cred_data.data)
    except Exception as e:
        logger.error(f"Failed to encrypt credential data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to encrypt credential data: {str(e)}"
        )
    
    expires_at = None
    if cred_data.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=cred_data.expires_in_days)
    
    credential = Credential(
        name=cred_data.name,
        type=cred_data.type,
        plugin_name=cred_data.plugin_name,
        user_id=current_user.id,
        encrypted_data=encrypted_data,
        metadata=cred_data.metadata,
        expires_at=expires_at
    )
    
    db.add(credential)
    await db.commit()
    await db.refresh(credential)
    
    logger.info(
        "Credential created",
        credential_id=str(credential.id),
        type=credential.type,
        plugin=credential.plugin_name,
        created_by=str(current_user.id)
    )
    
    return credential


@router.get("/", response_model=List[CredentialResponse])
async def list_credentials(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    plugin_name: Optional[str] = None,
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100
):
    """List credentials. Admins can see all credentials."""
    query = select(Credential)
    
    if user_id:
        query = query.where(Credential.user_id == user_id)

    if plugin_name:
        query = query.where(Credential.plugin_name == plugin_name)
    
    result = await db.execute(
        query
        .offset(skip)
        .limit(limit)
        .order_by(Credential.created_at.desc())
    )
    
    return result.scalars().all()


@router.delete("/{credential_id}")
async def delete_credential(
    credential_id: UUID,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db)
):
    """Delete a credential. Admins can delete any credential."""
    result = await db.execute(
        delete(Credential).where(Credential.id == credential_id)
    )
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found"
        )
    
    await db.commit()
    
    logger.info(
        "Credential deleted",
        credential_id=str(credential_id),
        deleted_by=str(current_user.id)
    )
    
    return {"message": "Credential deleted successfully"}