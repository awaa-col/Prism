"""
API Key and usage related database models.
"""
from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING
import uuid

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Text,
    ForeignKey, Table, Index
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from app.db.models.base import Base, GUID

if TYPE_CHECKING:
    from .user import User
    from .plugin import Model

api_key_models = Table(
    'api_key_models',
    Base.metadata,
    Column('api_key_id', GUID(), ForeignKey('api_keys.id')),
    Column('model_id', GUID(), ForeignKey('models.id'))
)

class APIKey(Base):
    """API Key model for authentication"""
    __tablename__ = "api_keys"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    key = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    
    rate_limit = Column(Integer, nullable=True)
    rate_limit_period = Column(Integer, default=60)
    
    total_requests = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    
    user: Mapped["User"] = relationship("User", back_populates="api_keys")
    allowed_models: Mapped[List["Model"]] = relationship(secondary=api_key_models, back_populates="api_keys")
    usage_logs: Mapped[List["UsageLog"]] = relationship("UsageLog", back_populates="api_key", cascade="all, delete-orphan")

class UsageLog(Base):
    """Usage logging for analytics and billing"""
    __tablename__ = "usage_logs"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    api_key_id = Column(GUID(), ForeignKey("api_keys.id"), nullable=False)
    model_name = Column(String(100), nullable=False)
    
    request_id = Column(String(100), unique=True, nullable=False)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    
    latency_ms = Column(Integer, nullable=True)
    status_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    
    api_key: Mapped["APIKey"] = relationship("APIKey", back_populates="usage_logs")
    
    __table_args__ = (
        Index('idx_usage_logs_created_at', 'created_at'),
        Index('idx_usage_logs_api_key_created', 'api_key_id', 'created_at'),
    )