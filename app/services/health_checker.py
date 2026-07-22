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
from app.models import RepositorySource
from app.services.naming import get_index_url

logger = logging.getLogger(__name__)

# 连续失败次数阈值，达到后标记为 down
CONSECUTIVE_FAILURE_THRESHOLD = 3


async def check_single_source(
    source: RepositorySource,
    timeout: float = 10.0,
) -> tuple[str, float]:
    """检查单个仓库源的健康状态。

    Returns:
        (status, latency_seconds)
    """
    url = get_index_url(source.url)

    try:
        start = time.monotonic()
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.head(url, allow_redirects=True) as resp:
                latency = time.monotonic() - start
                if resp.status < 400:
                    if latency > 5.0:
                        return "degraded", latency
                    return "healthy", latency
                else:
                    return "down", latency
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.debug(f"健康检查失败 {source.name}: {e}")
        return "down", 0.0


async def run_health_check() -> dict:
    """对所有启用的仓库源执行一轮健康检查。

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

    results = []
    for source in sources:
        status, latency = await check_single_source(source)

        # 更新数据库中的健康状态
        async with async_session_factory() as session:
            src = await session.get(RepositorySource, source.id)
            if src:
                old_status = src.health_status
                src.health_status = status

                # 从 down 恢复时记录日志
                if old_status == "down" and status == "healthy":
                    src.last_sync_status = "success"
                    logger.info(f"源 {source.name} 已恢复健康")

                await session.commit()

        results.append({
            "id": source.id,
            "name": source.name,
            "url": source.url,
            "status": status,
            "latency": round(latency, 3),
        })

    return {"checked": len(results), "results": results}


# ─── 调度集成 ─────────────────────────────────────────────

async def _health_check_loop():
    """持续运行的健康检查循环。"""
    # 首次启动延迟 10 秒，等待系统初始化
    await asyncio.sleep(10)

    interval = getattr(settings, "health_check_interval", 60)

    while True:
        try:
            result = await run_health_check()
            if result["checked"] > 0:
                logger.debug(f"健康检查完成: {result['checked']} 个源")
        except Exception as e:
            logger.warning(f"健康检查异常: {e}")

        await asyncio.sleep(interval)


def start_health_checker():
    """启动后台健康检查任务。"""
    asyncio.create_task(_health_check_loop())
    logger.info("仓库源健康检查器已启动")