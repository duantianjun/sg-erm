# -*- coding: utf-8 -*-
"""同步任务 API。

提供同步任务的触发、取消、列表和详情查询。
"""
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import success, success_response, error_response
from app.database import get_db
from app.models import RepositorySource, SyncPolicy, SyncTask, User
from app.services.auth_service import require_admin
from app.services.sync_engine import sync_engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/sync",
    tags=["sync"],
    dependencies=[Depends(require_admin)],
)


class SyncTrigger(BaseModel):
    """触发同步请求。"""
    source_id: str
    policy_id: str | None = None
    dry_run: bool = False


@router.get("/tasks")
async def list_tasks(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: str = Query("", description="状态过滤"),
    source_id: str = Query("", description="仓库源过滤"),
    db: AsyncSession = Depends(get_db),
):
    """同步任务列表。"""
    logger.debug(
        f"[同步API] 查询任务列表 page={page} limit={limit} "
        f"status={status or 'all'} source_id={source_id or 'all'}"
    )
    query = select(SyncTask).order_by(SyncTask.started_at.desc())

    if status:
        query = query.where(SyncTask.status == status)
    if source_id:
        query = query.where(SyncTask.source_id == source_id)

    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # 分页
    query = query.offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    tasks = result.scalars().all()

    data = []
    for t in tasks:
        # 获取源名称
        source = await db.get(RepositorySource, t.source_id)
        source_name = source.name if source else ""

        data.append({
            "id": t.id,
            "source_id": t.source_id,
            "source_name": source_name,
            "policy_id": t.policy_id,
            "status": t.status,
            "total": t.total,
            "downloaded": t.downloaded,
            "failed": t.failed,
            "skipped": t.skipped,
            "error_message": t.error_message,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        })

    logger.info(f"[同步API] 返回 {len(data)} 个任务，总计 {total}")
    return success(data, total)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """同步任务详情。"""
    logger.debug(f"[同步API] 查询任务详情 task_id={task_id}")
    task = await db.get(SyncTask, task_id)
    if not task:
        logger.warning(f"[同步API] 任务不存在 task_id={task_id}")
        return error_response("任务不存在", status_code=404)

    source = await db.get(RepositorySource, task.source_id)
    policy = await db.get(SyncPolicy, task.policy_id) if task.policy_id else None

    data = {
        "id": task.id,
        "source_id": task.source_id,
        "source_name": source.name if source else "",
        "policy_id": task.policy_id,
        "policy_name": policy.name if policy else None,
        "status": task.status,
        "total": task.total,
        "downloaded": task.downloaded,
        "failed": task.failed,
        "skipped": task.skipped,
        "error_message": task.error_message,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "diff_summary": task.diff_summary,
    }

    return success(data, 1)


@router.post("/trigger")
async def trigger_sync(
    body: SyncTrigger,
    db: AsyncSession = Depends(get_db),
):
    """触发同步任务。"""
    logger.info(
        f"[同步API] 触发同步 source_id={body.source_id} "
        f"policy_id={body.policy_id} dry_run={body.dry_run}"
    )
    source = await db.get(RepositorySource, body.source_id)
    if not source:
        logger.warning(f"[同步API] 触发失败：仓库源不存在 source_id={body.source_id}")
        return error_response("仓库源不存在", status_code=404)

    if not source.enabled:
        logger.warning(f"[同步API] 触发失败：仓库源已禁用 source_id={body.source_id}")
        return error_response("仓库源已禁用")

    task = await sync_engine.run(
        source_id=body.source_id,
        policy_id=body.policy_id,
        dry_run=body.dry_run,
    )

    logger.info(f"[同步API] 同步任务已启动 task_id={task.id} source_name={source.name}")
    return success({
        "task_id": task.id,
        "status": task.status,
        "source_name": source.name,
        "dry_run": body.dry_run,
    }, 1, "同步任务已启动")


@router.post("/cancel/{task_id}")
async def cancel_sync(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """取消同步任务。"""
    logger.info(f"[同步API] 取消同步任务 task_id={task_id}")
    task = await db.get(SyncTask, task_id)
    if not task:
        logger.warning(f"[同步API] 取消失败：任务不存在 task_id={task_id}")
        return error_response("任务不存在", status_code=404)

    if task.status not in ("pending", "running"):
        logger.warning(f"[同步API] 取消失败：状态不允许 task_id={task_id} status={task.status}")
        return error_response(f"任务状态为 {task.status}，无法取消")

    cancelled = await sync_engine.cancel(task_id)
    if cancelled:
        logger.info(f"[同步API] 取消请求已发送 task_id={task_id}")
        return success({"task_id": task_id}, 1, "取消请求已发送")
    else:
        logger.warning(f"[同步API] 取消失败：任务已完成或不存在 task_id={task_id}")
        return error_response("任务可能已完成或不存在")


class PolicyCreate(BaseModel):
    """创建同步策略请求。"""
    name: str
    source_id: str
    filters: dict | None = None
    schedule: str | None = None
    enabled: bool = True
    bandwidth_limit: str | None = None
    keep_old_versions: int = 3


class PolicyUpdate(BaseModel):
    """更新同步策略请求。"""
    name: str | None = None
    source_id: str | None = None
    filters: dict | None = None
    schedule: str | None = None
    enabled: bool | None = None
    bandwidth_limit: str | None = None
    keep_old_versions: int | None = None


@router.get("/policies")
async def list_policies(
    db: AsyncSession = Depends(get_db),
):
    """同步策略列表。"""
    logger.info("[同步API] 查询同步策略列表")
    result = await db.execute(
        select(SyncPolicy)
        .order_by(SyncPolicy.name)
    )
    policies = result.scalars().all()

    # 预加载源名称
    source_ids = {p.source_id for p in policies}
    source_names = {}
    if source_ids:
        src_result = await db.execute(
            select(RepositorySource.id, RepositorySource.name)
            .where(RepositorySource.id.in_(source_ids))
        )
        for sid, sname in src_result.all():
            source_names[sid] = sname

    data = []
    for p in policies:
        data.append({
            "id": p.id,
            "name": p.name,
            "source_id": p.source_id,
            "source_name": source_names.get(p.source_id, ""),
            "filters": p.filters,
            "schedule": p.schedule,
            "enabled": p.enabled,
            "bandwidth_limit": p.bandwidth_limit,
            "keep_old_versions": p.keep_old_versions,
        })

    logger.info(f"[同步API] 返回 {len(data)} 个策略")
    return success(data, len(data))


@router.post("/policies")
async def create_policy(
    body: PolicyCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建同步策略。"""
    logger.info(
        f"[同步API] 创建策略 name={body.name} source_id={body.source_id} "
        f"schedule={body.schedule} enabled={body.enabled}"
    )
    source = await db.get(RepositorySource, body.source_id)
    if not source:
        logger.warning(f"[同步API] 创建失败：仓库源不存在 source_id={body.source_id}")
        return error_response("仓库源不存在", status_code=404)

    policy = SyncPolicy(
        name=body.name,
        source_id=body.source_id,
        filters=body.filters,
        schedule=body.schedule,
        enabled=body.enabled,
        bandwidth_limit=body.bandwidth_limit,
        keep_old_versions=body.keep_old_versions,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)

    from app.services.scheduler import reload_jobs
    reload_jobs()

    logger.info(f"[同步API] 创建成功 policy_id={policy.id} name={policy.name}")
    return success({"id": policy.id}, 1, "创建成功")


@router.put("/policies/{policy_id}")
async def update_policy(
    policy_id: str,
    body: PolicyUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新同步策略。"""
    update_data = body.model_dump(exclude_unset=True)
    logger.info(f"[同步API] 更新策略 policy_id={policy_id} fields={list(update_data.keys())}")

    policy = await db.get(SyncPolicy, policy_id)
    if not policy:
        logger.warning(f"[同步API] 更新失败：策略不存在 policy_id={policy_id}")
        return error_response("策略不存在", status_code=404)

    for key, value in update_data.items():
        setattr(policy, key, value)

    await db.commit()
    await db.refresh(policy)

    from app.services.scheduler import reload_jobs
    reload_jobs()

    logger.info(f"[同步API] 更新成功 policy_id={policy_id}")
    return success({"id": policy_id}, 1, "更新成功")


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除同步策略。"""
    logger.info(f"[同步API] 删除策略 policy_id={policy_id}")
    policy = await db.get(SyncPolicy, policy_id)
    if not policy:
        logger.warning(f"[同步API] 删除失败：策略不存在 policy_id={policy_id}")
        return error_response("策略不存在", status_code=404)

    policy_name = policy.name
    await db.delete(policy)
    await db.commit()

    from app.services.scheduler import reload_jobs
    reload_jobs()

    logger.info(f"[同步API] 删除成功 policy_id={policy_id} name={policy_name}")
    return success({"id": policy_id}, 1, "删除成功")
