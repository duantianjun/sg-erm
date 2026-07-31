# -*- coding: utf-8 -*-
"""proxy_engine 集成测试：HIT/MISS/NOT_FOUND + 模式切换 + 白名单绕过。"""
from unittest.mock import Mock

import aiohttp
import pytest
from aioresponses import aioresponses
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import GlobalWhitelist
from app.services.naming import get_package_url
from app.services.proxy_engine import HIT, MISS, NOT_FOUND, ProxyEngine


@pytest.fixture(autouse=True)
def _aiohttp_aioresponses_compat(monkeypatch):
    """aioresponses 0.7.9 与 aiohttp 3.14+ 兼容补丁。

    aiohttp 3.14 给 ClientResponse.__init__ 新增了 stream_writer 必填关键字参数，
    而 aioresponses 0.7.9（当前最新版）构造 mock 响应时未传入该参数，导致
    TypeError: ClientResponse.__init__() missing 1 required keyword-only
    argument: 'stream_writer'。此处包装 __init__ 在缺省时注入 Mock，仅影响
    测试中的 mock 响应构造（mock 响应不会真正执行流式 I/O）。
    """
    original_init = aiohttp.ClientResponse.__init__

    def patched_init(self, *args, **kwargs):
        if "stream_writer" not in kwargs:
            kwargs["stream_writer"] = Mock()
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(aiohttp.ClientResponse, "__init__", patched_init)


def _make_engine(db_engine, test_config):
    """构造测试用 ProxyEngine 实例。"""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    return ProxyEngine(session_factory=factory, config=test_config)


@pytest.mark.integration
class TestHitMissNotFound:
    async def test_hit_returns_cached_file(self, db_engine, db_session, repo_dir, test_config):
        """本地已有文件 → HIT，不发起 HTTP。"""
        engine = _make_engine(db_engine, test_config)
        # 预置缓存文件
        pkg_path = repo_dir / "com.ongres" / "x86_64" / "linux" / "postgis-3.4-pg16.4.tar"
        pkg_path.parent.mkdir(parents=True)
        pkg_path.write_bytes(b"cached content")

        file_path, status = await engine.handle_package_request(
            "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        assert status == HIT
        assert file_path is not None
        assert file_path.exists()

    async def test_miss_fetches_from_upstream_and_caches(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """本地无文件 → 从上游拉取 → MISS。"""
        engine = _make_engine(db_engine, test_config)

        # patch _get_upstream_url 返回固定 URL
        async def fake_get_upstream():
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        # patch _mark_cached 避免数据库副作用（_mark_cached 需要完整 Extension 链）
        async def fake_mark_cached(publisher, arch, os_name, package_name):
            pass
        monkeypatch.setattr(engine, "_mark_cached", fake_mark_cached)

        upstream_url = get_package_url(
            "https://upstream.test/repo", "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        with aioresponses() as m:
            m.get(upstream_url, status=200, body=b"downloaded content")
            file_path, status = await engine.handle_package_request(
                "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
            )

        assert status == MISS
        assert file_path is not None
        assert file_path.exists()
        assert file_path.read_bytes() == b"downloaded content"

    async def test_strict_mode_returns_404_without_fetching(
        self, db_engine, db_session, repo_dir, monkeypatch
    ):
        """strict 模式 + 本地无文件 → NOT_FOUND，不发起 HTTP。"""
        from types import SimpleNamespace
        strict_config = SimpleNamespace(
            repo_dir=repo_dir,
            proxy_mode="strict",
            sync_download_timeout=10,
            sync_concurrency=4,
            upstream_repo_url="https://upstream.test/repo",
        )
        engine = _make_engine(db_engine, strict_config)

        # 验证不调用 _get_upstream_url
        call_count = 0
        async def fake_get_upstream():
            nonlocal call_count
            call_count += 1
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        file_path, status = await engine.handle_package_request(
            "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        assert status == NOT_FOUND
        assert file_path is None
        assert call_count == 0

    async def test_upstream_404_returns_not_found(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """上游返回 404 → NOT_FOUND。"""
        engine = _make_engine(db_engine, test_config)
        async def fake_get_upstream():
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        upstream_url = get_package_url(
            "https://upstream.test/repo", "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        with aioresponses() as m:
            m.get(upstream_url, status=404)
            file_path, status = await engine.handle_package_request(
                "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
            )

        assert status == NOT_FOUND
        assert file_path is None

    async def test_no_upstream_returns_not_found(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """无可用上游 → NOT_FOUND。"""
        engine = _make_engine(db_engine, test_config)
        async def fake_get_upstream():
            return None
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        file_path, status = await engine.handle_package_request(
            "com.ongres", "x86_64", "linux", "postgis-3.4-pg16.4"
        )

        assert status == NOT_FOUND
        assert file_path is None

    async def test_path_traversal_returns_404(
        self, db_engine, db_session, repo_dir, test_config
    ):
        """路径段含 .. → NOT_FOUND（路径遍历防御）。"""
        engine = _make_engine(db_engine, test_config)
        file_path, status = await engine.handle_package_request(
            "..", "x86_64", "linux", "postgis-3.4-pg16.4"
        )
        assert status == NOT_FOUND
        assert file_path is None


@pytest.mark.integration
class TestSimulatedDownloadFlow:
    """模拟下载完整流程测试。

    覆盖场景：
    1. MISS: 首次请求 → 从上游拉取 → 写入缓存
    2. HIT:  再次请求 → 直接读缓存（不发 HTTP）
    3. 缓存文件路径与内容校验
    4. 多源 URL 构造（含空 arch/os 的默认值处理）
    """

    async def test_miss_then_hit_full_workflow(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """MISS → 缓存 → HIT 完整流程。"""
        engine = _make_engine(db_engine, test_config)

        async def fake_get_upstream():
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)

        async def fake_mark_cached(publisher, arch, os_name, package_name):
            pass
        monkeypatch.setattr(engine, "_mark_cached", fake_mark_cached)

        pkg_name = "postgis-3.4-pg16.4"
        upstream_url = get_package_url(
            "https://upstream.test/repo", "com.ongres", "x86_64", "linux", pkg_name
        )
        expected_local = repo_dir / "com.ongres" / "x86_64" / "linux" / f"{pkg_name}.tar"
        assert not expected_local.exists()

        # 1. MISS: 首次请求
        with aioresponses() as m:
            m.get(upstream_url, status=200, body=b"downloaded-payload")
            file_path_1, status_1 = await engine.handle_package_request(
                "com.ongres", "x86_64", "linux", pkg_name
            )

        assert status_1 == MISS
        assert file_path_1 is not None
        assert file_path_1.exists()
        assert file_path_1.read_bytes() == b"downloaded-payload"
        # 验证文件落到预期路径
        assert file_path_1 == expected_local

        # 2. HIT: 再次请求同一包（不应发起 HTTP）
        with aioresponses() as m:
            # 若误发起 HTTP，aioresponses 未注册会抛异常
            file_path_2, status_2 = await engine.handle_package_request(
                "com.ongres", "x86_64", "linux", pkg_name
            )

        assert status_2 == HIT
        assert file_path_2 == expected_local
        assert file_path_2.read_bytes() == b"downloaded-payload"

    async def test_miss_with_empty_arch_uses_default(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """arch 为空字符串 → get_arch 默认值 x86_64，URL 不出现双斜杠。"""
        from app.services.naming import get_arch, get_os, DEFAULT_ARCH, DEFAULT_OS
        assert get_arch("") == DEFAULT_ARCH
        assert get_os("") == DEFAULT_OS

        engine = _make_engine(db_engine, test_config)
        async def fake_get_upstream():
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)
        async def fake_mark_cached(*args):
            pass
        monkeypatch.setattr(engine, "_mark_cached", fake_mark_cached)

        # 即使上游 index.json 返回 arch="", 代理应使用默认值构造 URL
        upstream_url = get_package_url(
            "https://upstream.test/repo",
            "com.ongres",
            get_arch(""),
            get_os(""),
            "adminpack-2.1-pg16.4",
        )
        # 排除 scheme 后的 '://'，路径段不应含 '//'
        path_part = upstream_url.split("://", 1)[1]
        assert "//" not in path_part, f"URL 路径含双斜杠: {upstream_url}"

        with aioresponses() as m:
            m.get(upstream_url, status=200, body=b"adminpack-payload")
            file_path, status = await engine.handle_package_request(
                "com.ongres", get_arch(""), get_os(""), "adminpack-2.1-pg16.4"
            )

        assert status == MISS
        assert file_path.exists()
        assert file_path.read_bytes() == b"adminpack-payload"

    async def test_hit_no_http_call(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """HIT 不发起任何 HTTP 请求（aioresponses 未注册 URL 时调用会抛异常）。"""
        engine = _make_engine(db_engine, test_config)
        pkg_path = repo_dir / "com.ongres" / "x86_64" / "linux" / "pgvector-0.6-pg16.tar"
        pkg_path.parent.mkdir(parents=True, exist_ok=True)
        pkg_path.write_bytes(b"already cached")

        with aioresponses() as m:
            # 不注册任何 URL — 若 engine 发起 HTTP 必抛异常
            file_path, status = await engine.handle_package_request(
                "com.ongres", "x86_64", "linux", "pgvector-0.6-pg16"
            )

        assert status == HIT
        assert file_path == pkg_path


@pytest.mark.integration
class TestWhitelistBypass:
    async def test_proxy_does_not_check_whitelist(
        self, db_engine, db_session, repo_dir, test_config, monkeypatch
    ):
        """代理拉取不查 GlobalWhitelist — 空白名单时仍能 MISS 拉取。"""
        # 确认白名单为空
        result = await db_session.execute(select(GlobalWhitelist))
        assert result.scalars().all() == []

        engine = _make_engine(db_engine, test_config)
        async def fake_get_upstream():
            return "https://upstream.test/repo"
        monkeypatch.setattr(engine, "_get_upstream_url", fake_get_upstream)
        async def fake_mark_cached(*args):
            pass
        monkeypatch.setattr(engine, "_mark_cached", fake_mark_cached)

        upstream_url = get_package_url(
            "https://upstream.test/repo", "any-pub", "x86_64", "linux", "anything-1.0-pg16.4"
        )

        with aioresponses() as m:
            m.get(upstream_url, status=200, body=b"data")
            file_path, status = await engine.handle_package_request(
                "any-pub", "x86_64", "linux", "anything-1.0-pg16.4"
            )

        assert status == MISS
        assert file_path.exists()


from types import SimpleNamespace

import app.database as db_module
import app.main as main_module
from app.services import proxy_engine as pe_module


@pytest.mark.integration
class TestXCacheStatusHeader:
    """通过 FastAPI client 验证 X-Cache-Status 响应头。

    client fixture 已 patch db_module.async_session_factory 为测试 factory，
    因此直接复用即可。全局单例 app.main.proxy_engine 需在测试内替换。
    """

    def _make_test_proxy(self, repo_dir, proxy_mode="hybrid"):
        """构造测试用 ProxyEngine 实例，复用 client fixture 已 patch 的 session_factory。"""
        config = SimpleNamespace(
            repo_dir=repo_dir,
            proxy_mode=proxy_mode,
            sync_download_timeout=10,
            sync_concurrency=4,
            upstream_repo_url="https://upstream.test/repo",
        )
        return pe_module.ProxyEngine(
            session_factory=db_module.async_session_factory,
            config=config,
        )

    async def test_hit_response_has_x_cache_status_header(
        self, client, repo_dir
    ):
        """HIT 响应包含 X-Cache-Status: HIT 头。"""
        test_proxy = self._make_test_proxy(repo_dir, proxy_mode="hybrid")

        # 预置缓存文件
        pkg_path = repo_dir / "com.ongres" / "x86_64" / "linux" / "postgis-3.4-pg16.4.tar"
        pkg_path.parent.mkdir(parents=True)
        pkg_path.write_bytes(b"cached")

        original_proxy = main_module.proxy_engine
        main_module.proxy_engine = test_proxy
        try:
            resp = await client.get(
                "/com.ongres/x86_64/linux/postgis-3.4-pg16.4.tar"
            )
            assert resp.status_code == 200
            assert resp.headers.get("x-cache-status") == "HIT"
        finally:
            main_module.proxy_engine = original_proxy

    async def test_strict_mode_miss_returns_404_without_header(
        self, client, repo_dir
    ):
        """strict 模式未命中 → 404，无 X-Cache-Status 头。"""
        test_proxy = self._make_test_proxy(repo_dir, proxy_mode="strict")

        original_proxy = main_module.proxy_engine
        main_module.proxy_engine = test_proxy
        try:
            resp = await client.get(
                "/com.ongres/x86_64/linux/nonexistent-1.0-pg16.4.tar"
            )
            assert resp.status_code == 404
            assert "x-cache-status" not in resp.headers
        finally:
            main_module.proxy_engine = original_proxy
