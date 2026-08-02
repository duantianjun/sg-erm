# -*- coding: utf-8 -*-
"""后台任务调度器。

使用 APScheduler 的 AsyncIOScheduler 实现定时任务：
- 同步策略：从数据库加载启用的 SyncPolicy，按 Cron 表达式注册
- 缓存淘汰：按固定间隔执行 run_full_eviction()
- 指标收集：按固定间隔执行 collect_metrics()
"""
import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.logging_config import get_task_logger
from app.models import SyncPolicy
from app.services.sync_engine import sync_engine

logger = logging.getLogger(__name__)
task_logger = get_task_logger()

_background_tasks: set[asyncio.Task] = set()

scheduler = AsyncIOScheduler()


async def sync_job(policy_id: str):
    """定时同步任务的执行函数。"""
    task_logger.info(f"[定时任务] 开始执行同步 policy_id={policy_id}")
    async with async_session_factory() as session:
        policy = await session.get(SyncPolicy, policy_id)
        if not policy or not policy.enabled:
            task_logger.warning(f"[定时任务] policy_id={policy_id} 不存在或已禁用，跳过")
            return

        source_id = policy.source_id
        try:
            await sync_engine.run(
                source_id=source_id,
                policy_id=policy_id,
                dry_run=False,
            )
            task_logger.info(f"[定时任务] 同步完成 policy_id={policy_id}")
        except Exception as e:
            task_logger.error(f"[定时任务] 同步失败 policy_id={policy_id}: {e}", exc_info=True)


def reload_jobs():
    """重新加载所有启用的同步策略为定时任务。

    在调度器启动时调用，或策略变更后调用。
    """
    import asyncio

    for job in scheduler.get_jobs():
        if job.id.startswith("sync_"):
            job.remove()

    async def _load():
        async with async_session_factory() as session:
            result = await session.execute(
                select(SyncPolicy).where(SyncPolicy.enabled == True)
            )
            policies = result.scalars().all()

            for policy in policies:
                if not policy.schedule:
                    continue
                try:
                    trigger = CronTrigger.from_crontab(policy.schedule)
                    job_id = f"sync_{policy.id}"
                    scheduler.add_job(
                        sync_job,
                        trigger=trigger,
                        id=job_id,
                        replace_existing=True,
                        args=[policy.id],
                    )
                    task_logger.info(f"[调度器] 已注册定时任务 {job_id} schedule={policy.schedule}")
                except Exception as e:
                    task_logger.error(f"[调度器] 无法解析 Cron 表达式 '{policy.schedule}' policy={policy.id}: {e}")

    task = asyncio.create_task(_load())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _cache_eviction_job():
    """定时缓存淘汰执行函数。"""
    from app.services.cache_eviction import run_full_eviction

    try:
        result = await run_full_eviction()
        task_logger.info(
            f"[定时任务] 缓存淘汰完成: "
            f"evicted={result['total_evicted']} freed={result['total_freed_bytes']} bytes"
        )
    except Exception as e:
        task_logger.error(f"[定时任务] 缓存淘汰失败: {e}", exc_info=True)


async def _metrics_collect_job():
    """定时指标收集执行函数。"""
    from app.services.metrics import collect_metrics

    try:
        await collect_metrics()
    except Exception as e:
        task_logger.debug(f"[定时任务] 指标收集失败: {e}")


def _register_builtin_jobs():
    """注册内置定时任务（缓存淘汰、指标收集）。

    在调度器启动时调用，间隔由集中配置 settings 控制。
    设置为 0 表示禁用对应任务。
    """
    # 缓存淘汰
    eviction_interval = settings.cache_eviction_interval
    if eviction_interval > 0:
        scheduler.add_job(
            _cache_eviction_job,
            trigger=IntervalTrigger(seconds=eviction_interval),
            id="cache_eviction",
            replace_existing=True,
        )
        task_logger.info(
            f"[调度器] 已注册缓存淘汰定时任务 interval={eviction_interval}s"
        )

    # 指标收集
    metrics_interval = settings.metrics_collect_interval
    if metrics_interval > 0:
        scheduler.add_job(
            _metrics_collect_job,
            trigger=IntervalTrigger(seconds=metrics_interval),
            id="metrics_collect",
            replace_existing=True,
        )
        task_logger.info(
            f"[调度器] 已注册指标收集定时任务 interval={metrics_interval}s"
        )


def start_scheduler():
    """启动调度器。"""
    if not scheduler.running:
        scheduler.start()
        _register_builtin_jobs()
        reload_jobs()
        task_logger.info("[调度器] 后台任务调度器已启动")


def stop_scheduler():
    """停止调度器。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        task_logger.info("[调度器] 定时同步调度器已停止")
