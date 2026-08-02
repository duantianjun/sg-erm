# -*- coding: utf-8 -*-
"""ProxyEngine 单元测试。

覆盖：
- handle_package_request: 路径验证 / HIT / strict / hybrid (200/404/网络错误)
- handle_index_request: 本地命中 / 单源上游获取 (成功/失败)
- _get_upstream_url: DB 启用源 / config 回退
- _update_access_time: 有/无匹配记录
- _mark_cached: 已有记录更新 / 无记录新建链路
- _has_multiple_sources: 0/1/2+ 启用源

DB 使用 sqlite:///:memory: + StaticPool，每测试一个全新库；
aiohttp.ClientSession 通过 patch + AsyncMock 模拟整个异步上下文链。
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (  # noqa: F401,F403  注册所有模型到 Base.metadata
    Extension,
    ExtensionBuild,
    ExtensionVersion,
    Publisher,
    RepositorySource,
)
from app.models import *  # noqa: F401,F403
from app.services.naming import INDEX_PATH
from app.services.proxy_engine import ProxyEngine


# ─── 辅助：构造 aiohttp mock ─────────────────────────────────────
class _AsyncChunkIterator:
    """模拟 resp.content.iter_chunked() 返回的异步迭代器。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._iter = None

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _AsyncCtxManager:
    """通用异步上下文管理器：__aenter__ 返回 value, __aexit__ 返回 None。"""

    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return None


def _make_mock_response(status=200, chunks=None, raise_exc=None):
    """构造 mock aiohttp 响应对象。

    Args:
        status: HTTP 状态码
        chunks: 字节块列表（用于 iter_chunked）；None 表示不需要 body
        raise_exc: 若提供，raise_for_status() 抛此异常
    """
    resp = AsyncMock()
    resp.status = status
    if raise_exc is not None:
        resp.raise_for_status = MagicMock(side_effect=raise_exc)
    else:
        resp.raise_for_status = MagicMock()
    if chunks is not None:
        resp.content = MagicMock()
        resp.content.iter_chunked = MagicMock(
            return_value=_AsyncChunkIterator(chunks)
        )
    return resp


def _make_mock_session(response):
    """构造 mock aiohttp.ClientSession，每次 .get() 返回包裹 response 的异步上下文。"""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    def get_fn(url, headers=None):
        return _AsyncCtxManager(response)

    session.get = MagicMock(side_effect=get_fn)
    return session


# ─── 内存 DB fixture ──────────────────────────────────────────────
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


@pytest_asyncio.fixture
async def db_factory(db_engine):
    """基于内存引擎的 session factory。"""
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest.fixture
def test_config(tmp_path):
    """测试用配置对象（SimpleNamespace，避免触发 Settings 校验）。"""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    return SimpleNamespace(
        repo_dir=repo_dir,
        proxy_mode="hybrid",
        sync_download_timeout=10,
        io_chunk_size=8192,
        temp_file_suffix=".tmp",
        upstream_repo_url="https://upstream.test/repo",
    )


@pytest.fixture
def engine(db_factory, test_config):
    """ProxyEngine 实例（注入 test session_factory 与 test config）。"""
    return ProxyEngine(session_factory=db_factory, config=test_config)


# ─── 测试常量 ─────────────────────────────────────────────────────
PUBLISHER = "com.ongres"
ARCH = "x86_64"
OS_NAME = "linux"
PKG_NAME = "postgis-3.4-pg16.4"
REL_PATH = f"{PUBLISHER}/{ARCH}/{OS_NAME}/{PKG_NAME}.tar"


async def _insert_build_chain(session, *, cached=True, last_accessed=None):
    """插入完整 Publisher→Extension→Version→Build 链路，返回 ExtensionBuild。"""
    pub = Publisher(name=PUBLISHER, display_name=PUBLISHER)
    session.add(pub)
    await session.flush()
    ext = Extension(name="postgis", publisher_id=pub.id)
    session.add(ext)
    await session.flush()
    ver = ExtensionVersion(
        extension_id=ext.id, version="3.4", channel="stable"
    )
    session.add(ver)
    await session.flush()
    build = ExtensionBuild(
        version_id=ver.id,
        postgres_version="16.4",
        arch=ARCH,
        os=OS_NAME,
        flavor="pg",
        build=None,
        package_path=REL_PATH,
        package_size=None,
        cached=cached,
        last_accessed=last_accessed,
    )
    session.add(build)
    await session.commit()
    return build


# ─── 1. TestHandlePackageRequest ─────────────────────────────────
@pytest.mark.unit
class TestHandlePackageRequest:
    async def test_invalid_path_returns_404(self, engine):
        """含 ../ 的路径段 → (None, "404")。"""
        path, status = await engine.handle_package_request(
            "../etc", ARCH, OS_NAME, PKG_NAME
        )
        assert path is None
        assert status == "404"

    async def test_local_cache_hit(self, engine, test_config, db_factory):
        """本地有缓存文件 → (path, "HIT") 并更新 last_accessed。"""
        local_path = test_config.repo_dir / REL_PATH
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"cached-content")

        async with db_factory() as session:
            await _insert_build_chain(session, cached=True, last_accessed=None)

        path, status = await engine.handle_package_request(
            PUBLISHER, ARCH, OS_NAME, PKG_NAME
        )
        assert status == "HIT"
        assert path == local_path

        async with db_factory() as session:
            result = await session.execute(
                select(ExtensionBuild).where(
                    ExtensionBuild.package_path == REL_PATH
                )
            )
            b = result.scalar_one()
            assert b.last_accessed is not None

    async def test_strict_mode_no_cache(self, db_factory, tmp_path):
        """strict 模式 + 未命中 → (None, "404")。"""
        cfg = SimpleNamespace(
            repo_dir=tmp_path / "repo",
            proxy_mode="strict",
            sync_download_timeout=10,
            io_chunk_size=8192,
            temp_file_suffix=".tmp",
            upstream_repo_url="https://upstream.test/repo",
        )
        strict_engine = ProxyEngine(session_factory=db_factory, config=cfg)

        path, status = await strict_engine.handle_package_request(
            PUBLISHER, ARCH, OS_NAME, PKG_NAME
        )
        assert path is None
        assert status == "404"

    async def test_hybrid_upstream_404(self, engine):
        """hybrid 模式 + 上游 404 → (None, "404")。"""
        mock_resp = _make_mock_response(status=404)
        mock_session = _make_mock_session(mock_resp)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            path, status = await engine.handle_package_request(
                PUBLISHER, ARCH, OS_NAME, PKG_NAME
            )
        assert path is None
        assert status == "404"

    async def test_hybrid_upstream_200(self, engine, test_config, db_factory):
        """hybrid 模式 + 上游 200 → (path, "MISS")，文件已创建，DB 已标记。"""
        mock_resp = _make_mock_response(status=200, chunks=[b"hello", b"-world"])
        mock_session = _make_mock_session(mock_resp)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            path, status = await engine.handle_package_request(
                PUBLISHER, ARCH, OS_NAME, PKG_NAME
            )
        assert status == "MISS"
        assert path is not None
        assert path.exists()
        assert path.read_bytes() == b"hello-world"

        # 验证 _mark_cached 被调用：DB 中存在 ExtensionBuild 记录，cached=True
        async with db_factory() as session:
            result = await session.execute(
                select(ExtensionBuild).where(
                    ExtensionBuild.package_path == REL_PATH
                )
            )
            b = result.scalar_one()
            assert b.cached is True
            assert b.last_accessed is not None

    async def test_hybrid_network_error(self, engine):
        """hybrid 模式 + 网络异常 → (None, "404")。"""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(
            side_effect=aiohttp.ClientError("network error")
        )
        mock_session.__aexit__ = AsyncMock(return_value=None)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            path, status = await engine.handle_package_request(
                PUBLISHER, ARCH, OS_NAME, PKG_NAME
            )
        assert path is None
        assert status == "404"


# ─── 2. TestHandleIndexRequest ───────────────────────────────────
@pytest.mark.unit
class TestHandleIndexRequest:
    async def test_local_index_hit(self, engine, test_config):
        """本地已有 index.json → 返回路径。"""
        index_path = test_config.repo_dir / INDEX_PATH
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("{}", encoding="utf-8")

        result = await engine.handle_index_request()
        assert result == index_path

    async def test_single_source_upstream_success(self, engine, test_config):
        """单源 + 上游获取成功 → 返回路径并写入文件。"""
        mock_resp = _make_mock_response(
            status=200, chunks=[b'{"extensions":[]}']
        )
        mock_session = _make_mock_session(mock_resp)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await engine.handle_index_request()
        assert result is not None
        assert result.exists()
        assert result.read_bytes() == b'{"extensions":[]}'

    async def test_single_source_upstream_failure(self, engine):
        """单源 + 上游获取失败 → None。"""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(
            side_effect=aiohttp.ClientError("network error")
        )
        mock_session.__aexit__ = AsyncMock(return_value=None)
        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await engine.handle_index_request()
        assert result is None


# ─── 3. TestGetUpstreamUrl ───────────────────────────────────────
@pytest.mark.unit
class TestGetUpstreamUrl:
    async def test_db_has_enabled_source(self, engine, db_factory):
        """DB 有启用源 → 返回优先级最高的源 URL（数字小=优先级高）。"""
        async with db_factory() as session:
            session.add(RepositorySource(
                id="s1", name="src1", url="https://a.test/repo",
                priority=10, enabled=True,
            ))
            session.add(RepositorySource(
                id="s2", name="src2", url="https://b.test/repo",
                priority=20, enabled=True,
            ))
            await session.commit()

        url = await engine._get_upstream_url()
        assert url == "https://a.test/repo"

    async def test_no_source_fallback_to_config(self, engine, test_config):
        """DB 无启用源 → 回退到 config.upstream_repo_url。"""
        url = await engine._get_upstream_url()
        assert url == test_config.upstream_repo_url


# ─── 4. TestUpdateAccessTime ─────────────────────────────────────
@pytest.mark.unit
class TestUpdateAccessTime:
    async def test_update_existing_build(self, engine, db_factory):
        """有匹配的 ExtensionBuild → last_accessed 被更新。"""
        old_time = datetime(2020, 1, 1)
        async with db_factory() as session:
            await _insert_build_chain(
                session, cached=True, last_accessed=old_time
            )

        await engine._update_access_time(PUBLISHER, ARCH, OS_NAME, PKG_NAME)

        async with db_factory() as session:
            result = await session.execute(
                select(ExtensionBuild).where(
                    ExtensionBuild.package_path == REL_PATH
                )
            )
            b = result.scalar_one()
            assert b.last_accessed is not None
            assert b.last_accessed > old_time

    async def test_no_matching_build_no_error(self, engine):
        """无匹配记录 → 不报错。"""
        # 不应抛异常
        await engine._update_access_time(PUBLISHER, ARCH, OS_NAME, PKG_NAME)


# ─── 5. TestMarkCached ───────────────────────────────────────────
@pytest.mark.unit
class TestMarkCached:
    async def test_update_existing_build(self, engine, db_factory, test_config):
        """已有记录 → cached=True, last_accessed 被更新。"""
        old_time = datetime(2020, 1, 1)
        async with db_factory() as session:
            await _insert_build_chain(
                session, cached=False, last_accessed=old_time
            )

        # 创建本地文件让 package_size 可被填充
        local_path = test_config.repo_dir / REL_PATH
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"data")

        await engine._mark_cached(PUBLISHER, ARCH, OS_NAME, PKG_NAME)

        async with db_factory() as session:
            result = await session.execute(
                select(ExtensionBuild).where(
                    ExtensionBuild.package_path == REL_PATH
                )
            )
            b = result.scalar_one()
            assert b.cached is True
            assert b.last_accessed > old_time

    async def test_create_new_chain(self, engine, db_factory, test_config):
        """无记录 → 创建完整 Publisher→Extension→Version→Build 链路。"""
        local_path = test_config.repo_dir / REL_PATH
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"package-data")

        await engine._mark_cached(PUBLISHER, ARCH, OS_NAME, PKG_NAME)

        async with db_factory() as session:
            pub_result = await session.execute(
                select(Publisher).where(Publisher.name == PUBLISHER)
            )
            pub = pub_result.scalar_one()
            assert pub is not None

            ext_result = await session.execute(
                select(Extension).where(Extension.name == "postgis")
            )
            ext = ext_result.scalar_one()
            assert ext.publisher_id == pub.id

            ver_result = await session.execute(
                select(ExtensionVersion).where(
                    ExtensionVersion.extension_id == ext.id,
                    ExtensionVersion.version == "3.4",
                )
            )
            ver = ver_result.scalar_one()
            assert ver.channel == "stable"

            build_result = await session.execute(
                select(ExtensionBuild).where(
                    ExtensionBuild.package_path == REL_PATH
                )
            )
            build = build_result.scalar_one()
            assert build.version_id == ver.id
            assert build.cached is True
            assert build.postgres_version == "16.4"
            assert build.arch == ARCH
            assert build.os == OS_NAME
            assert build.flavor == "pg"
            assert build.build is None
            assert build.package_size == len(b"package-data")
            assert build.last_accessed is not None


# ─── 6. TestHasMultipleSources ───────────────────────────────────
@pytest.mark.unit
class TestHasMultipleSources:
    async def test_zero_sources(self, engine):
        """0 个启用源 → False。"""
        assert await engine._has_multiple_sources() is False

    async def test_one_source(self, engine, db_factory):
        """1 个启用源 → False。"""
        async with db_factory() as session:
            session.add(RepositorySource(
                id="s1", name="src1", url="https://a.test/repo",
                priority=10, enabled=True,
            ))
            await session.commit()

        assert await engine._has_multiple_sources() is False

    async def test_two_or_more_sources(self, engine, db_factory):
        """2+ 个启用源 → True。"""
        async with db_factory() as session:
            session.add(RepositorySource(
                id="s1", name="src1", url="https://a.test/repo",
                priority=10, enabled=True,
            ))
            session.add(RepositorySource(
                id="s2", name="src2", url="https://b.test/repo",
                priority=20, enabled=True,
            ))
            await session.commit()

        assert await engine._has_multiple_sources() is True
