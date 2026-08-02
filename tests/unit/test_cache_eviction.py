# -*- coding: utf-8 -*-
"""cache_eviction 服务单元测试。

覆盖：
- get_disk_usage / get_usage_percent：纯函数
- evict_by_disk_threshold：磁盘阈值 LRU 淘汰
- evict_by_ttl：TTL 淘汰（含 last_accessed=None）
- evict_old_versions：版本保留淘汰
- run_full_eviction：完整流程汇总与调用顺序

DB：内存 SQLite（StaticPool），patch 模块级 async_session_factory 与 settings
的缓存淘汰相关字段。ExtensionBuild.package_path 为相对路径，实际文件位于
repo_dir / package_path。
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (  # noqa: F401  触发所有模型注册到 Base.metadata
    Extension,
    ExtensionBuild,
    ExtensionVersion,
    Publisher,
)
import app.services.cache_eviction as ce


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
    """测试用 async session factory（与 db_session 共享同一内存库）。"""
    return async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(test_factory):
    """单测试用 AsyncSession。"""
    async with test_factory() as session:
        yield session


@pytest.fixture
def patch_factory(test_factory):
    """patch cache_eviction 模块的 async_session_factory 为测试 factory。"""
    with patch.object(ce, "async_session_factory", test_factory):
        yield


@pytest.fixture
def eviction_settings():
    """patch cache_eviction.settings 的缓存淘汰相关字段（默认值）。

    单测可在其上再嵌套 patch.object 覆盖某个字段。
    """
    defaults = {
        "cache_max_disk_usage": 80,
        "cache_target_disk_usage": 70,
        "cache_ttl_days": 7,
        "cache_keep_versions": 3,
        "cache_eviction_interval": 3600,
    }
    patches = [patch.object(ce.settings, k, v) for k, v in defaults.items()]
    for p in patches:
        p.start()
    yield defaults
    for p in patches:
        p.stop()


@pytest.fixture
def repo_dir(tmp_path):
    """临时仓库根目录。"""
    d = tmp_path / "repo"
    d.mkdir()
    return d


# ─── 数据构造辅助 ────────────────────────────────────────────────


async def _make_publisher(db_session, name=None):
    pub = Publisher(name=name or f"pub-{uuid.uuid4().hex[:8]}")
    db_session.add(pub)
    await db_session.commit()
    await db_session.refresh(pub)
    return pub


async def _make_extension(db_session, publisher, name=None):
    ext = Extension(name=name or f"ext-{uuid.uuid4().hex[:8]}", publisher_id=publisher.id)
    db_session.add(ext)
    await db_session.commit()
    await db_session.refresh(ext)
    return ext


async def _make_version(db_session, ext, version="1.0.0", created_at=None):
    ver = ExtensionVersion(extension_id=ext.id, version=version, channel="stable")
    if created_at is not None:
        ver.created_at = created_at
    db_session.add(ver)
    await db_session.commit()
    await db_session.refresh(ver)
    return ver


async def _make_build(
    db_session,
    repo_dir,
    version,
    package_path,
    *,
    cached=True,
    last_accessed=None,
    package_size=100,
):
    """创建一条 cached ExtensionBuild 并在 repo_dir 下生成真实包文件。"""
    pkg = repo_dir / package_path
    pkg.parent.mkdir(parents=True, exist_ok=True)
    pkg.write_bytes(b"x" * package_size)
    build = ExtensionBuild(
        version_id=version.id,
        postgres_version="16.4",
        arch="x86_64",
        os="linux",
        flavor="pg",
        package_path=package_path,
        package_size=package_size,
        cached=cached,
        last_accessed=last_accessed,
    )
    db_session.add(build)
    await db_session.commit()
    await db_session.refresh(build)
    return build


# ─── get_disk_usage ─────────────────────────────────────────────


@pytest.mark.unit
class TestGetDiskUsage:
    async def test_nonexistent_dir_returns_zero(self, tmp_path):
        """目录不存在 → (0, 1)。"""
        used, total = await ce.get_disk_usage(tmp_path / "does_not_exist")
        assert (used, total) == (0, 1)

    async def test_existing_dir_returns_real_usage(self, tmp_path):
        """目录存在 → 返回实际磁盘使用 (used, total)。"""
        used, total = await ce.get_disk_usage(tmp_path)
        assert isinstance(used, int)
        assert isinstance(total, int)
        assert total > 0
        assert 0 <= used <= total


# ─── get_usage_percent ──────────────────────────────────────────


@pytest.mark.unit
class TestGetUsagePercent:
    def test_total_zero_returns_zero(self):
        """total=0 → 0.0（避免除零）。"""
        assert ce.get_usage_percent(100, 0) == 0.0

    def test_normal_calculation(self):
        """(used/total)*100。"""
        assert ce.get_usage_percent(50, 200) == 25.0
        assert ce.get_usage_percent(80, 100) == 80.0

    def test_full_usage(self):
        assert ce.get_usage_percent(100, 100) == 100.0


# ─── evict_by_disk_threshold ────────────────────────────────────


@pytest.mark.unit
class TestEvictByDiskThreshold:
    async def test_below_threshold_no_eviction(
        self, repo_dir, patch_factory, eviction_settings
    ):
        """当前使用率 < max_pct → 不淘汰。"""
        # 10% < 80%
        with patch.object(ce, "get_disk_usage", new=AsyncMock(return_value=(100, 1000))):
            result = await ce.evict_by_disk_threshold(repo_dir)
        assert result["evicted"] == 0
        assert result["freed_bytes"] == 0
        assert result["current_pct"] == 10.0

    async def test_evicts_lru_build_then_stops_at_target(
        self, repo_dir, db_session, patch_factory, eviction_settings
    ):
        """使用率 >= max_pct → 按 LRU 删最久未访问的 build，降到 target 后停止。"""
        pub = await _make_publisher(db_session)
        ext = await _make_extension(db_session, pub)
        ver = await _make_version(db_session, ext)
        old_time = datetime.now(timezone.utc) - timedelta(days=30)
        recent_time = datetime.now(timezone.utc) - timedelta(days=1)
        # build1 较久未访问（应先被淘汰）
        build1 = await _make_build(
            db_session, repo_dir, ver, "pkg/old.tar",
            last_accessed=old_time, package_size=100,
        )
        # build2 最近访问
        build2 = await _make_build(
            db_session, repo_dir, ver, "pkg/recent.tar",
            last_accessed=recent_time, package_size=200,
        )

        # 首次 get_disk_usage 触发淘汰（90% >= 80%）；
        # 循环 iter1 仍高（85% > 70）→ 删 build1；
        # 循环 iter2 回落（50% <= 70）→ break，build2 保留
        mock_du = AsyncMock(side_effect=[(900, 1000), (850, 1000), (500, 1000)])
        with patch.object(ce, "get_disk_usage", new=mock_du):
            result = await ce.evict_by_disk_threshold(repo_dir)

        assert result["evicted"] == 1
        assert result["freed_bytes"] == 100
        assert not (repo_dir / "pkg/old.tar").exists()
        assert (repo_dir / "pkg/recent.tar").exists()

        await db_session.refresh(build1)
        await db_session.refresh(build2)
        assert build1.cached is False
        assert build1.package_size == 0
        assert build1.package_path == ""
        assert build2.cached is True

    async def test_evicts_all_when_usage_stays_high(
        self, repo_dir, db_session, patch_factory, eviction_settings
    ):
        """使用率持续高于 target → 删除全部 cached builds。"""
        pub = await _make_publisher(db_session)
        ext = await _make_extension(db_session, pub)
        ver = await _make_version(db_session, ext)
        b1 = await _make_build(db_session, repo_dir, ver, "p1.tar", package_size=50)
        b2 = await _make_build(db_session, repo_dir, ver, "p2.tar", package_size=60)

        # 每次都返回 90% > target 70
        with patch.object(ce, "get_disk_usage", new=AsyncMock(return_value=(900, 1000))):
            result = await ce.evict_by_disk_threshold(repo_dir)

        assert result["evicted"] == 2
        assert result["freed_bytes"] == 110
        assert not (repo_dir / "p1.tar").exists()
        assert not (repo_dir / "p2.tar").exists()


# ─── evict_by_ttl ───────────────────────────────────────────────


@pytest.mark.unit
class TestEvictByTtl:
    async def test_no_expired_packages(
        self, repo_dir, db_session, patch_factory, eviction_settings
    ):
        """无过期包 → evicted=0。"""
        pub = await _make_publisher(db_session)
        ext = await _make_extension(db_session, pub)
        ver = await _make_version(db_session, ext)
        fresh = datetime.now(timezone.utc) - timedelta(days=1)
        await _make_build(db_session, repo_dir, ver, "fresh.tar", last_accessed=fresh)

        result = await ce.evict_by_ttl(repo_dir)

        assert result["evicted"] == 0
        assert result["freed_bytes"] == 0
        assert result["ttl_days"] == 7

    async def test_evicts_expired_and_null_accessed(
        self, repo_dir, db_session, patch_factory, eviction_settings
    ):
        """过期包（last_accessed < cutoff）与 last_accessed=None 都被淘汰。"""
        pub = await _make_publisher(db_session)
        ext = await _make_extension(db_session, pub)
        ver = await _make_version(db_session, ext)
        old = datetime.now(timezone.utc) - timedelta(days=30)
        fresh = datetime.now(timezone.utc) - timedelta(days=1)
        b_old = await _make_build(
            db_session, repo_dir, ver, "old.tar", last_accessed=old, package_size=100
        )
        b_null = await _make_build(
            db_session, repo_dir, ver, "null.tar", last_accessed=None, package_size=80
        )
        b_fresh = await _make_build(
            db_session, repo_dir, ver, "fresh.tar", last_accessed=fresh, package_size=90
        )

        result = await ce.evict_by_ttl(repo_dir)

        assert result["evicted"] == 2  # old + null
        assert result["freed_bytes"] == 180  # 100 + 80
        assert not (repo_dir / "old.tar").exists()
        assert not (repo_dir / "null.tar").exists()
        assert (repo_dir / "fresh.tar").exists()

        await db_session.refresh(b_old)
        await db_session.refresh(b_null)
        await db_session.refresh(b_fresh)
        assert b_old.cached is False
        assert b_null.cached is False
        assert b_fresh.cached is True


# ─── evict_old_versions ─────────────────────────────────────────


@pytest.mark.unit
class TestEvictOldVersions:
    async def test_keep_within_limit_no_eviction(
        self, repo_dir, db_session, patch_factory, eviction_settings
    ):
        """版本数 <= keep → 不淘汰。"""
        pub = await _make_publisher(db_session)
        ext = await _make_extension(db_session, pub)
        v1 = await _make_version(
            db_session, ext, "1.0", datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        v2 = await _make_version(
            db_session, ext, "2.0", datetime(2026, 2, 1, tzinfo=timezone.utc)
        )
        await _make_build(db_session, repo_dir, v1, "v1.tar", package_size=100)
        await _make_build(db_session, repo_dir, v2, "v2.tar", package_size=100)

        result = await ce.evict_old_versions(repo_dir)

        assert result["evicted"] == 0
        assert result["freed_bytes"] == 0
        assert result["keep_versions"] == 3

    async def test_evicts_old_versions_beyond_keep(
        self, repo_dir, db_session, patch_factory, eviction_settings
    ):
        """版本数 > keep → 淘汰旧版本的 cached builds。"""
        pub = await _make_publisher(db_session)
        ext = await _make_extension(db_session, pub)
        v_old = await _make_version(
            db_session, ext, "1.0", datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        v_mid = await _make_version(
            db_session, ext, "2.0", datetime(2026, 2, 1, tzinfo=timezone.utc)
        )
        v_new = await _make_version(
            db_session, ext, "3.0", datetime(2026, 3, 1, tzinfo=timezone.utc)
        )
        v_newest = await _make_version(
            db_session, ext, "4.0", datetime(2026, 4, 1, tzinfo=timezone.utc)
        )
        b_old = await _make_build(db_session, repo_dir, v_old, "b_old.tar", package_size=100)
        b_mid = await _make_build(db_session, repo_dir, v_mid, "b_mid.tar", package_size=110)
        b_new = await _make_build(db_session, repo_dir, v_new, "b_new.tar", package_size=120)
        b_newest = await _make_build(
            db_session, repo_dir, v_newest, "b_newest.tar", package_size=130
        )

        # keep=2 → 保留 newest/new，淘汰 mid/old
        with patch.object(ce.settings, "cache_keep_versions", 2):
            result = await ce.evict_old_versions(repo_dir)

        assert result["evicted"] == 2
        assert result["freed_bytes"] == 210  # 100 + 110
        assert result["keep_versions"] == 2
        assert not (repo_dir / "b_old.tar").exists()
        assert not (repo_dir / "b_mid.tar").exists()
        assert (repo_dir / "b_new.tar").exists()
        assert (repo_dir / "b_newest.tar").exists()

        await db_session.refresh(b_old)
        await db_session.refresh(b_mid)
        await db_session.refresh(b_new)
        await db_session.refresh(b_newest)
        assert b_old.cached is False
        assert b_mid.cached is False
        assert b_new.cached is True
        assert b_newest.cached is True


# ─── run_full_eviction ──────────────────────────────────────────


@pytest.mark.unit
class TestRunFullEviction:
    async def test_aggregates_sub_results_in_order(self, tmp_path):
        """mock 三个子函数，验证调用顺序（disk → ttl → old → du）与汇总。"""
        mock_settings = MagicMock()
        mock_settings.repo_dir = tmp_path

        call_order = []

        async def fake_disk(repo_dir):
            call_order.append("disk")
            return {"evicted": 1, "freed_bytes": 100}

        async def fake_ttl(repo_dir):
            call_order.append("ttl")
            return {"evicted": 2, "freed_bytes": 200}

        async def fake_old(repo_dir):
            call_order.append("old")
            return {"evicted": 3, "freed_bytes": 300}

        async def fake_du(repo_dir):
            call_order.append("du")
            return (10, 1000)

        with patch.object(ce, "settings", mock_settings):
            with patch.object(ce, "evict_by_disk_threshold", fake_disk):
                with patch.object(ce, "evict_by_ttl", fake_ttl):
                    with patch.object(ce, "evict_old_versions", fake_old):
                        with patch.object(ce, "get_disk_usage", fake_du):
                            result = await ce.run_full_eviction()

        assert result["disk_threshold"] == {"evicted": 1, "freed_bytes": 100}
        assert result["ttl"] == {"evicted": 2, "freed_bytes": 200}
        assert result["old_versions"] == {"evicted": 3, "freed_bytes": 300}
        assert result["total_evicted"] == 6
        assert result["total_freed_bytes"] == 600
        assert result["final_disk_usage_pct"] == 1.0  # 10/1000*100
        assert call_order == ["disk", "ttl", "old", "du"]
