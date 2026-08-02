# -*- coding: utf-8 -*-
"""调度器内置任务注册单元测试。

测试 _register_builtin_jobs() 正确注册缓存淘汰和指标收集任务。
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services import scheduler as scheduler_module


@pytest.mark.unit
class TestRegisterBuiltinJobs:
    """内置任务注册。"""

    def test_registers_cache_eviction_job(self):
        """cache_eviction_interval > 0 时注册缓存淘汰任务。"""
        mock_scheduler = MagicMock()
        scheduler_module.scheduler = mock_scheduler

        with patch.object(scheduler_module.settings, "cache_eviction_interval", 3600):
            with patch.object(scheduler_module.settings, "metrics_collect_interval", 0):
                scheduler_module._register_builtin_jobs()

        # 应注册 cache_eviction 任务
        eviction_calls = [
            c for c in mock_scheduler.add_job.call_args_list
            if c.kwargs.get("id") == "cache_eviction"
        ]
        assert len(eviction_calls) == 1
        assert eviction_calls[0].kwargs["id"] == "cache_eviction"
        assert eviction_calls[0].kwargs["replace_existing"] is True

    def test_registers_metrics_collect_job(self):
        """metrics_collect_interval > 0 时注册指标收集任务。"""
        mock_scheduler = MagicMock()
        scheduler_module.scheduler = mock_scheduler

        with patch.object(scheduler_module.settings, "cache_eviction_interval", 0):
            with patch.object(scheduler_module.settings, "metrics_collect_interval", 30):
                scheduler_module._register_builtin_jobs()

        metrics_calls = [
            c for c in mock_scheduler.add_job.call_args_list
            if c.kwargs.get("id") == "metrics_collect"
        ]
        assert len(metrics_calls) == 1
        assert metrics_calls[0].kwargs["id"] == "metrics_collect"
        assert metrics_calls[0].kwargs["replace_existing"] is True

    def test_disabled_when_interval_zero(self):
        """interval=0 时不注册对应任务。"""
        mock_scheduler = MagicMock()
        scheduler_module.scheduler = mock_scheduler

        with patch.object(scheduler_module.settings, "cache_eviction_interval", 0):
            with patch.object(scheduler_module.settings, "metrics_collect_interval", 0):
                scheduler_module._register_builtin_jobs()

        mock_scheduler.add_job.assert_not_called()

    def test_registers_both_jobs(self):
        """两个 interval > 0 时都注册。"""
        mock_scheduler = MagicMock()
        scheduler_module.scheduler = mock_scheduler

        with patch.object(scheduler_module.settings, "cache_eviction_interval", 1800):
            with patch.object(scheduler_module.settings, "metrics_collect_interval", 30):
                scheduler_module._register_builtin_jobs()

        assert mock_scheduler.add_job.call_count == 2
        job_ids = [c.kwargs["id"] for c in mock_scheduler.add_job.call_args_list]
        assert "cache_eviction" in job_ids
        assert "metrics_collect" in job_ids

    def test_cache_eviction_job_calls_run_full_eviction(self):
        """_cache_eviction_job 调用 run_full_eviction。"""
        import asyncio

        async def fake_run():
            return {"total_evicted": 0, "total_freed_bytes": 0}

        with patch(
            "app.services.cache_eviction.run_full_eviction",
            new=fake_run,
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    scheduler_module._cache_eviction_job()
                )
            finally:
                loop.close()

    def test_metrics_collect_job_calls_collect_metrics(self):
        """_metrics_collect_job 调用 collect_metrics。"""
        import asyncio

        async def fake_collect():
            pass

        with patch(
            "app.services.metrics.collect_metrics",
            new=fake_collect,
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    scheduler_module._metrics_collect_job()
                )
            finally:
                loop.close()


@pytest.mark.unit
class TestStartScheduler:
    """start_scheduler 启动流程。"""

    def test_start_registers_builtin_and_sync_jobs(self):
        """start_scheduler 启动时注册内置任务和同步策略。"""
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        scheduler_module.scheduler = mock_scheduler

        with patch.object(scheduler_module, "_register_builtin_jobs") as mock_builtin:
            with patch.object(scheduler_module, "reload_jobs") as mock_reload:
                scheduler_module.start_scheduler()

        mock_scheduler.start.assert_called_once()
        mock_builtin.assert_called_once()
        mock_reload.assert_called_once()

    def test_start_skips_if_already_running(self):
        """调度器已在运行时不重复启动。"""
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        scheduler_module.scheduler = mock_scheduler

        with patch.object(scheduler_module, "_register_builtin_jobs") as mock_builtin:
            with patch.object(scheduler_module, "reload_jobs") as mock_reload:
                scheduler_module.start_scheduler()

        mock_scheduler.start.assert_not_called()
        mock_builtin.assert_not_called()
        mock_reload.assert_not_called()
