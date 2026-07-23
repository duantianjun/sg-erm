# -*- coding: utf-8 -*-
"""仪表盘 API。

提供统计数据：扩展总数、包总数、磁盘用量、同步状态、缓存管理。
"""
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import success
from app.config import settings
from app.database import get_db
from app.models import (
    Extension,
    ExtensionBuild,
    GlobalWhitelist,
    RepositorySource,
    SyncTask,
    User,
)
from app.services.auth_service import require_admin, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_auth),
):
    """仪表盘统计数据。"""
    # 扩展总数
    ext_count = await db.scalar(
        select(func.count()).select_from(Extension)
    ) or 0

    # 自定义扩展数
    custom_ext_count = await db.scalar(
        select(func.count()).select_from(Extension).where(Extension.is_custom == True)  # noqa: E712
    ) or 0

    # 包总数（构建数）
    build_count = await db.scalar(
        select(func.count()).select_from(ExtensionBuild)
    ) or 0

    # 已缓存的包数
    cached_count = await db.scalar(
        select(func.count()).select_from(ExtensionBuild).where(ExtensionBuild.cached == True)  # noqa: E712
    ) or 0

    # 仓库源数
    source_count = await db.scalar(
        select(func.count()).select_from(RepositorySource)
    ) or 0

    # 启用的仓库源数
    enabled_source_count = await db.scalar(
        select(func.count()).select_from(RepositorySource).where(RepositorySource.enabled == True)  # noqa: E712
    ) or 0

    # 白名单条目数
    whitelist_count = await db.scalar(
        select(func.count()).select_from(GlobalWhitelist)
    ) or 0

    # 同步任务统计
    total_tasks = await db.scalar(
        select(func.count()).select_from(SyncTask)
    ) or 0

    running_tasks = await db.scalar(
        select(func.count()).select_from(SyncTask).where(SyncTask.status == "running")
    ) or 0

    # 最近 5 个同步任务
    recent_result = await db.execute(
        select(SyncTask).order_by(SyncTask.started_at.desc()).limit(5)
    )
    recent_tasks = []
    for t in recent_result.scalars().all():
        source = await db.get(RepositorySource, t.source_id)
        recent_tasks.append({
            "id": t.id,
            "source_name": source.name if source else "",
            "status": t.status,
            "total": t.total,
            "downloaded": t.downloaded,
            "failed": t.failed,
            "started_at": t.started_at.isoformat() if t.started_at else None,
        })

    # 磁盘用量
    repo_dir = Path(settings.repo_dir)
    disk_usage = _get_disk_usage(repo_dir)

    logger.debug(
        f"[仪表盘API] 统计: ext={ext_count} pkg={build_count} cached={cached_count} "
        f"sources={enabled_source_count}/{source_count} tasks_running={running_tasks}"
    )

    data = {
        "extensions": {
            "total": ext_count,
            "custom": custom_ext_count,
        },
        "packages": {
            "total": build_count,
            "cached": cached_count,
        },
        "sources": {
            "total": source_count,
            "enabled": enabled_source_count,
        },
        "whitelist": {
            "total": whitelist_count,
        },
        "sync": {
            "total_tasks": total_tasks,
            "running": running_tasks,
            "recent": recent_tasks,
        },
        "disk": disk_usage,
        "proxy_mode": settings.proxy_mode,
    }

    return success(data, 1)


def _get_disk_usage(path: Path) -> dict:
    """获取磁盘用量统计。"""
    if not path.exists():
        return {
            "total_bytes": 0,
            "used_bytes": 0,
            "free_bytes": 0,
            "usage_percent": 0,
            "file_count": 0,
        }

    # 文件系统使用情况
    usage = shutil.disk_usage(str(path))

    # 仓库目录下文件总大小
    total_size = 0
    file_count = 0
    for f in path.rglob("*"):
        if f.is_file():
            total_size += f.stat().st_size
            file_count += 1

    return {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "usage_percent": round(usage.used / usage.total * 100, 1) if usage.total > 0 else 0,
        "repo_size_bytes": total_size,
        "file_count": file_count,
    }


@router.post("/cache/evict")
async def evict_cache(
    mode: str = "full",
    _: User = Depends(require_admin),
):
    """手动触发缓存淘汰。

    mode:
    - full: 全部策略（磁盘阈值 + TTL + 版本保留）
    - disk: 仅磁盘阈值
    - ttl: 仅 TTL
    - versions: 仅版本保留
    """
    from app.services.cache_eviction import (
        evict_by_disk_threshold,
        evict_by_ttl,
        evict_old_versions,
        run_full_eviction,
    )

    repo_dir = settings.repo_dir

    logger.info(f"[仪表盘API] 管理员手动触发缓存淘汰 mode={mode}")

    if mode == "full":
        result = await run_full_eviction()
    elif mode == "disk":
        result = {"disk_threshold": await evict_by_disk_threshold(repo_dir)}
    elif mode == "ttl":
        result = {"ttl": await evict_by_ttl(repo_dir)}
    elif mode == "versions":
        result = {"old_versions": await evict_old_versions(repo_dir)}
    else:
        logger.warning(f"[仪表盘API] 缓存淘汰失败：未知模式 mode={mode}")
        result = {"error": f"未知模式: {mode}"}

    # 附加当前磁盘用量
    result["disk_after"] = _get_disk_usage(repo_dir)

    logger.info(f"[仪表盘API] 缓存淘汰完成 result={result}")
    return success(result, 1, "缓存淘汰完成")
