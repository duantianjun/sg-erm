"""审计日志 API。

提供审计日志的查询和统计。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import success
from app.database import get_db
from app.models import AuditLog, User
from app.services.auth_service import require_admin

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("/logs")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    action: str = Query("", description="动作过滤"),
    result: str = Query("", description="结果过滤: success/failure"),
    start_date: str = Query("", description="开始日期 YYYY-MM-DD"),
    end_date: str = Query("", description="结束日期 YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """审计日志列表（仅管理员）。"""
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())

    if action:
        query = query.where(AuditLog.action.contains(action))
    if result:
        query = query.where(AuditLog.result == result)
    if start_date:
        query = query.where(AuditLog.timestamp >= f"{start_date} 00:00:00")
    if end_date:
        query = query.where(AuditLog.timestamp <= f"{end_date} 23:59:59")

    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # 分页
    query = query.offset((page - 1) * limit).limit(limit)
    result_obj = await db.execute(query)
    logs = result_obj.scalars().all()

    data = [
        {
            "id": log.id,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "actor": log.actor,
            "action": log.action,
            "resource": log.resource,
            "detail": log.detail,
            "result": log.result,
            "client_ip": log.client_ip,
        }
        for log in logs
    ]

    return success(data, total)


@router.get("/stats")
async def audit_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """审计统计（仅管理员）。"""
    total = await db.scalar(select(func.count()).select_from(AuditLog)) or 0
    success_count = await db.scalar(
        select(func.count()).where(AuditLog.result == "success")
    ) or 0
    failure_count = await db.scalar(
        select(func.count()).where(AuditLog.result == "failure")
    ) or 0

    # 最近 24 小时
    from datetime import datetime, timedelta

    since = datetime.utcnow() - timedelta(hours=24)
    recent = await db.scalar(
        select(func.count()).where(AuditLog.timestamp >= since)
    ) or 0

    return success(
        {
            "total": total,
            "success": success_count,
            "failure": failure_count,
            "recent_24h": recent,
        },
        1,
    )
