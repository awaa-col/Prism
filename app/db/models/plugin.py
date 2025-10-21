"""
Plugin and model related database models.
"""
from datetime import datetime, timezone
from typing import List, Dict, Any, TYPE_CHECKING
import uuid

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Text, JSON,
    ForeignKey, Float, UniqueConstraint
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.db.models.base import Base, GUID

if TYPE_CHECKING:
    from .user import User
    from .api_key import APIKey

class Model(Base):
    """Model configuration"""
    __tablename__ = "models"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False, index=True)
    plugin_name = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    
    max_tokens = Column(Integer, nullable=True)
    supports_streaming = Column(Boolean, default=True)
    supports_functions = Column(Boolean, default=False)
    
    input_cost_per_1k = Column(Float, nullable=True)
    output_cost_per_1k = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    api_keys: Mapped[List["APIKey"]] = relationship("APIKey", secondary="api_key_models", back_populates="allowed_models")

class Credential(Base):
    """Credentials for external services"""
    __tablename__ = "credentials"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    type = Column(String(50), nullable=False)
    plugin_name = Column(String(50), nullable=False)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    
    encrypted_data = Column(Text, nullable=False)
    
    metadata_json = Column("metadata", JSON, nullable=True)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)
    
    user: Mapped["User"] = relationship("User", back_populates="credentials")
    
    __table_args__ = (
        UniqueConstraint('name', 'plugin_name', 'user_id', name='_credential_unique'),
    )
    
    def get_decrypted_data(self) -> Dict[str, Any]:
        from app.core.encryption import decrypt_credential, get_credential_encryption
        try:
            encryption = get_credential_encryption()
            encrypted_data_str = str(self.encrypted_data)
            if encryption.is_encrypted(encrypted_data_str):
                return decrypt_credential(encrypted_data_str)
            else:
                import json
                try:
                    return json.loads(encrypted_data_str)
                except json.JSONDecodeError:
                    raise RuntimeError("Invalid credential data format")
        except Exception as e:
            raise RuntimeError(f"Failed to decrypt credential data: {e}")
    
    def update_encrypted_data(self, new_data: Dict[str, Any]) -> None:
        from app.core.encryption import encrypt_credential
        try:
            self.encrypted_data = encrypt_credential(new_data)
            self.updated_at = datetime.now(timezone.utc)
        except Exception as e:
            raise RuntimeError(f"Failed to encrypt credential data: {e}")
    
    def migrate_to_encrypted(self) -> bool:
        from app.core.encryption import get_credential_encryption, migrate_plaintext_credential
        try:
            encryption = get_credential_encryption()
            encrypted_data_str = str(self.encrypted_data)
            if not encryption.is_encrypted(encrypted_data_str):
                self.encrypted_data = migrate_plaintext_credential(encrypted_data_str)
                self.updated_at = datetime.now(timezone.utc)
                return True
            else:
                return False
        except Exception as e:
            raise RuntimeError(f"Failed to migrate credential data: {e}")