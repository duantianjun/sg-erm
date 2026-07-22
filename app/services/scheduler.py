"""同步任务调度器。

使用 APScheduler 的 AsyncIOScheduler 实现定时同步。
- 启动时从数据库加载所有启用的 SyncPolicy
- 根据 schedule (Cron 表达式) 注册定时任务
- 触发时调用 SyncEngine.run()
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.database import async_session_factory
from app.models import SyncPolicy
from app.services.sync_engine import sync_engine

scheduler = AsyncIOScheduler()


async def sync_job(policy_id: str):
    """定时同步任务的执行函数。"""
    async with async_session_factory() as session:
        policy = await session.get(SyncPolicy, policy_id)
        if not policy or not policy.enabled:
            return

        source_id = policy.source_id
        try:
            await sync_engine.run(
                source_id=source_id,
                policy_id=policy_id,
                dry_run=False,
            )
        except Exception as e:
            print(f"[Scheduler] 同步任务失败 policy={policy_id}: {e}")


def reload_jobs():
    """重新加载所有启用的同步策略为定时任务。

    在调度器启动时调用，或策略变更后调用。
    """
    import asyncio

    # 移除所有旧的 sync_ 任务
    for job in scheduler.get_jobs():
        if job.id.startswith("sync_"):
            job.remove()

    # 从数据库加载策略
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
                    # 使用 CronTrigger 解析 Cron 表达式
                    trigger = CronTrigger.from_crontab(policy.schedule)
                    job_id = f"sync_{policy.id}"
                    scheduler.add_job(
                        sync_job,
                        trigger=trigger,
                        id=job_id,
                        replace_existing=True,
                        args=[policy.id],
                    )
                except Exception as e:
                    print(f"[Scheduler] 无法解析 Cron 表达式 '{policy.schedule}' for policy={policy.id}: {e}")

    asyncio.create_task(_load())


def start_scheduler():
    """启动调度器。"""
    if not scheduler.running:
        scheduler.start()
        reload_jobs()
        print("[Scheduler] 定时同步调度器已启动")


def stop_scheduler():
    """停止调度器。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[Scheduler] 定时同步调度器已停止")
