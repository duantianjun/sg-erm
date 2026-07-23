# -*- coding: utf-8 -*-
"""仓库源管理 API。

提供仓库源的增删改查。
"""
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import error_response, success
from app.database import get_db
from app.models import AuditLog, RepositorySource, SyncTask, User
from app.services.auth_service import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/sources",
    tags=["sources"],
    dependencies=[Depends(require_admin)],
)


class SourceCreate(BaseModel):
    """创建仓库源请求。"""
    name: str
    url: str
    enabled: bool = True
    priority: int = 100
    sync_interval: int = 3600
    auth_type: str = "none"
    auth_config: dict | None = None
    proxy_url: str | None = None


class SourceUpdate(BaseModel):
    """更新仓库源请求。"""
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    sync_interval: int | None = None
    auth_type: str | None = None
    auth_config: dict | None = None
    proxy_url: str | None = None


@router.get("")
async def list_sources(
    db: AsyncSession = Depends(get_db),
):
    """仓库源列表。"""
    logger.info("[仓库源API] 查询仓库源列表")
    result = await db.execute(
        select(RepositorySource).order_by(RepositorySource.priority)
    )
    sources = result.scalars().all()

    data = [
        {
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "enabled": s.enabled,
            "priority": s.priority,
            "sync_interval": s.sync_interval,
            "last_sync": s.last_sync.isoformat() if s.last_sync else None,
            "last_sync_status": s.last_sync_status,
            "health_status": s.health_status,
            "auth_type": s.auth_type,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in sources
    ]

    logger.info(f"[仓库源API] 返回 {len(data)} 个仓库源")
    return success(data, len(data))


@router.get("/{source_id}")
async def get_source(source_id: str, db: AsyncSession = Depends(get_db)):
    """仓库源详情。"""
    logger.debug(f"[仓库源API] 查询详情 source_id={source_id}")
    source = await db.get(RepositorySource, source_id)
    if not source:
        logger.warning(f"[仓库源API] 仓库源不存在 source_id={source_id}")
        return error_response("仓库源不存在", status_code=404)

    return success({
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "enabled": source.enabled,
        "priority": source.priority,
        "sync_interval": source.sync_interval,
        "last_sync": source.last_sync.isoformat() if source.last_sync else None,
        "last_sync_status": source.last_sync_status,
        "health_status": source.health_status,
        "auth_type": source.auth_type,
        "auth_config": source.auth_config,
        "proxy_url": source.proxy_url,
        "created_at": source.created_at.isoformat() if source.created_at else None,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
    }, 1)


@router.post("")
async def create_source(
    body: SourceCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建仓库源。"""
    logger.info(
        f"[仓库源API] 创建仓库源 name={body.name} url={body.url} "
        f"enabled={body.enabled} priority={body.priority}"
    )
    source = RepositorySource(
        name=body.name,
        url=body.url,
        enabled=body.enabled,
        priority=body.priority,
        sync_interval=body.sync_interval,
        auth_type=body.auth_type,
        auth_config=body.auth_config,
        proxy_url=body.proxy_url,
        health_status="unknown",
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    logger.info(f"[仓库源API] 创建成功 source_id={source.id} name={source.name}")
    return success({
        "id": source.id,
        "name": source.name,
        "url": source.url,
        "enabled": source.enabled,
    }, 1, "创建成功")


@router.put("/{source_id}")
async def update_source(
    source_id: str,
    body: SourceUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新仓库源。"""
    update_data = body.model_dump(exclude_unset=True)
    logger.info(f"[仓库源API] 更新仓库源 source_id={source_id} fields={list(update_data.keys())}")

    source = await db.get(RepositorySource, source_id)
    if not source:
        logger.warning(f"[仓库源API] 更新失败：仓库源不存在 source_id={source_id}")
        return error_response("仓库源不存在", status_code=404)

    for key, value in update_data.items():
        setattr(source, key, value)

    await db.commit()
    await db.refresh(source)

    logger.info(f"[仓库源API] 更新成功 source_id={source_id} name={source.name}")
    return success({"id": source.id}, 1, "更新成功")


@router.delete("/{source_id}")
async def delete_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除仓库源。

    会级联删除关联的同步任务记录。
    如果有运行中的同步任务，会拒绝删除。
    """
    logger.info(f"[仓库源API] 删除仓库源 source_id={source_id}")
    source = await db.get(RepositorySource, source_id)
    if not source:
        logger.warning(f"[仓库源API] 删除失败：仓库源不存在 source_id={source_id}")
        return error_response("仓库源不存在", status_code=404)

    from sqlalchemy import select, delete
    from app.models import SyncTask
    running = await db.scalar(
        select(func.count())
        .select_from(SyncTask)
        .where(SyncTask.source_id == source_id, SyncTask.status == "running")
    )
    if running and running > 0:
        logger.warning(f"[仓库源API] 删除失败：有运行中任务 source_id={source_id} running={running}")
        return error_response("有运行中的同步任务，无法删除")

    await db.execute(
        delete(SyncTask).where(SyncTask.source_id == source_id)
    )

    source_name = source.name
    await db.delete(source)
    await db.commit()

    logger.info(f"[仓库源API] 删除成功 source_id={source_id} name={source_name}")
    return success({"id": source_id}, 1, "删除成功")


@router.post("/health-check")
async def trigger_health_check():
    """手动触发所有仓库源的健康检查。"""
    logger.info("[仓库源API] 手动触发健康检查")
    from app.services.health_checker import run_health_check
    result = await run_health_check()
    logger.info(f"[仓库源API] 健康检查完成 checked={result['checked']}")
    return success(result, 1, f"已检查 {result['checked']} 个源")


@router.post("/aggregate-index")
async def trigger_aggregate():
    """手动触发多源 index.json 聚合。"""
    logger.info("[仓库源API] 手动触发 index.json 聚合")
    from app.services.index_aggregator import build_aggregated_index
    path = await build_aggregated_index()
    if path:
        logger.info(f"[仓库源API] 索引聚合成功 path={path}")
        return success({"path": str(path)}, 1, "索引聚合成功")
    logger.error("[仓库源API] 索引聚合失败")
    return error_response("索引聚合失败，请检查日志")
