from typing import Optional
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_deps import require_admin
from app.db.session import get_db
from app.db.models import User, UsageLog
from app.core.structured_logging import get_logger

router = APIRouter()
logger = get_logger("api.admin.stats")

class UsageStats(BaseModel):
    total_requests: int
    total_tokens: int
    total_errors: int
    average_latency_ms: float
    period_start: datetime
    period_end: datetime

@router.get("/usage", response_model=UsageStats)
async def get_usage_stats(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    api_key_id: Optional[UUID] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
):
    """Get usage statistics"""
    if not end_date:
        end_date = datetime.now(timezone.utc)
    if not start_date:
        start_date = end_date - timedelta(days=30)
    
    query = select(
        func.count(UsageLog.id).label("total_requests"),
        func.sum(UsageLog.total_tokens).label("total_tokens"),
        func.count(UsageLog.id).filter(UsageLog.error_message.isnot(None)).label("total_errors"),
        func.avg(UsageLog.latency_ms).label("average_latency_ms")
    ).where(
        UsageLog.created_at >= start_date,
        UsageLog.created_at <= end_date
    )
    
    if api_key_id:
        query = query.where(UsageLog.api_key_id == api_key_id)
    
    result = await db.execute(query)
    stats = result.one()
    
    return UsageStats(
        total_requests=stats.total_requests or 0,
        total_tokens=stats.total_tokens or 0,
        total_errors=stats.total_errors or 0,
        average_latency_ms=float(stats.average_latency_ms or 0),
        period_start=start_date,
        period_end=end_date
    )