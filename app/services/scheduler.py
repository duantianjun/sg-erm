# -*- coding: utf-8 -*-
"""同步任务调度器。

使用 APScheduler 的 AsyncIOScheduler 实现定时同步。
- 启动时从数据库加载所有启用的 SyncPolicy
- 根据 schedule (Cron 表达式) 注册定时任务
- 触发时调用 SyncEngine.run()
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.database import async_session_factory
from app.logging_config import get_task_logger
from app.models import SyncPolicy
from app.services.sync_engine import sync_engine

logger = logging.getLogger(__name__)
task_logger = get_task_logger()

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

    asyncio.create_task(_load())


def start_scheduler():
    """启动调度器。"""
    if not scheduler.running:
        scheduler.start()
        reload_jobs()
        task_logger.info("[调度器] 定时同步调度器已启动")


def stop_scheduler():
    """停止调度器。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        task_logger.info("[调度器] 定时同步调度器已停止")
