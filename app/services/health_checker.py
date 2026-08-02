# -*- coding: utf-8 -*-
"""仓库源自动健康检查。

定期对所有启用的仓库源发送 HEAD 请求探测 index.json 可达性，
更新 RepositorySource.health_status 字段。

健康状态：
- healthy: HEAD 请求返回 2xx
- degraded: 响应慢 (>5s) 或间歇性失败
- down: 连接失败或 4xx/5xx

检查周期由配置 SG_ERM_HEALTH_CHECK_INTERVAL 控制（默认 60 秒）。
"""
import asyncio
import logging
import time
from datetime import datetime

import aiohttp
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory
from app.logging_config import get_task_logger
from app.models import RepositorySource
from app.services.naming import get_index_url

_background_tasks: set[asyncio.Task] = set()

logger = logging.getLogger(__name__)
task_logger = get_task_logger()

# 连续失败次数阈值，达到后标记为 down（从集中配置读取）
CONSECUTIVE_FAILURE_THRESHOLD = settings.health_consecutive_failure_threshold


async def check_single_source(
    source: RepositorySource,
    timeout: float | None = None,
) -> tuple[str, float]:
    """检查单个仓库源的健康状态。

    超时、降级阈值均从集中配置 settings 读取。

    Returns:
        (status, latency_seconds)
    """
    url = get_index_url(source.url)

    # 从配置中获取默认超时和降级阈值
    actual_timeout = timeout if timeout is not None else settings.health_check_timeout
    degraded_threshold = settings.health_degraded_latency_sec

    try:
        start = time.monotonic()
        timeout_obj = aiohttp.ClientTimeout(total=actual_timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.head(url, allow_redirects=True) as resp:
                latency = time.monotonic() - start
                if resp.status < 400:
                    if latency > degraded_threshold:
                        return "degraded", latency
                    return "healthy", latency
                else:
                    return "down", latency
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.debug(f"健康检查失败 {source.name}: {e}")
        return "down", 0.0


async def run_health_check() -> dict:
    """对所有启用的仓库源执行一轮健康检查。

    连续失败计数逻辑：
    - 检查成功 → consecutive_failures=0, health_status=healthy
    - 检查返回 degraded → consecutive_failures 不变, health_status=degraded
    - 检查返回 down → consecutive_failures+=1
      - consecutive_failures >= 阈值 → health_status=down
      - consecutive_failures < 阈值 → health_status=degraded（间歇性失败）

    Returns:
        {"checked": N, "results": [{"id": ..., "name": ..., "status": ..., "latency": ...}]}
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(RepositorySource)
            .where(RepositorySource.enabled == True)  # noqa: E712
            .order_by(RepositorySource.priority)
        )
        sources = result.scalars().all()

    threshold = CONSECUTIVE_FAILURE_THRESHOLD
    results = []

    for source in sources:
        check_status, latency = await check_single_source(source)

        # 根据连续失败计数决定最终状态
        async with async_session_factory() as session:
            src = await session.get(RepositorySource, source.id)
            if src:
                old_status = src.health_status

                if check_status == "healthy":
                    src.consecutive_failures = 0
                    src.health_status = "healthy"
                elif check_status == "down":
                    src.consecutive_failures += 1
                    if src.consecutive_failures >= threshold:
                        src.health_status = "down"
                    else:
                        src.health_status = "degraded"
                else:  # degraded
                    src.health_status = "degraded"

                # 从 down 恢复时记录日志
                if old_status == "down" and src.health_status == "healthy":
                    src.last_sync_status = "success"
                    task_logger.info(f"[健康检查] 源 {source.name} 已恢复健康")

                await session.commit()

                final_status = src.health_status
            else:
                final_status = check_status

        results.append({
            "id": source.id,
            "name": source.name,
            "url": source.url,
            "status": final_status,
            "latency": round(latency, 3),
        })

    return {"checked": len(results), "results": results}


# ─── 调度集成 ─────────────────────────────────────────────

async def _health_check_loop():
    """持续运行的健康检查循环（参数从集中配置读取）。"""
    # 首次启动延迟，等待系统初始化
    await asyncio.sleep(settings.health_initial_delay_sec)

    interval = settings.health_check_interval

    while True:
        try:
            result = await run_health_check()
            if result["checked"] > 0:
                healthy = sum(1 for r in result["results"] if r["status"] == "healthy")
                down = result["checked"] - healthy
                task_logger.info(
                    f"[健康检查] 完成 {result['checked']} 个源: "
                    f"healthy={healthy}, down={down}"
                )
        except Exception as e:
            task_logger.warning(f"[健康检查] 异常: {e}")

        await asyncio.sleep(interval)


def start_health_checker():
    """启动后台健康检查任务。"""
    task = asyncio.create_task(_health_check_loop())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task_logger.info("[健康检查] 仓库源健康检查器已启动")