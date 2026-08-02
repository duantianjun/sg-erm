# -*- coding: utf-8 -*-
"""健康检查连续失败计数逻辑集成测试。

测试 run_health_check() 的连续失败计数行为：
- 检查成功 → consecutive_failures 清零，health_status=healthy
- 检查返回 down → consecutive_failures+1
  - 未达阈值 → health_status=degraded
  - 达阈值 → health_status=down
- 检查返回 degraded → health_status=degraded
"""
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import RepositorySource


async def _run_health_check_with_test_db(db_engine):
    """运行健康检查，使用测试 DB。

    health_checker 模块级导入了 async_session_factory，
    需临时替换为测试 factory。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    test_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    with patch("app.services.health_checker.async_session_factory", test_factory):
        from app.services.health_checker import run_health_check
        return await run_health_check()


@pytest.mark.integration
class TestConsecutiveFailureCounting:
    """连续失败计数逻辑。"""

    async def test_healthy_resets_counter(self, client, db_session, db_engine):
        """检查成功时 consecutive_failures 清零。"""
        source = RepositorySource(
            name="official",
            url="https://ext.stackgres.io/repo",
            enabled=True,
            health_status="down",
            consecutive_failures=3,  # 之前已经失败3次
        )
        db_session.add(source)
        await db_session.commit()

        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("healthy", 0.1),
        ):
            result = await _run_health_check_with_test_db(db_engine)

        assert result["checked"] == 1
        assert result["results"][0]["status"] == "healthy"

        await db_session.refresh(source)
        assert source.consecutive_failures == 0
        assert source.health_status == "healthy"

    async def test_single_down_below_threshold_is_degraded(self, client, db_session, db_engine):
        """单次失败未达阈值 → degraded（不是 down）。"""
        source = RepositorySource(
            name="test-source",
            url="https://example.com/repo",
            enabled=True,
            health_status="healthy",
            consecutive_failures=0,
        )
        db_session.add(source)
        await db_session.commit()

        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("down", 0.0),
        ):
            await _run_health_check_with_test_db(db_engine)

        await db_session.refresh(source)
        assert source.consecutive_failures == 1
        # 阈值默认为 3，1 < 3 → degraded
        assert source.health_status == "degraded"

    async def test_failures_reaching_threshold_becomes_down(self, client, db_session, db_engine):
        """连续失败达到阈值 → down。"""
        source = RepositorySource(
            name="failing-source",
            url="https://fail.example.com/repo",
            enabled=True,
            health_status="degraded",
            consecutive_failures=2,  # 再失败1次就达阈值3
        )
        db_session.add(source)
        await db_session.commit()

        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("down", 0.0),
        ):
            await _run_health_check_with_test_db(db_engine)

        await db_session.refresh(source)
        assert source.consecutive_failures == 3
        assert source.health_status == "down"

    async def test_degraded_does_not_increment_counter(self, client, db_session, db_engine):
        """检查返回 degraded → consecutive_failures 不变。"""
        source = RepositorySource(
            name="slow-source",
            url="https://slow.example.com/repo",
            enabled=True,
            health_status="healthy",
            consecutive_failures=1,
        )
        db_session.add(source)
        await db_session.commit()

        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("degraded", 6.0),
        ):
            await _run_health_check_with_test_db(db_engine)

        await db_session.refresh(source)
        assert source.consecutive_failures == 1  # 不变
        assert source.health_status == "degraded"

    async def test_recovery_from_down(self, client, db_session, db_engine):
        """从 down 恢复到 healthy。"""
        source = RepositorySource(
            name="recovered-source",
            url="https://recovered.example.com/repo",
            enabled=True,
            health_status="down",
            consecutive_failures=5,
        )
        db_session.add(source)
        await db_session.commit()

        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("healthy", 0.2),
        ):
            await _run_health_check_with_test_db(db_engine)

        await db_session.refresh(source)
        assert source.consecutive_failures == 0
        assert source.health_status == "healthy"

    async def test_multiple_failures_then_recovery(self, client, db_session, db_engine):
        """多轮失败后恢复的完整周期。"""
        source = RepositorySource(
            name="cycle-source",
            url="https://cycle.example.com/repo",
            enabled=True,
            health_status="healthy",
            consecutive_failures=0,
        )
        db_session.add(source)
        await db_session.commit()

        # 第1轮：失败 → degraded (1 < 3)
        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("down", 0.0),
        ):
            await _run_health_check_with_test_db(db_engine)
        await db_session.refresh(source)
        assert source.consecutive_failures == 1
        assert source.health_status == "degraded"

        # 第2轮：失败 → degraded (2 < 3)
        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("down", 0.0),
        ):
            await _run_health_check_with_test_db(db_engine)
        await db_session.refresh(source)
        assert source.consecutive_failures == 2
        assert source.health_status == "degraded"

        # 第3轮：失败 → down (3 >= 3)
        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("down", 0.0),
        ):
            await _run_health_check_with_test_db(db_engine)
        await db_session.refresh(source)
        assert source.consecutive_failures == 3
        assert source.health_status == "down"

        # 第4轮：恢复 → healthy (0)
        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("healthy", 0.1),
        ):
            await _run_health_check_with_test_db(db_engine)
        await db_session.refresh(source)
        assert source.consecutive_failures == 0
        assert source.health_status == "healthy"


@pytest.mark.integration
class TestHealthCheckMultiSource:
    """多源健康检查。"""

    async def test_only_checks_enabled_sources(self, client, db_session, db_engine):
        """只检查 enabled=True 的源。"""
        enabled = RepositorySource(
            name="enabled", url="https://enabled.com/repo",
            enabled=True, health_status="unknown",
        )
        disabled = RepositorySource(
            name="disabled", url="https://disabled.com/repo",
            enabled=False, health_status="unknown",
        )
        db_session.add_all([enabled, disabled])
        await db_session.commit()

        with patch(
            "app.services.health_checker.check_single_source",
            new_callable=AsyncMock,
            return_value=("healthy", 0.1),
        ) as mock_check:
            result = await _run_health_check_with_test_db(db_engine)

        assert result["checked"] == 1
        assert mock_check.call_count == 1
