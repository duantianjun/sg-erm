# -*- coding: utf-8 -*-
"""缓存淘汰服务。

三种淘汰策略（按优先级）：
1. 磁盘阈值：使用率超过 cache_max_disk_usage 时，按 LRU 淘汰到 cache_target_disk_usage
2. TTL 清理：超过 cache_ttl_days 未访问的包
3. 版本保留：每个扩展只保留最新 cache_keep_versions 个版本

提供手动触发和定时自动执行。
"""
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, update

from app.config import settings
from app.database import async_session_factory
from app.logging_config import get_task_logger
from app.models import ExtensionBuild, ExtensionVersion, Extension

logger = logging.getLogger(__name__)
task_logger = get_task_logger()


async def get_disk_usage(repo_dir: Path) -> tuple[int, int]:
    """获取磁盘使用情况。

    Returns:
        (used_bytes, total_bytes)
    """
    if not repo_dir.exists():
        return 0, 1

    # 对于 PVC，使用 repo_dir 所在的磁盘
    usage = shutil.disk_usage(str(repo_dir))
    return usage.used, usage.total


def get_usage_percent(used: int, total: int) -> float:
    """计算使用率百分比。"""
    if total == 0:
        return 0.0
    return (used / total) * 100.0


async def evict_by_disk_threshold(repo_dir: Path) -> dict:
    """磁盘阈值淘汰：LRU 策略，按 last_accessed 排序删除。

    直到使用率降到 cache_target_disk_usage 以下。
    """
    used, total = await get_disk_usage(repo_dir)
    current_pct = get_usage_percent(used, total)
    max_pct = settings.cache_max_disk_usage
    target_pct = settings.cache_target_disk_usage

    if current_pct < max_pct:
        return {"evicted": 0, "freed_bytes": 0, "current_pct": round(current_pct, 1)}

    task_logger.info(
        f"[缓存淘汰] 磁盘阈值触发: current_pct={current_pct:.1f}% >= max_pct={max_pct}%, target={target_pct}%"
    )
    evicted = 0
    freed = 0

    async with async_session_factory() as session:
        result = await session.execute(
            select(ExtensionBuild)
            .where(ExtensionBuild.cached == True)
            .order_by(ExtensionBuild.last_accessed.asc().nullsfirst())
        )
        builds = result.scalars().all()

        for build in builds:
            used, total = await get_disk_usage(repo_dir)
            current_pct = get_usage_percent(used, total)
            if current_pct <= target_pct:
                break

            pkg_path = repo_dir / build.package_path
            if pkg_path.exists():
                file_size = pkg_path.stat().st_size
                pkg_path.unlink()
                freed += file_size

            build.cached = False
            build.package_size = 0
            build.package_path = ""
            evicted += 1

        await session.commit()

    task_logger.info(
        f"[缓存淘汰] 磁盘阈值淘汰完成: evicted={evicted} freed={freed} bytes, current_pct={current_pct:.1f}%"
    )
    return {
        "evicted": evicted,
        "freed_bytes": freed,
        "current_pct": round(current_pct, 1),
    }


async def evict_by_ttl(repo_dir: Path) -> dict:
    """TTL 淘汰：删除超过 cache_ttl_days 未访问的缓存包。"""
    ttl_days = settings.cache_ttl_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)

    evicted = 0
    freed = 0

    async with async_session_factory() as session:
        result = await session.execute(
            select(ExtensionBuild)
            .where(
                ExtensionBuild.cached == True,
                (ExtensionBuild.last_accessed < cutoff)
                | (ExtensionBuild.last_accessed.is_(None)),
            )
        )
        builds = result.scalars().all()

        task_logger.info(f"[缓存淘汰] TTL 淘汰开始: ttl_days={ttl_days} candidates={len(builds)}")

        for build in builds:
            pkg_path = repo_dir / build.package_path
            if pkg_path.exists():
                freed += pkg_path.stat().st_size
                pkg_path.unlink()

            build.cached = False
            build.package_size = 0
            build.package_path = ""
            evicted += 1

        await session.commit()

    task_logger.info(
        f"[缓存淘汰] TTL 淘汰完成: evicted={evicted} freed={freed} bytes"
    )
    return {"evicted": evicted, "freed_bytes": freed, "ttl_days": ttl_days}


async def evict_old_versions(repo_dir: Path) -> dict:
    """版本保留淘汰：每个扩展只保留最新 cache_keep_versions 个版本。

    逻辑：
    - 按 extension 分组
    - 每组内按 version 创建时间排序
    - 保留最新 N 个版本，删除旧版本的构建
    """
    keep = settings.cache_keep_versions
    evicted = 0
    freed = 0

    async with async_session_factory() as session:
        extensions = (await session.execute(select(Extension))).scalars().all()

        task_logger.info(
            f"[缓存淘汰] 版本保留淘汰开始: keep_versions={keep} extensions={len(extensions)}"
        )

        for ext in extensions:
            versions = (await session.execute(
                select(ExtensionVersion)
                .where(ExtensionVersion.extension_id == ext.id)
                .order_by(ExtensionVersion.created_at.desc())
            )).scalars().all()

            versions_to_keep = set()
            for i, ver in enumerate(versions):
                if i < keep:
                    versions_to_keep.add(ver.id)
                else:
                    builds = (await session.execute(
                        select(ExtensionBuild)
                        .where(
                            ExtensionBuild.version_id == ver.id,
                            ExtensionBuild.cached == True,
                        )
                    )).scalars().all()

                    for build in builds:
                        pkg_path = repo_dir / build.package_path
                        if pkg_path.exists():
                            freed += pkg_path.stat().st_size
                            pkg_path.unlink()

                        build.cached = False
                        build.package_size = 0
                        build.package_path = ""
                        evicted += 1

        await session.commit()

    task_logger.info(
        f"[缓存淘汰] 版本保留淘汰完成: evicted={evicted} freed={freed} bytes"
    )
    return {"evicted": evicted, "freed_bytes": freed, "keep_versions": keep}


async def run_full_eviction() -> dict:
    """执行完整的缓存淘汰流程。

    按优先级：磁盘阈值 → TTL → 版本保留
    """
    repo_dir = settings.repo_dir
    task_logger.info(f"[缓存淘汰] 开始完整淘汰流程 repo_dir={repo_dir}")

    result = {
        "disk_threshold": await evict_by_disk_threshold(repo_dir),
        "ttl": await evict_by_ttl(repo_dir),
        "old_versions": await evict_old_versions(repo_dir),
    }

    total_evicted = sum(r["evicted"] for r in result.values())
    total_freed = sum(r["freed_bytes"] for r in result.values())
    result["total_evicted"] = total_evicted
    result["total_freed_bytes"] = total_freed

    used, total = await get_disk_usage(repo_dir)
    result["final_disk_usage_pct"] = round(get_usage_percent(used, total), 1)

    task_logger.info(
        f"[缓存淘汰] 完整淘汰完成: total_evicted={total_evicted} "
        f"total_freed={total_freed} bytes final_pct={result['final_disk_usage_pct']}%"
    )
    return result