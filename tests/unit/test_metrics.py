# -*- coding: utf-8 -*-
"""metrics 服务单元测试。

覆盖：
- collect_metrics：从 DB + 文件系统收集 Prometheus 指标
- metrics_response：生成 Prometheus 格式响应

DB：内存 SQLite（StaticPool），patch app.database.async_session_factory 为测试 factory。
settings.repo_dir 通过 patch app.services.metrics.settings 替换为 SimpleNamespace。
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.responses import Response

from app.database import Base
from app.models import (  # noqa: F401  触发所有模型注册到 Base.metadata
    Extension,
    ExtensionBuild,
    ExtensionVersion,
    Publisher,
    RepositorySource,
)
from app.services import metrics as metrics_module


# ─── DB / 配置 fixture ──────────────────────────────────────────


@pytest_asyncio.fixture
async def db_engine():
    """函数级内存 SQLite 引擎，建表后 yield，测完 drop+dispose。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def test_factory(db_engine):
    """测试用 async session factory。"""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(test_factory):
    """单测试用 AsyncSession。"""
    async with test_factory() as session:
        yield session


@pytest.fixture
def patch_factory(test_factory):
    """patch app.database.async_session_factory 为测试 factory。

    collect_metrics 内部 `from app.database import async_session_factory`，
    patch app.database 模块属性即可生效。
    """
    import app.database as db_module
    with patch.object(db_module, "async_session_factory", test_factory):
        yield


@pytest.fixture
def repo_dir(tmp_path):
    """临时仓库根目录。"""
    d = tmp_path / "repo"
    d.mkdir()
    return d


@pytest.fixture
def patch_settings(repo_dir):
    """patch app.services.metrics.settings 为带 repo_dir 的 SimpleNamespace。"""
    fake_settings = SimpleNamespace(repo_dir=repo_dir)
    with patch.object(metrics_module, "settings", fake_settings):
        yield repo_dir


# ─── 数据构造辅助 ────────────────────────────────────────────────


async def _make_publisher(db_session, name=None):
    pub = Publisher(name=name or f"pub-{uuid.uuid4().hex[:8]}")
    db_session.add(pub)
    await db_session.commit()
    await db_session.refresh(pub)
    return pub


async def _make_extension(db_session, publisher, name=None, is_custom=False):
    ext = Extension(
        name=name or f"ext-{uuid.uuid4().hex[:8]}",
        publisher_id=publisher.id,
        is_custom=is_custom,
    )
    db_session.add(ext)
    await db_session.commit()
    await db_session.refresh(ext)
    return ext


async def _make_version(db_session, ext, version="1.0.0"):
    ver = ExtensionVersion(extension_id=ext.id, version=version, channel="stable")
    db_session.add(ver)
    await db_session.commit()
    await db_session.refresh(ver)
    return ver


async def _make_build(db_session, version, *, cached=False, package_size=100):
    build = ExtensionBuild(
        version_id=version.id,
        postgres_version="16.4",
        arch="x86_64",
        os="linux",
        flavor="pg",
        package_path=f"pkg/{uuid.uuid4().hex[:8]}.tar",
        package_size=package_size,
        cached=cached,
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)
    return build


async def _make_source(db_session, *, enabled=True, name=None):
    src = RepositorySource(
        name=name or f"src-{uuid.uuid4().hex[:8]}",
        url=f"https://example.com/{uuid.uuid4().hex[:8]}",
        enabled=enabled,
    )
    db_session.add(src)
    await db_session.commit()
    await db_session.refresh(src)
    return src


# ─── collect_metrics ────────────────────────────────────────────


@pytest.mark.unit
class TestCollectMetrics:
    """collect_metrics：从 DB + 文件系统收集指标。"""

    async def test_empty_db_sets_zero_gauges(self, patch_factory, patch_settings):
        """空数据库 → 所有 Gauge 设为 0。"""
        await metrics_module.collect_metrics()

        assert metrics_module.extensions_total._value.get() == 0
        assert metrics_module.extensions_custom_total._value.get() == 0
        assert metrics_module.packages_total._value.get() == 0
        assert metrics_module.packages_cached_total._value.get() == 0
        assert metrics_module.sources_total._value.get() == 0
        assert metrics_module.sources_enabled._value.get() == 0

    async def test_db_counts_set_correctly(
        self, db_session, patch_factory, patch_settings
    ):
        """有数据 → Gauge 值正确设置。"""
        pub = await _make_publisher(db_session)
        # 2 个扩展，1 个自定义
        await _make_extension(db_session, pub, is_custom=False)
        await _make_extension(db_session, pub, is_custom=True)
        # 第 3 个扩展，用于挂版本和构建
        ext = await _make_extension(db_session, pub)
        ver = await _make_version(db_session, ext)
        # 3 个 build，2 个 cached
        await _make_build(db_session, ver, cached=True, package_size=100)
        await _make_build(db_session, ver, cached=True, package_size=200)
        await _make_build(db_session, ver, cached=False, package_size=300)
        # 2 个 source，1 个 enabled
        await _make_source(db_session, enabled=True)
        await _make_source(db_session, enabled=False)

        await metrics_module.collect_metrics()

        assert metrics_module.extensions_total._value.get() == 3
        assert metrics_module.extensions_custom_total._value.get() == 1
        assert metrics_module.packages_total._value.get() == 3
        assert metrics_module.packages_cached_total._value.get() == 2
        assert metrics_module.sources_total._value.get() == 2
        assert metrics_module.sources_enabled._value.get() == 1

    async def test_repo_dir_not_exist_sets_zero_disk(self, patch_factory, tmp_path):
        """repo_dir 不存在 → disk_usage_percent=0, repo_size_bytes=0, repo_file_count=0。"""
        nonexistent = tmp_path / "does_not_exist"
        fake_settings = SimpleNamespace(repo_dir=nonexistent)
        with patch.object(metrics_module, "settings", fake_settings):
            await metrics_module.collect_metrics()

        assert metrics_module.disk_usage_percent._value.get() == 0
        assert metrics_module.repo_size_bytes._value.get() == 0
        assert metrics_module.repo_file_count._value.get() == 0

    async def test_repo_dir_with_files_calculates_size(
        self, patch_factory, patch_settings, repo_dir
    ):
        """repo_dir 存在 → 正确计算文件大小和数量。"""
        # 创建 3 个文件（含子目录）
        (repo_dir / "f1.tar").write_bytes(b"x" * 100)
        (repo_dir / "sub").mkdir()
        (repo_dir / "sub" / "f2.tar").write_bytes(b"y" * 200)
        (repo_dir / "f3.sha256").write_bytes(b"z" * 50)

        await metrics_module.collect_metrics()

        assert metrics_module.repo_size_bytes._value.get() == 350
        assert metrics_module.repo_file_count._value.get() == 3
        assert metrics_module.disk_usage_percent._value.get() >= 0


# ─── metrics_response ───────────────────────────────────────────


@pytest.mark.unit
class TestMetricsResponse:
    """metrics_response：生成 Prometheus 格式响应。"""

    async def test_returns_response_with_correct_media_type(self):
        """正常调用（有事件循环）→ 返回 Response，content 非空，media_type 正确。"""
        with patch.object(metrics_module, "collect_metrics", AsyncMock()):
            resp = metrics_module.metrics_response()
            # 让背景任务有机会完成，避免 loop 关闭时出现 pending 警告
            await asyncio.sleep(0)

        assert isinstance(resp, Response)
        assert resp.media_type == "text/plain; version=0.0.4; charset=utf-8"
        assert len(resp.body) > 0
        body = resp.body.decode("utf-8") if isinstance(resp.body, bytes) else resp.body
        assert "sg_erm_" in body

    def test_no_event_loop_returns_response(self):
        """无事件循环 → 不抛异常，返回 Response。

        在同步上下文中调用（无运行中的事件循环），metrics_response 应
        捕获 RuntimeError 并正常返回 Response。
        """
        resp = metrics_module.metrics_response()

        assert isinstance(resp, Response)
        assert resp.media_type == "text/plain; version=0.0.4; charset=utf-8"
        assert len(resp.body) > 0
