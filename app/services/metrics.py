# -*- coding: utf-8 -*-
"""Prometheus 监控指标。

暴露 /metrics 端点，提供以下指标：
- sg_erm_extensions_total: 扩展总数
- sg_erm_packages_cached_total: 已缓存包数
- sg_erm_sync_tasks_total: 同步任务总数（按状态 label 分类）
- sg_erm_disk_usage_percent: 磁盘使用率百分比
- sg_erm_repo_size_bytes: 仓库文件总大小
- sg_erm_http_requests_total: HTTP 请求总数
- sg_erm_http_request_duration_seconds: 请求延迟
"""
import shutil
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY
from starlette.responses import Response

from app.config import settings


# ─── 自定义指标 ───────────────────────────────────────

# 扩展
extensions_total = Gauge(
    "sg_erm_extensions_total",
    "Total number of extensions",
)

extensions_custom_total = Gauge(
    "sg_erm_extensions_custom_total",
    "Number of custom extensions",
)

# 包
packages_cached_total = Gauge(
    "sg_erm_packages_cached_total",
    "Number of cached packages",
)

packages_total = Gauge(
    "sg_erm_packages_total",
    "Total number of package builds",
)

# 同步任务
sync_tasks_total = Counter(
    "sg_erm_sync_tasks_total",
    "Total number of sync tasks",
    ["status"],
)

sync_packages_downloaded = Counter(
    "sg_erm_sync_packages_downloaded_total",
    "Total packages downloaded during sync",
)

sync_packages_failed = Counter(
    "sg_erm_sync_packages_failed_total",
    "Total packages failed during sync",
)

# 代理缓存
proxy_requests_total = Counter(
    "sg_erm_proxy_requests_total",
    "Total proxy requests",
    ["status"],  # HIT / MISS / 404
)

# 磁盘
disk_usage_percent = Gauge(
    "sg_erm_disk_usage_percent",
    "Disk usage percentage",
)

repo_size_bytes = Gauge(
    "sg_erm_repo_size_bytes",
    "Total size of repository files",
)

repo_file_count = Gauge(
    "sg_erm_repo_file_count",
    "Number of files in repository",
)

# 仓库源
sources_total = Gauge(
    "sg_erm_sources_total",
    "Total number of repository sources",
)

sources_enabled = Gauge(
    "sg_erm_sources_enabled_total",
    "Number of enabled repository sources",
)

# HTTP 请求（由中间件更新）
http_requests_total = Counter(
    "sg_erm_http_requests_total",
    "Total HTTP requests",
    ["method", "path_template", "status_code"],
)

http_request_duration_seconds = Histogram(
    "sg_erm_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path_template"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


async def collect_metrics() -> None:
    """从数据库和文件系统收集最新指标值。

    由定时任务调用（每 30 秒）。
    """
    from sqlalchemy import func, select

    from app.database import async_session_factory
    from app.models import Extension, ExtensionBuild, RepositorySource, SyncTask

    async with async_session_factory() as session:
        # 扩展数
        ext_total = await session.scalar(
            select(func.count()).select_from(Extension)
        ) or 0
        extensions_total.set(ext_total)

        custom_total = await session.scalar(
            select(func.count()).select_from(Extension)
            .where(Extension.is_custom == True)  # noqa: E712
        ) or 0
        extensions_custom_total.set(custom_total)

        # 包数
        pkg_total = await session.scalar(
            select(func.count()).select_from(ExtensionBuild)
        ) or 0
        packages_total.set(pkg_total)

        cached = await session.scalar(
            select(func.count()).select_from(ExtensionBuild)
            .where(ExtensionBuild.cached == True)  # noqa: E712
        ) or 0
        packages_cached_total.set(cached)

        # 仓库源
        src_total = await session.scalar(
            select(func.count()).select_from(RepositorySource)
        ) or 0
        sources_total.set(src_total)

        enabled = await session.scalar(
            select(func.count()).select_from(RepositorySource)
            .where(RepositorySource.enabled == True)  # noqa: E712
        ) or 0
        sources_enabled.set(enabled)

    # 磁盘和文件系统指标
    repo_dir = settings.repo_dir
    if repo_dir.exists():
        usage = shutil.disk_usage(str(repo_dir))
        disk_usage_percent.set(round(usage.used / usage.total * 100, 1) if usage.total > 0 else 0)

        total_size = 0
        file_count = 0
        for f in repo_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
                file_count += 1
        repo_size_bytes.set(total_size)
        repo_file_count.set(file_count)
    else:
        disk_usage_percent.set(0)
        repo_size_bytes.set(0)
        repo_file_count.set(0)


def metrics_response() -> Response:
    """生成 Prometheus 格式的 metrics 响应。"""
    from asyncio import create_task

    # 异步收集指标（非阻塞，使用上次收集的结果）
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(collect_metrics())
    except RuntimeError:
        pass

    return Response(
        content=generate_latest(REGISTRY),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )