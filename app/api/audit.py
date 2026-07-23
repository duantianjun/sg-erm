# -*- coding: utf-8 -*-
"""审计日志 API。

提供审计日志的查询和统计。
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import success
from app.database import get_db
from app.models import AuditLog, User
from app.services.auth_service import require_admin

logger = logging.getLogger(__name__)

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
    logger.info(
        f"[审计API] 查询审计日志 page={page} limit={limit} "
        f"action={action or 'all'} result={result or 'all'} "
        f"start_date={start_date or 'any'} end_date={end_date or 'any'}"
    )
    query = select(AuditLog).order_by(AuditLog.timestamp.desc())

    if action:
        query = query.where(AuditLog.action.contains(action))
    if result:
        query = query.where(AuditLog.result == result)
    if start_date:
        query = query.where(AuditLog.timestamp >= f"{start_date} 00:00:00")
    if end_date:
        query = query.where(AuditLog.timestamp <= f"{end_date} 23:59:59")

    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

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

    logger.info(f"[审计API] 返回 {len(data)} 条审计日志，总计 {total} 条")
    return success(data, total)


@router.get("/stats")
async def audit_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """审计统计（仅管理员）。"""
    logger.info("[审计API] 查询审计统计")
    total = await db.scalar(select(func.count()).select_from(AuditLog)) or 0
    success_count = await db.scalar(
        select(func.count()).where(AuditLog.result == "success")
    ) or 0
    failure_count = await db.scalar(
        select(func.count()).where(AuditLog.result == "failure")
    ) or 0

    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent = await db.scalar(
        select(func.count()).where(AuditLog.timestamp >= since)
    ) or 0

    logger.info(
        f"[审计API] 统计结果 total={total} success={success_count} "
        f"failure={failure_count} recent_24h={recent}"
    )
    return success(
        {
            "total": total,
            "success": success_count,
            "failure": failure_count,
            "recent_24h": recent,
        },
        1,
    )
