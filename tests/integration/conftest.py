# -*- coding: utf-8 -*-
"""集成测试 fixture：内存 SQLite + httpx ASGITransport。

关键点：
1. db_engine/db_session 用 sqlite:///:memory: + StaticPool，每测试一个全新库
2. client fixture 除覆盖 get_db 外，还 patch app.database.async_session_maker
   与 async_session_factory，使审计中间件（直接 from app.database import
   async_session_maker）的 DB 写入也落到测试库
3. 用 httpx.ASGITransport 而非 TestClient，不触发 lifespan
   （绕开 init_db / start_scheduler / start_health_checker / _init_default_admin）
"""
import app.database as db_module
from app.database import Base, get_db
from app.models import *  # noqa: F401,F403  确保所有模型注册到 Base.metadata
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import httpx
import pytest
import pytest_asyncio


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
async def db_session(db_engine):
    """单测试用 AsyncSession，与 client 共享同一内存库。"""
    factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_engine, db_session):
    """API 测试客户端。

    - 覆盖 get_db → 路由用 db_session
    - patch app.database.async_session_factory / async_session_maker → 审计中间件
      写日志用同一内存库（中间件不走依赖注入，直接拿模块级 session_maker）
    """
    test_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    original_factory = db_module.async_session_factory
    original_maker = db_module.async_session_maker
    db_module.async_session_factory = test_factory
    db_module.async_session_maker = test_factory

    from app.main import app

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as c:
        yield c

    app.dependency_overrides.clear()
    db_module.async_session_factory = original_factory
    db_module.async_session_maker = original_maker


# ─── Phase 2 新增 fixture ─────────────────────────────────────
from types import SimpleNamespace


@pytest_asyncio.fixture
def repo_dir(tmp_path):
    """临时仓库根目录，替代 settings.repo_dir。"""
    d = tmp_path / "repo"
    d.mkdir()
    return d


@pytest.fixture
def test_config(repo_dir):
    """测试用配置对象（SimpleNamespace，避免触发 Settings 校验）。

    sync_engine / proxy_engine 构造函数只读 repo_dir / proxy_mode /
    sync_download_timeout / sync_concurrency 字段。
    """
    return SimpleNamespace(
        repo_dir=repo_dir,
        proxy_mode="hybrid",
        sync_download_timeout=10,
        sync_concurrency=4,
        upstream_repo_url="https://upstream.test/repo",
    )


@pytest.fixture
def patch_publish_settings(monkeypatch, repo_dir):
    """monkeypatch publish_service 的全局 settings.repo_dir。

    publish_extension 直接读 settings.repo_dir（未通过参数注入），
    测试时需 patch 到临时目录。
    """
    from app.services import publish_service
    monkeypatch.setattr(publish_service.settings, "repo_dir", repo_dir)
    yield
